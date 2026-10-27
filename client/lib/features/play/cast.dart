import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'airplay.dart';
import 'dlna.dart';

/// 同时搜索多种协议，找到一台推一台。某一种搜不了不影响其他，
/// 全部都失败而且一台都没找到时才报错。
class CompositeDiscovery implements CastDiscovery {
  CompositeDiscovery(this._sources);

  final List<CastDiscovery> _sources;

  @override
  Stream<CastDevice> search({Duration timeout = const Duration(seconds: 5)}) {
    final controller = StreamController<CastDevice>();
    final subscriptions = <StreamSubscription<CastDevice>>[];
    var open = _sources.length;
    var failed = 0;
    var found = false;
    Object? lastError;
    controller.onListen = () {
      for (final source in _sources) {
        subscriptions.add(
          source
              .search(timeout: timeout)
              .listen(
                (device) {
                  found = true;
                  controller.add(device);
                },
                onError: (Object e) {
                  failed++;
                  lastError = e;
                },
                onDone: () {
                  if (--open > 0) return;
                  if (!found && failed == _sources.length) {
                    controller.addError(lastError!);
                  }
                  unawaited(controller.close());
                },
              ),
        );
      }
    };
    controller.onCancel = () =>
        Future.wait([for (final s in subscriptions) s.cancel()]);
    return controller.stream;
  }
}

// ---- 依赖，测试时替换 ----

/// 和电视通信用的 HTTP 客户端：不带服务器地址和登录令牌，局域网里超时短一些。
final _castDioProvider = Provider<Dio>(
  (ref) => Dio(
    BaseOptions(
      connectTimeout: const Duration(seconds: 3),
      receiveTimeout: const Duration(seconds: 5),
      sendTimeout: const Duration(seconds: 5),
    ),
  ),
);

final castDiscoveryProvider = Provider<CastDiscovery>(
  (ref) => CompositeDiscovery([
    SsdpDiscovery(ref.watch(_castDioProvider)),
    MdnsDiscovery(),
  ]),
);

typedef CastRendererFactory = CastRenderer Function(CastDevice device);

final castRendererProvider = Provider<CastRendererFactory>((ref) {
  final dio = ref.watch(_castDioProvider);
  return (device) => switch (device.protocol) {
    CastProtocol.dlna => DlnaRenderer(device, dio),
    CastProtocol.airplay => AirPlayRenderer(device, dio),
  };
});
