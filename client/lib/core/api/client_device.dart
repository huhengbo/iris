import 'dart:math';

import 'package:package_info_plus/package_info_plus.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'device_label_stub.dart'
    if (dart.library.io) 'device_label_io.dart'
    if (dart.library.js_interop) 'device_label_web.dart'
    as platform;

/// 登录和续期时上报的本机信息。「我的设备」靠它区分设备，
/// 服务端按 [id] 把同一设备的重新登录替换成一个会话。
class ClientDevice {
  const ClientDevice({
    required this.id,
    this.name,
    this.platform,
    this.version,
  });

  /// 安装时生成、之后不变；所有服务器和账号共用
  final String id;
  final String? name;
  final String? platform;
  final String? version;

  Map<String, String> toJson() => {
    'device_id': id,
    'device_name': ?name,
    'platform': ?platform,
    'client_version': ?version,
  };
}

const _deviceIdKey = 'client.device_id';

Future<ClientDevice> loadClientDevice() async {
  final prefs = SharedPreferencesAsync();
  var id = await prefs.getString(_deviceIdKey);
  if (id == null) {
    id = _newDeviceId();
    await prefs.setString(_deviceIdKey, id);
  }
  final info = await PackageInfo.fromPlatform();
  final label = platform.deviceLabel();
  return ClientDevice(
    id: id,
    name: label.name,
    platform: label.platform,
    version: info.buildNumber.isEmpty
        ? info.version
        : '${info.version} (${info.buildNumber})',
  );
}

String _newDeviceId() {
  final random = Random.secure();
  return List.generate(
    16,
    (_) => random.nextInt(256).toRadixString(16).padLeft(2, '0'),
  ).join();
}
