import 'dart:io';

/// App 端：设备名用主机名（Android 上通常只是 localhost，这时退回到「Android 设备」）。
({String? name, String? platform}) deviceLabel() {
  final platform = switch (Platform.operatingSystem) {
    'android' => 'Android',
    'ios' => 'iOS',
    'macos' => 'macOS',
    'windows' => 'Windows',
    'linux' => 'Linux',
    final other => other,
  };
  var host = Platform.localHostname.trim();
  if (host.endsWith('.local')) host = host.substring(0, host.length - 6);
  final name = host.isEmpty || host == 'localhost' ? '$platform 设备' : host;
  return (name: name, platform: platform);
}
