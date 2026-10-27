import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';

/// 一次登录（会话）。第三方客户端（Emby 协议）的 kind 是 `emby`，
/// 普通 Iris 客户端是 `app`。
class DeviceSession {
  const DeviceSession({
    required this.id,
    this.deviceName,
    this.platform,
    this.clientVersion,
    this.ip,
    required this.kind,
    this.clientName,
    this.lastSeenAt,
  });

  factory DeviceSession.fromJson(Map<String, dynamic> json) => DeviceSession(
    id: json['id'] as int,
    deviceName: json['device_name'] as String?,
    platform: json['platform'] as String?,
    clientVersion: json['client_version'] as String?,
    ip: json['ip'] as String?,
    kind: json['kind'] as String,
    clientName: json['client_name'] as String?,
    lastSeenAt: json['last_seen_at'] as String?,
  );

  final int id;
  final String? deviceName;
  final String? platform;
  final String? clientVersion;
  final String? ip;

  /// app（Iris 客户端）或 emby（第三方客户端）
  final String? kind;

  /// 第三方客户端名，如 Infuse
  final String? clientName;

  /// ISO 8601，直接交给 formatIsoAgo 显示
  final String? lastSeenAt;

  bool get thirdParty => kind == 'emby';

  String get name => deviceName ?? clientName ?? '未知设备';
}

final devicesApiProvider = Provider<DevicesApi>(
  (ref) => DevicesApi(ref.watch(apiClientProvider)),
);

class DevicesApi {
  const DevicesApi(this._dio);

  final Dio _dio;

  Future<List<DeviceSession>> list() async {
    final response = await _dio.get<List<dynamic>>('/api/me/sessions');
    return [
      for (final item in response.data ?? const [])
        DeviceSession.fromJson(item as Map<String, dynamic>),
    ];
  }

  Future<void> revoke(int id) => _dio.delete<void>('/api/me/sessions/$id');
}

final myDevicesProvider = FutureProvider.autoDispose<List<DeviceSession>>(
  (ref) => ref.watch(devicesApiProvider).list(),
);
