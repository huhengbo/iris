import 'dart:convert';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/features/play/dlna.dart';
import 'package:xml/xml.dart';

const avTransport = 'urn:schemas-upnp-org:service:AVTransport:1';

/// 小米电视这类设备的描述：渲染器在内嵌设备里，控制地址是相对的。
const xiaomiDescription = '''<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
    <friendlyName>客厅的小米电视</friendlyName>
    <UDN>uuid:tv-1</UDN>
    <serviceList>
      <service>
        <serviceType>urn:schemas-upnp-org:service:RenderingControl:1</serviceType>
        <controlURL>/RenderingControl/control</controlURL>
      </service>
    </serviceList>
    <deviceList>
      <device>
        <friendlyName>内嵌渲染器</friendlyName>
        <UDN>uuid:inner</UDN>
        <serviceList>
          <service>
            <serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>
            <controlURL>AVTransport/control</controlURL>
          </service>
        </serviceList>
      </device>
    </deviceList>
  </device>
</root>''';

String soapResponse(String action, Map<String, String> args) =>
    '<?xml version="1.0"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><u:${action}Response xmlns:u="$avTransport">'
    '${args.entries.map((e) => '<${e.key}>${e.value}</${e.key}>').join()}'
    '</u:${action}Response></s:Body></s:Envelope>';

const faultResponse =
    '<?xml version="1.0"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    '<s:Body><s:Fault><faultcode>s:Client</faultcode>'
    '<faultstring>UPnPError</faultstring><detail>'
    '<UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
    '<errorCode>714</errorCode><errorDescription>Illegal MIME-type'
    '</errorDescription></UPnPError></detail></s:Fault></s:Body></s:Envelope>';

void main() {
  group('discovery', () {
    test('search asks every device of a type to answer', () {
      final message = searchMessage(avTransport);
      expect(message, startsWith('M-SEARCH * HTTP/1.1\r\n'));
      expect(message, contains('HOST: 239.255.255.250:1900\r\n'));
      expect(message, contains('MAN: "ssdp:discover"\r\n'));
      expect(message, contains('ST: $avTransport\r\n'));
      expect(message, endsWith('\r\n\r\n'));
    });

    test('answers give the description address', () {
      expect(
        parseSsdpLocation(
          'HTTP/1.1 200 OK\r\n'
          'CACHE-CONTROL: max-age=1800\r\n'
          'location: http://192.168.1.20:49152/description.xml\r\n'
          'ST: $avTransport\r\n\r\n',
        ),
        'http://192.168.1.20:49152/description.xml',
      );
      // 别的设备的广播和不带地址的应答都不算
      expect(
        parseSsdpLocation(
          'NOTIFY * HTTP/1.1\r\nLOCATION: http://192.168.1.30/d.xml\r\n\r\n',
        ),
        isNull,
      );
      expect(parseSsdpLocation('HTTP/1.1 200 OK\r\nST: x\r\n\r\n'), isNull);
      expect(parseSsdpLocation(''), isNull);
    });

    test('a description names the TV and where to control it', () {
      final location = Uri.parse('http://192.168.1.20:49152/dev/desc.xml');
      final device = parseDeviceDescription(xiaomiDescription, location)!;
      // 名字取外层设备的，控制地址相对描述地址解析
      expect(device.id, 'uuid:tv-1');
      expect(device.name, '客厅的小米电视');
      expect(device.serviceType, avTransport);
      expect(
        device.controlUrl,
        Uri.parse('http://192.168.1.20:49152/dev/AVTransport/control'),
      );
    });

    test('URLBase wins over the description address', () {
      final body = xiaomiDescription.replaceFirst(
        '<specVersion>',
        '<URLBase>http://192.168.1.21:8080/</URLBase><specVersion>',
      );
      final device = parseDeviceDescription(
        body,
        Uri.parse('http://192.168.1.20:49152/dev/desc.xml'),
      )!;
      expect(
        device.controlUrl,
        Uri.parse('http://192.168.1.21:8080/AVTransport/control'),
      );
    });

    test('devices that cannot play video are left out', () {
      final location = Uri.parse('http://192.168.1.40/desc.xml');
      // 只有音量控制的音箱
      final speaker = xiaomiDescription.replaceAll(
        'AVTransport:1',
        'ConnectionManager:1',
      );
      expect(parseDeviceDescription(speaker, location), isNull);
      expect(parseDeviceDescription('<html>路由器后台</html>', location), isNull);
      expect(parseDeviceDescription('不是 XML', location), isNull);
    });
  });

  group('audio-only devices', () {
    late HttpServer server;
    late String sink;
    late int cmStatus;
    late List<String> bodies;

    String description() => '''<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <device>
    <friendlyName>客厅</friendlyName>
    <UDN>uuid:room-1</UDN>
    <serviceList>
      <service>
        <serviceType>urn:schemas-upnp-org:service:ConnectionManager:1</serviceType>
        <controlURL>/cm/control</controlURL>
      </service>
      <service>
        <serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>
        <controlURL>/av/control</controlURL>
      </service>
    </serviceList>
  </device>
</root>''';

    setUp(() async {
      sink = '';
      cmStatus = 200;
      bodies = [];
      server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      server.listen((request) async {
        final body = await utf8.decodeStream(request);
        if (request.uri.path == '/cm/control') {
          bodies.add(body);
          request.response
            ..statusCode = cmStatus
            ..write(
              '<?xml version="1.0"?>'
              '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
              '<s:Body><u:GetProtocolInfoResponse '
              'xmlns:u="urn:schemas-upnp-org:service:ConnectionManager:1">'
              '<Source></Source><Sink>$sink</Sink>'
              '</u:GetProtocolInfoResponse></s:Body></s:Envelope>',
            );
        } else {
          request.response.write(description());
        }
        await request.response.close();
      });
    });

    tearDown(() => server.close(force: true));

    Future<CastDevice?> find() => fetchDevice(
      Dio(),
      Uri.parse('http://127.0.0.1:${server.port}/desc.xml'),
    );

    test('a device that takes video is listed', () async {
      sink = 'http-get:*:audio/mpeg:*,http-get:*:video/mp4:*';
      expect((await find())?.name, '客厅');
      // GetProtocolInfo 没有 InstanceID 参数
      expect(bodies.single, isNot(contains('InstanceID')));
    });

    test('a speaker that takes only audio is left out', () async {
      sink = 'http-get:*:audio/mpeg:*,http-get:*:audio/x-flac:*';
      expect(await find(), isNull);
    });

    test('a device that cannot say what it plays is kept', () async {
      cmStatus = 500;
      expect((await find())?.name, '客厅');
      cmStatus = 200;
      sink = '';
      expect((await find())?.name, '客厅');
    });
  });

  group('SOAP', () {
    test('arguments are escaped inside the action', () {
      final body = soapEnvelope(avTransport, 'SetAVTransportURI', {
        'CurrentURI': 'https://m.example.com/play/t?a=1&b=2',
      });
      final action = XmlDocument.parse(body)
          .findAllElements('SetAVTransportURI', namespaceUri: '*')
          .single;
      expect(action.namespaceUri, avTransport);
      expect(action.getElement('InstanceID')!.innerText, '0');
      expect(
        action.getElement('CurrentURI')!.innerText,
        'https://m.example.com/play/t?a=1&b=2',
      );
    });

    test('results and faults are read from the reply', () {
      expect(
        soapResult(
          soapResponse('GetPositionInfo', {'Track': '1', 'RelTime': '0:12:34'}),
          'GetPositionInfo',
        ),
        {'Track': '1', 'RelTime': '0:12:34'},
      );
      expect(soapResult('坏掉的应答', 'Play'), isEmpty);
      expect(soapFault(faultResponse), '714 Illegal MIME-type');
      expect(soapFault(soapResponse('Play', {})), isNull);
    });

    test('metadata tells the TV the title and the format', () {
      final didl = XmlDocument.parse(
        didlMetadata(
          'https://m.example.com/play/t/流浪地球.MKV?x=1',
          '流浪地球 <2019>',
        ),
      );
      expect(
        didl.findAllElements('title', namespaceUri: '*').single.innerText,
        '流浪地球 <2019>',
      );
      final res = didl.findAllElements('res', namespaceUri: '*').single;
      expect(res.getAttribute('protocolInfo'), 'http-get:*:video/x-matroska:*');
      expect(
        didlMetadata('https://m.example.com/play/t/movie', 'x'),
        contains('http-get:*:*:*'),
      );
      expect(
        didlMetadata('https://m.example.com/play/t/movie.mp4', 'x'),
        contains('video/mp4'),
      );
    });

    test('UPnP times', () {
      expect(
        parseUpnpTime('1:02:03'),
        const Duration(hours: 1, minutes: 2, seconds: 3),
      );
      expect(
        parseUpnpTime('00:00:05.500'),
        const Duration(seconds: 5, milliseconds: 500),
      );
      expect(parseUpnpTime('NOT_IMPLEMENTED'), isNull);
      expect(parseUpnpTime(null), isNull);
      expect(formatUpnpTime(const Duration(seconds: 754)), '00:12:34');
      expect(formatUpnpTime(const Duration(hours: 2, seconds: 1)), '02:00:01');
      expect(formatUpnpTime(const Duration(seconds: -3)), '00:00:00');
    });
  });

  group('renderer', () {
    late HttpServer server;
    late List<({String action, String body, bool persistent})> calls;
    late Map<String, (int, String)> replies;
    late (int, String) Function(String action, String body) reply;
    late DlnaRenderer renderer;
    var delay = Duration.zero;

    setUp(() async {
      calls = [];
      replies = {};
      delay = Duration.zero;
      reply = (action, _) => replies[action] ?? (200, soapResponse(action, {}));
      server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      server.listen((request) async {
        final body = await utf8.decodeStream(request);
        final soapAction = request.headers.value('soapaction') ?? '';
        final action = soapAction.replaceAll('"', '').split('#').last;
        calls.add((
          action: soapAction,
          body: body,
          persistent: request.persistentConnection,
        ));
        final (status, text) = reply(action, body);
        await Future<void>.delayed(delay);
        request.response
          ..statusCode = status
          ..headers.contentType = ContentType('text', 'xml', charset: 'utf-8')
          ..write(text);
        await request.response.close();
      });
      renderer = DlnaRenderer(
        CastDevice(
          id: 'uuid:tv-1',
          name: '客厅的小米电视',
          controlUrl: Uri.parse(
            'http://127.0.0.1:${server.port}/AVTransport/control',
          ),
          serviceType: avTransport,
        ),
        Dio(),
      );
    });

    tearDown(() => server.close(force: true));

    test('controls are SOAP actions on AVTransport', () async {
      await renderer.play();
      await renderer.pause();
      await renderer.seek(const Duration(minutes: 12, seconds: 34));
      await renderer.stop();
      expect(calls.map((c) => c.action), [
        '"$avTransport#Play"',
        '"$avTransport#Pause"',
        '"$avTransport#Seek"',
        '"$avTransport#Stop"',
      ]);
      expect(calls[0].body, contains('<Speed>1</Speed>'));
      expect(calls[2].body, contains('<Unit>REL_TIME</Unit>'));
      expect(calls[2].body, contains('<Target>00:12:34</Target>'));
    });

    test('loading retries without metadata when the TV refuses it', () async {
      // 只认不带描述的
      reply = (action, body) => body.contains('DIDL-Lite')
          ? (500, faultResponse)
          : (200, soapResponse(action, {}));
      await renderer.load('https://m.example.com/play/t/movie.mkv', '流浪地球');
      expect(calls, hasLength(2));
      expect(calls[0].body, contains('DIDL-Lite'));
      expect(
        calls[1].body,
        contains('<CurrentURIMetaData></CurrentURIMetaData>'),
      );

      // 两次都拒绝，把电视给的原因告诉用户
      calls.clear();
      reply = (_, _) => (500, faultResponse);
      await expectLater(
        renderer.load('https://m.example.com/play/t/movie.mkv', '流浪地球'),
        throwsA(
          isA<CastException>().having(
            (e) => e.message,
            'message',
            '电视拒绝了操作（714 Illegal MIME-type）',
          ),
        ),
      );
      expect(calls, hasLength(2));
    });

    test('status combines transport state and position', () async {
      replies['GetTransportInfo'] = (
        200,
        soapResponse('GetTransportInfo', {
          'CurrentTransportState': 'PAUSED_PLAYBACK',
          'CurrentTransportStatus': 'OK',
        }),
      );
      replies['GetPositionInfo'] = (
        200,
        soapResponse('GetPositionInfo', {
          'TrackDuration': '2:00:00',
          'RelTime': '0:12:34',
        }),
      );
      final status = await renderer.status();
      expect(status.state, CastState.paused);
      expect(status.position, const Duration(minutes: 12, seconds: 34));
      expect(status.duration, const Duration(hours: 2));

      // 还没读到片长的电视给 0:00:00
      replies['GetTransportInfo'] = (
        200,
        soapResponse('GetTransportInfo', {
          'CurrentTransportState': 'TRANSITIONING',
        }),
      );
      replies['GetPositionInfo'] = (
        200,
        soapResponse('GetPositionInfo', {
          'TrackDuration': '0:00:00',
          'RelTime': 'NOT_IMPLEMENTED',
        }),
      );
      final loading = await renderer.status();
      expect(loading.state, CastState.connecting);
      expect(loading.position, isNull);
      expect(loading.duration, isNull);
    });

    test('an unreachable TV says to check the Wi-Fi', () async {
      await server.close(force: true);
      await expectLater(
        renderer.play(),
        throwsA(
          isA<CastException>()
              .having((e) => e.fault, 'fault', isFalse)
              .having((e) => e.message, 'message', contains('同一个 Wi-Fi')),
        ),
      );
    });

    test('loading waits longer than ordinary actions for the TV', () async {
      // 电视先去拉流再应答，比普通动作等得久；等不及会把已经在放的投屏当成失败
      delay = const Duration(milliseconds: 400);
      final impatient = DlnaRenderer(
        renderer.device,
        Dio(BaseOptions(receiveTimeout: const Duration(milliseconds: 100))),
      );
      await impatient.load('http://nas/a.mp4', '片名');
      await expectLater(
        impatient.pause(),
        throwsA(
          isA<CastException>()
              .having((e) => e.fault, 'fault', isFalse)
              .having((e) => e.message, 'message', contains('没有及时回应')),
        ),
      );
    });

    test('play is retried while the TV is still loading', () async {
      var refusals = 2;
      reply = (action, _) => action == 'Play' && refusals-- > 0
          ? (500, faultResponse)
          : (200, soapResponse(action, {}));
      final patient = DlnaRenderer(
        renderer.device,
        Dio(),
        playRetryDelay: Duration.zero,
      );
      await patient.play();
      expect(calls.where((c) => c.action.contains('#Play')), hasLength(3));
    });

    test('play gives up when the TV keeps refusing', () async {
      reply = (action, _) => (500, faultResponse);
      final patient = DlnaRenderer(
        renderer.device,
        Dio(),
        playRetryDelay: Duration.zero,
      );
      await expectLater(
        patient.play(),
        throwsA(isA<CastException>().having((e) => e.fault, 'fault', isTrue)),
      );
      expect(calls, hasLength(4));
    });

    test('every action uses a fresh connection', () async {
      // 复用旧连接时，电视已经关掉的空闲连接会让下一个动作失败
      await renderer.load('http://nas/a.mp4', '片名');
      await renderer.play();
      await renderer.status();
      expect(calls, isNotEmpty);
      expect(calls.every((c) => !c.persistent), isTrue);
    });
  });
}
