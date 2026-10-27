import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/features/play/airplay.dart';
import 'package:iris/features/play/cast.dart';
import 'package:iris/features/play/dlna.dart';

String plist(Map<String, String> entries) =>
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<plist version="1.0"><dict>'
    '${entries.entries.map((e) => '<key>${e.key}</key>${e.value}').join()}'
    '</dict></plist>';

class _Fake implements CastDiscovery {
  _Fake(this.devices, {this.error});

  final List<CastDevice> devices;
  final Object? error;

  @override
  Stream<CastDevice> search({Duration timeout = const Duration(seconds: 5)}) =>
      Stream.multi((c) {
        if (error != null) c.addError(error!);
        devices.forEach(c.add);
        c.close();
      });
}

CastDevice device(String id) => CastDevice(
  id: id,
  name: id,
  controlUrl: Uri.parse('http://10.0.0.2/'),
  serviceType: 'x',
);

void main() {
  group('discovery', () {
    test('a TV record becomes an AirPlay device', () {
      final tv = airPlayDevice(
        instance: '客厅的小米电视._airplay._tcp.local',
        address: '192.168.1.20',
        port: 7000,
        txt: 'deviceid=AA:BB:CC:DD:EE:FF\nfeatures=0x5A7FFFF7,0x1E\nmodel=MiTV',
      );
      expect(tv, isNotNull);
      expect(tv!.protocol, CastProtocol.airplay);
      expect(tv.name, '客厅的小米电视');
      expect(tv.id, 'airplay:AA:BB:CC:DD:EE:FF');
      expect(tv.controlUrl.toString(), 'http://192.168.1.20:7000');
    });

    test('speakers without video are left out', () {
      expect(
        airPlayDevice(
          instance: 'HomePod._airplay._tcp.local',
          address: '192.168.1.30',
          port: 7000,
          txt: 'features=0x4A7FCA00,0xBC354BD0',
        ),
        isNull,
      );
      expect(airPlaySupportsVideo('0x1'), isTrue);
      expect(airPlaySupportsVideo('0x10'), isTrue);
      expect(airPlaySupportsVideo(null), isTrue);
    });

    test(
      'searches of every protocol are merged, one failing is fine',
      () async {
        final merged = CompositeDiscovery([
          _Fake([device('a')]),
          _Fake([device('b')], error: 'boom'),
        ]);
        expect((await merged.search().toList()).map((d) => d.id), ['a', 'b']);
      },
    );

    test('an error is reported only when every search failed', () async {
      final merged = CompositeDiscovery([
        _Fake(const [], error: 'one'),
        _Fake(const [], error: 'two'),
      ]);
      await expectLater(merged.search(), emitsError('two'));
    });
  });

  group('status', () {
    test('plist values are read', () {
      final info = parsePlist(
        plist({
          'duration': '<real>120.5</real>',
          'position': '<real>10</real>',
          'rate': '<real>1</real>',
          'readyToPlay': '<true/>',
        }),
      );
      expect(info['duration'], 120.5);
      expect(info['readyToPlay'], true);
      expect(parsePlist('not xml'), isEmpty);
    });

    test('rate and duration decide the state', () {
      expect(
        airPlayStatus({'rate': 1.0, 'duration': 100.0, 'position': 5.0}).state,
        CastState.playing,
      );
      expect(
        airPlayStatus({'rate': 0.0, 'duration': 100.0, 'position': 5.0}).state,
        CastState.paused,
      );
      expect(
        airPlayStatus({'rate': 0.0, 'duration': 100.0, 'position': 100.0})
            .state,
        CastState.stopped,
      );
      expect(airPlayStatus(const {}).state, CastState.stopped);
      final status = airPlayStatus({
        'rate': 1.0,
        'duration': 90.0,
        'position': 12.5,
      });
      expect(status.position, const Duration(milliseconds: 12500));
      expect(status.duration, const Duration(seconds: 90));
    });
  });

  group('renderer', () {
    late HttpServer server;
    late List<({String method, String path, String body, String? type})> calls;
    late (int, String) Function(String path) reply;
    late AirPlayRenderer renderer;
    var delay = Duration.zero;

    setUp(() async {
      calls = [];
      reply = (_) => (200, '');
      delay = Duration.zero;
      server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      server.listen((request) async {
        final body = await utf8.decodeStream(request);
        calls.add((
          method: request.method,
          path: request.uri.toString(),
          body: body,
          type: request.headers.contentType?.mimeType,
        ));
        final (status, text) = reply(request.uri.path);
        await Future<void>.delayed(delay);
        request.response
          ..statusCode = status
          ..write(text);
        await request.response.close();
      });
      renderer = AirPlayRenderer(
        CastDevice(
          id: 'airplay:tv',
          name: '电视',
          controlUrl: Uri.parse('http://127.0.0.1:${server.port}'),
          serviceType: 'airplay',
          protocol: CastProtocol.airplay,
        ),
        Dio(),
      );
    });

    tearDown(() => server.close(force: true));

    test('loading pushes the address and starts playing', () async {
      await renderer.load('http://nas/play/t/1.mkv', '片名');
      await renderer.play(); // 推上去就开始放，不再多发
      expect(calls, hasLength(1));
      expect(calls.single.method, 'POST');
      expect(calls.single.path, '/play');
      expect(calls.single.type, 'text/parameters');
      expect(
        calls.single.body,
        contains('Content-Location: http://nas/play/t/1.mkv'),
      );
    });

    test('controls map to rate, scrub and stop', () async {
      await renderer.pause();
      await renderer.seek(const Duration(minutes: 1, seconds: 30));
      await renderer.stop();
      expect(calls.map((c) => c.path), [
        '/rate?value=0.000000',
        '/scrub?position=90.000',
        '/stop',
      ]);
    });

    test('playback info becomes a status', () async {
      reply = (_) => (
        200,
        plist({
          'duration': '<real>60</real>',
          'position': '<real>3</real>',
          'rate': '<real>1</real>',
        }),
      );
      final status = await renderer.status();
      expect(status.state, CastState.playing);
      expect(status.duration, const Duration(seconds: 60));
    });

    test('a refusal is a fault', () async {
      reply = (_) => (403, '');
      await expectLater(
        renderer.pause(),
        throwsA(isA<CastException>().having((e) => e.fault, 'fault', true)),
      );
    });

    test('loading waits for a TV that answers only once it is ready', () async {
      delay = const Duration(milliseconds: 400);
      final impatient = AirPlayRenderer(
        renderer.device,
        Dio(BaseOptions(receiveTimeout: const Duration(milliseconds: 100))),
      );
      await impatient.load('http://nas/play/t/1.mp4', '片名');
      await expectLater(
        impatient.pause(),
        throwsA(
          isA<CastException>().having(
            (e) => e.message,
            'message',
            contains('没有及时回应'),
          ),
        ),
      );
    });
  });
}
