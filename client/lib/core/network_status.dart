import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

/// 只区分播放流量提醒需要的网络类型。
enum NetworkKind { wifi, mobile, other, offline }

extension NetworkKindX on NetworkKind {
  bool get isMobile => this == NetworkKind.mobile;
  bool get isWifi => this == NetworkKind.wifi;
}

/// connectivity_plus 可能同时返回多个传输；移动数据优先，避免静默消耗流量。
NetworkKind networkKindFrom(Iterable<ConnectivityResult> results) {
  if (results.contains(ConnectivityResult.mobile)) return NetworkKind.mobile;
  if (results.contains(ConnectivityResult.wifi)) return NetworkKind.wifi;
  if (results.contains(ConnectivityResult.none)) return NetworkKind.offline;
  return NetworkKind.other;
}

bool isCellularSwitch(NetworkKind previous, NetworkKind current) =>
    previous.isWifi && current.isMobile;

abstract interface class NetworkStatusSource {
  Future<NetworkKind> current();

  Stream<NetworkKind> get changes;
}

class ConnectivityNetworkStatus implements NetworkStatusSource {
  ConnectivityNetworkStatus([Connectivity? connectivity])
    : _connectivity = connectivity ?? Connectivity();

  final Connectivity _connectivity;

  @override
  Future<NetworkKind> current() async {
    try {
      return networkKindFrom(await _connectivity.checkConnectivity());
    } on Object {
      // Web、桌面和 Flutter 测试环境可能没有原生网络插件；不阻断播放。
      return NetworkKind.other;
    }
  }

  @override
  Stream<NetworkKind> get changes =>
      _connectivity.onConnectivityChanged.map(networkKindFrom);
}

final networkStatusProvider = Provider<NetworkStatusSource>((ref) {
  // 移动数据只存在于手机 App；桌面和 Web 没有可靠的运营商网络类型。
  if (kIsWeb ||
      (defaultTargetPlatform != TargetPlatform.android &&
          defaultTargetPlatform != TargetPlatform.iOS)) {
    return const _UnavailableNetworkStatus();
  }
  return ConnectivityNetworkStatus();
});

class _UnavailableNetworkStatus implements NetworkStatusSource {
  const _UnavailableNetworkStatus();

  @override
  Future<NetworkKind> current() async => NetworkKind.other;

  @override
  Stream<NetworkKind> get changes => const Stream<NetworkKind>.empty();
}
