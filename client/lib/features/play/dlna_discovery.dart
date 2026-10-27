part of 'dlna.dart';

// ---- SSDP 搜索 ----

const _ssdpAddress = '239.255.255.250';
const _ssdpPort = 1900;
// 有的电视只应答 ssdp:all 或 rootdevice，或者只认 :2 版本；多问几种，按 LOCATION 去重
const _searchTargets = [
  'urn:schemas-upnp-org:device:MediaRenderer:1',
  'urn:schemas-upnp-org:service:AVTransport:1',
  'urn:schemas-upnp-org:device:MediaRenderer:2',
  'upnp:rootdevice',
  'ssdp:all',
];

/// 手机连着局域网的网卡，排除蜂窝、VPN 隧道和虚拟网卡（上面没有电视）。
/// 搜索包要从 Wi-Fi 发出去：绑到 anyIPv4 时组播走默认路由，开了移动数据双通道或 VPN
/// 的手机会从蜂窝或隧道网卡发出，电视收不到。绑定到网卡地址后内核按源地址选出口。
Future<List<NetworkInterface>> lanInterfaces() async {
  try {
    final interfaces = await NetworkInterface.list(
      type: InternetAddressType.IPv4,
    );
    return [
      for (final interface in interfaces)
        if (!const [
              'rmnet',
              'ccmni',
              'tun',
              'ppp',
              'dummy',
              'lo',
            ].any(interface.name.toLowerCase().startsWith) &&
            interface.addresses.any((a) => !a.isLoopback && !a.isLinkLocal))
          interface,
    ];
  } on Object {
    return const [];
  }
}

Future<List<InternetAddress>> _localAddresses() async => [
  for (final interface in await lanInterfaces())
    for (final address in interface.addresses)
      if (!address.isLoopback && !address.isLinkLocal) address,
];

class SsdpDiscovery implements CastDiscovery {
  SsdpDiscovery(this._dio);

  final Dio _dio;

  @override
  Stream<CastDevice> search({Duration timeout = const Duration(seconds: 5)}) {
    final controller = StreamController<CastDevice>();
    final seenLocations = <String>{};
    final seenDevices = <String>{};
    final pending = <Future<void>>[];
    final sockets = <RawDatagramSocket>[];
    final timers = <Timer>[];
    var finished = false;

    // 到时间或者调用方不再收听时结束，两者都可能发生
    Future<void> finish() async {
      if (finished) return;
      finished = true;
      for (final t in timers) {
        t.cancel();
      }
      for (final socket in sockets) {
        socket.close();
      }
      await Future.wait(pending);
      await holdMulticastLock(false);
      if (!controller.isClosed) await controller.close();
    }

    Future<void> describe(String location) async {
      try {
        final device = await fetchDevice(_dio, Uri.parse(location));
        if (device != null &&
            seenDevices.add(device.id) &&
            !controller.isClosed) {
          controller.add(device);
        }
      } on Object catch (e) {
        // 应答的不是渲染器或描述读不出来，跳过这台
        debugPrint('cast: skip $location: $e');
      }
    }

    controller.onListen = () async {
      await holdMulticastLock(true);
      // 找不到网卡地址时退回默认路由
      final locals = await _localAddresses();
      final binds = locals.isEmpty ? [InternetAddress.anyIPv4] : locals;
      for (final address in binds) {
        try {
          final bound = await RawDatagramSocket.bind(address, 0);
          if (finished) {
            bound.close();
            return;
          }
          bound.multicastHops = 4;
          sockets.add(bound);
          bound.listen((event) {
            if (event != RawSocketEvent.read) return;
            final datagram = bound.receive();
            if (datagram == null) return;
            final location = parseSsdpLocation(
              utf8.decode(datagram.data, allowMalformed: true),
            );
            if (location != null && seenLocations.add(location)) {
              pending.add(describe(location));
            }
          }, onError: (Object _) {});
        } on SocketException {
          // 这个网卡发不了，试下一个
        }
      }
      if (sockets.isEmpty) {
        controller.addError(const CastException('没能开始搜索，请确认手机连着 Wi-Fi'));
        await finish();
        return;
      }
      void send() {
        for (final socket in sockets) {
          for (final target in _searchTargets) {
            try {
              socket.send(
                utf8.encode(searchMessage(target)),
                InternetAddress(_ssdpAddress),
                _ssdpPort,
              );
            } on SocketException {
              // 这个网卡此刻发不出去，下一轮再试
            }
          }
        }
      }

      // UDP 会丢包，隔一秒再问几次
      send();
      timers
        ..add(Timer(const Duration(seconds: 1), send))
        ..add(Timer(const Duration(seconds: 2), send))
        ..add(Timer(const Duration(seconds: 3), send))
        ..add(Timer(timeout, finish));
    };
    controller.onCancel = finish;
    return controller.stream;
  }
}

@visibleForTesting
String searchMessage(String target) =>
    'M-SEARCH * HTTP/1.1\r\n'
    'HOST: $_ssdpAddress:$_ssdpPort\r\n'
    'MAN: "ssdp:discover"\r\n'
    'MX: 2\r\n'
    'ST: $target\r\n'
    '\r\n';

/// 搜索应答里的 LOCATION（设备描述地址）。不是成功应答时返回空。
@visibleForTesting
String? parseSsdpLocation(String response) {
  final lines = const LineSplitter().convert(response);
  if (lines.isEmpty || !lines.first.toUpperCase().contains(' 200 ')) {
    return null;
  }
  for (final line in lines.skip(1)) {
    final colon = line.indexOf(':');
    if (colon <= 0) continue;
    if (line.substring(0, colon).trim().toUpperCase() == 'LOCATION') {
      final value = line.substring(colon + 1).trim();
      return value.isEmpty ? null : value;
    }
  }
  return null;
}

/// 读设备描述，找出 AVTransport 服务。不能投视频的设备（音箱等）返回空。
Future<CastDevice?> fetchDevice(Dio dio, Uri location) async {
  final response = await dio.getUri<String>(
    location,
    options: Options(responseType: ResponseType.plain),
  );
  final device = parseDeviceDescription(response.data ?? '', location);
  if (device == null || await _playsVideo(dio, device) == false) return null;
  return device;
}

/// 问电视能接收什么格式（ConnectionManager 的 GetProtocolInfo），一个视频格式都没有的是音箱。
/// 问不到或者读不懂时返回空，不能因此把电视排除掉。
Future<bool?> _playsVideo(Dio dio, CastDevice device) async {
  final url = device.connectionUrl;
  final type = device.connectionType;
  if (url == null || type == null) return null;
  try {
    final response = await dio.postUri<String>(
      url,
      data: soapEnvelope(type, 'GetProtocolInfo', const {}, instance: false),
      options: Options(
        responseType: ResponseType.plain,
        validateStatus: (_) => true,
        persistentConnection: false,
        headers: {
          'Content-Type': 'text/xml; charset="utf-8"',
          'SOAPACTION': '"$type#GetProtocolInfo"',
        },
      ),
    );
    if (response.statusCode != 200) return null;
    return supportsVideo(soapResult(response.data ?? '', 'GetProtocolInfo'));
  } on Object {
    return null;
  }
}

/// GetProtocolInfo 应答里的 Sink 是电视能播的格式，形如 `http-get:*:video/mp4:*,…`。
/// 没有这一项时不判断。
@visibleForTesting
bool? supportsVideo(Map<String, String> protocolInfo) {
  final sink = protocolInfo['Sink']?.toLowerCase();
  if (sink == null || sink.isEmpty) return null;
  return sink.contains('video/') || sink.contains('mpegurl');
}

@visibleForTesting
CastDevice? parseDeviceDescription(String body, Uri location) {
  final XmlDocument document;
  try {
    document = XmlDocument.parse(body);
  } on XmlException {
    return null;
  }
  String? text(XmlElement parent, String name) {
    final value = parent
        .findElements(name, namespaceUri: '*')
        .firstOrNull
        ?.innerText
        .trim();
    return value == null || value.isEmpty ? null : value;
  }

  // 有的电视把渲染器放在内嵌设备里，名字用外层设备的
  final root = document
      .findAllElements('device', namespaceUri: '*')
      .firstOrNull;
  if (root == null) return null;
  final base = switch (document.rootElement
      .findElements('URLBase', namespaceUri: '*')
      .firstOrNull
      ?.innerText
      .trim()) {
    final value? when value.isNotEmpty => Uri.parse(value),
    _ => location,
  };
  Uri? connectionUrl;
  String? connectionType;
  for (final service in document.findAllElements(
    'service',
    namespaceUri: '*',
  )) {
    final type = text(service, 'serviceType');
    final control = text(service, 'controlURL');
    if (type != null &&
        control != null &&
        type.startsWith('urn:schemas-upnp-org:service:ConnectionManager:')) {
      connectionType = type;
      connectionUrl = base.resolve(control);
    }
  }
  for (final service in document.findAllElements(
    'service',
    namespaceUri: '*',
  )) {
    final type = text(service, 'serviceType');
    final control = text(service, 'controlURL');
    if (type == null || control == null) continue;
    if (!type.startsWith('urn:schemas-upnp-org:service:AVTransport:')) {
      continue;
    }
    return CastDevice(
      id: text(root, 'UDN') ?? location.toString(),
      name: text(root, 'friendlyName') ?? location.host,
      controlUrl: base.resolve(control),
      serviceType: type,
      connectionUrl: connectionUrl,
      connectionType: connectionType,
    );
  }
  return null;
}

const _lock = MethodChannel('iris/device');
var _lockHolders = 0;

/// 安卓默认丢掉组播包，搜索期间拿着组播锁（MainActivity）。SSDP 和 mDNS 搜索共用。
/// 重新搜索时新旧两次搜索会重叠，原生端的锁不计数，所以在这里计数，
/// 第一次搜索开始时拿锁，最后一次结束时才放。
Future<void> holdMulticastLock(bool acquire) async {
  if (kIsWeb || defaultTargetPlatform != TargetPlatform.android) return;
  _lockHolders += acquire ? 1 : -1;
  if (_lockHolders != (acquire ? 1 : 0)) return;
  try {
    await _lock.invokeMethod<void>(
      acquire ? 'acquireMulticastLock' : 'releaseMulticastLock',
    );
  } on Object {
    // 拿不到锁也照样搜，多数电视的应答是单播
  }
}
