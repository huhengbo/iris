import 'dart:async';
import 'dart:math';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:multicast_dns/multicast_dns.dart';
import 'package:xml/xml.dart';

import 'dlna.dart';

// AirPlay 投屏（ADR 0023）：mDNS 找 `_airplay._tcp`，再用 HTTP 把视频地址交给接收端，
// 接收端自己去拉流。走的是 AirPlay 的视频推送接口（POST /play），不是屏幕镜像。

const _service = '_airplay._tcp.local';

// ---- mDNS 搜索 ----

class MdnsDiscovery implements CastDiscovery {
  @override
  Stream<CastDevice> search({Duration timeout = const Duration(seconds: 5)}) {
    final controller = StreamController<CastDevice>();
    final seen = <String>{};
    final pending = <Future<void>>[];
    MDnsClient? client;
    StreamSubscription<PtrResourceRecord>? lookup;
    var finished = false;

    Future<void> finish() async {
      if (finished) return;
      finished = true;
      await lookup?.cancel();
      await Future.wait(pending);
      client?.stop();
      await holdMulticastLock(false);
      if (!controller.isClosed) await controller.close();
    }

    Future<T?> first<T extends ResourceRecord>(
      ResourceRecordQuery query,
    ) async {
      try {
        return await client!
            .lookup<T>(query, timeout: const Duration(seconds: 2))
            .first;
      } on StateError {
        return null;
      }
    }

    Future<void> resolve(String instance) async {
      try {
        final srv = await first<SrvResourceRecord>(
          ResourceRecordQuery.service(instance),
        );
        if (srv == null) return;
        final results = await Future.wait([
          first<TxtResourceRecord>(ResourceRecordQuery.text(instance)),
          first<IPAddressResourceRecord>(
            ResourceRecordQuery.addressIPv4(srv.target),
          ),
        ]);
        final address = (results[1] as IPAddressResourceRecord?)?.address;
        if (address == null) return;
        final device = airPlayDevice(
          instance: instance,
          address: address.address,
          port: srv.port,
          txt: (results[0] as TxtResourceRecord?)?.text ?? '',
        );
        if (device != null && seen.add(device.id) && !controller.isClosed) {
          controller.add(device);
        }
      } on Object catch (e) {
        debugPrint('cast: skip airplay $instance: $e');
      }
    }

    controller.onListen = () async {
      await holdMulticastLock(true);
      try {
        final started = MDnsClient();
        // 只在局域网网卡上收发，和 SSDP 搜索同样的原因
        await started.start(interfacesFactory: (_) async => lanInterfaces());
        if (finished) {
          started.stop();
          return;
        }
        client = started;
      } on Object catch (e) {
        debugPrint('cast: mdns start failed: $e');
        // 接收端用 SSDP 也能找到的话不必报错，由组合搜索决定
        controller.addError(const CastException('没能开始搜索，请确认手机连着 Wi-Fi'));
        await finish();
        return;
      }
      lookup = client!
          .lookup<PtrResourceRecord>(
            ResourceRecordQuery.serverPointer(_service),
            timeout: timeout,
          )
          .listen(
            (ptr) {
              if (seen.add('ptr:${ptr.domainName}')) {
                pending.add(resolve(ptr.domainName));
              }
            },
            onError: (Object _) {},
            onDone: finish,
          );
    };
    controller.onCancel = finish;
    return controller.stream;
  }
}

/// 把一条 mDNS 记录整理成可投屏的设备。不能放视频的（音箱等）返回空。
@visibleForTesting
CastDevice? airPlayDevice({
  required String instance,
  required String address,
  required int port,
  required String txt,
}) {
  final records = parseTxt(txt);
  if (!airPlaySupportsVideo(records['features'])) return null;
  final suffix = '._airplay._tcp.local';
  final name = instance.endsWith(suffix)
      ? instance.substring(0, instance.length - suffix.length)
      : instance;
  return CastDevice(
    id: 'airplay:${records['deviceid'] ?? records['pi'] ?? instance}',
    name: name.replaceAll(r'\032', ' '),
    controlUrl: Uri(scheme: 'http', host: address, port: port),
    serviceType: 'airplay',
    protocol: CastProtocol.airplay,
  );
}

/// TXT 记录，一行一条 `键=值`。
@visibleForTesting
Map<String, String> parseTxt(String txt) {
  final records = <String, String>{};
  for (final line in txt.split('\n')) {
    final eq = line.indexOf('=');
    if (eq > 0) records[line.substring(0, eq).trim()] = line.substring(eq + 1);
  }
  return records;
}

/// features 是 `0x低32位,0x高32位`。第 0 位是视频，第 4 位是 HLS 视频；没有这一项时不排除。
@visibleForTesting
bool airPlaySupportsVideo(String? features) {
  if (features == null || features.isEmpty) return true;
  final low = int.tryParse(
    features.split(',').first.replaceFirst('0x', ''),
    radix: 16,
  );
  if (low == null) return true;
  return low & 0x1 != 0 || low & 0x10 != 0;
}

// ---- AirPlay 控制 ----

class AirPlayRenderer implements CastRenderer {
  AirPlayRenderer(this.device, this._dio);

  final CastDevice device;
  final Dio _dio;
  final _session = _newSessionId();

  /// AirPlay 的 /play 一推上去就开始放，紧接着的 play() 不必再发
  var _justLoaded = false;

  static String _newSessionId() {
    final random = Random.secure();
    String hex(int n) =>
        List.generate(n, (_) => random.nextInt(16).toRadixString(16)).join();
    return '${hex(8)}-${hex(4)}-${hex(4)}-${hex(4)}-${hex(12)}';
  }

  @override
  Future<void> load(String url, String title) async {
    await _send(
      'POST',
      '/play',
      body: 'Content-Location: $url\nStart-Position: 0\n',
      receiveTimeout: castLoadTimeout,
    );
    _justLoaded = true;
  }

  @override
  Future<void> play() async {
    if (_justLoaded) {
      _justLoaded = false;
      return;
    }
    await _send('POST', '/rate?value=1.000000');
  }

  @override
  Future<void> pause() => _send('POST', '/rate?value=0.000000');

  @override
  Future<void> stop() => _send('POST', '/stop');

  @override
  Future<void> seek(Duration position) => _send(
    'POST',
    '/scrub?position=${(position.inMilliseconds / 1000).toStringAsFixed(3)}',
  );

  @override
  Future<CastStatus> status() async {
    final info = parsePlist(await _send('GET', '/playback-info'));
    return airPlayStatus(info);
  }

  Future<String> _send(
    String method,
    String path, {
    String? body,
    Duration? receiveTimeout,
  }) async {
    final Response<String> response;
    try {
      response = await _dio.requestUri<String>(
        device.controlUrl.replace(
          path: path.split('?').first,
          query: path.contains('?') ? path.split('?').last : null,
        ),
        data: body,
        options: Options(
          method: method,
          responseType: ResponseType.plain,
          validateStatus: (_) => true,
          receiveTimeout: receiveTimeout,
          headers: {
            'User-Agent': 'MediaControl/1.0',
            'X-Apple-Session-ID': _session,
            if (body != null) 'Content-Type': 'text/parameters',
          },
        ),
      );
    } on DioException catch (e) {
      throw castNetworkError(e, '$method $path');
    }
    final status = response.statusCode ?? 0;
    if (status < 200 || status >= 300) {
      throw CastException('电视拒绝了操作（HTTP $status）', fault: true);
    }
    return response.data ?? '';
  }
}

/// /playback-info 的结果：rate 大于 0 在放，有片长没在放是暂停，什么都没有是没在放。
@visibleForTesting
CastStatus airPlayStatus(Map<String, Object> info) {
  final rate = (info['rate'] as num?)?.toDouble() ?? 0;
  final duration = (info['duration'] as num?)?.toDouble();
  final position = (info['position'] as num?)?.toDouble();
  Duration? seconds(double? value) =>
      value == null ? null : Duration(milliseconds: (value * 1000).round());
  final length = duration != null && duration > 0 ? seconds(duration) : null;
  final state = rate > 0
      ? CastState.playing
      : length != null && info['readyToPlay'] != false
      // 放完后有的接收端停在片尾，rate 为 0
      ? (position != null && position >= duration! - 1
            ? CastState.stopped
            : CastState.paused)
      : CastState.stopped;
  return CastStatus(
    state: state,
    position: seconds(position),
    duration: length,
  );
}

/// XML 格式的 plist 里最外层字典的简单值（数字、布尔、字符串）。
/// 二进制 plist 和解析不了的内容返回空字典。
@visibleForTesting
Map<String, Object> parsePlist(String body) {
  final XmlDocument document;
  try {
    document = XmlDocument.parse(body);
  } on XmlException {
    return const {};
  }
  final dict = document.rootElement.findElements('dict').firstOrNull;
  if (dict == null) return const {};
  final result = <String, Object>{};
  final children = dict.childElements.toList();
  for (var i = 0; i + 1 < children.length; i += 2) {
    if (children[i].name.local != 'key') continue;
    final value = children[i + 1];
    final text = value.innerText.trim();
    switch (value.name.local) {
      case 'real' || 'integer':
        final number = num.tryParse(text);
        if (number != null) result[children[i].innerText.trim()] = number;
      case 'true':
        result[children[i].innerText.trim()] = true;
      case 'false':
        result[children[i].innerText.trim()] = false;
      case 'string':
        result[children[i].innerText.trim()] = text;
    }
  }
  return result;
}
