import 'package:flutter/services.dart';

/// 安装请求的结果。
enum InstallOutcome {
  /// 已调起系统安装器，接下来由系统界面接手。
  installing,

  /// 还没有“安装未知应用”的权限，已打开设置页；用户允许后回来再点一次安装。
  needsPermission,
}

/// 升级用到的原生能力（android/.../UpdateChannel.kt）。只在 Android 上调用。
abstract interface class UpdatePlatform {
  /// 设备支持的 ABI，按优先顺序排列。
  Future<List<String>> supportedAbis();

  /// 安装包的保存路径；同时删掉目录里其他文件。
  Future<String> prepareFile(String name);

  /// 文件的 sha256（小写 hex），文件不存在时为空。
  Future<String?> sha256(String path);

  /// 删掉所有已下载的安装包。
  Future<void> clear();

  Future<InstallOutcome> install(String path);
}

class MethodChannelUpdatePlatform implements UpdatePlatform {
  static const _channel = MethodChannel('iris/update');

  @override
  Future<List<String>> supportedAbis() async =>
      (await _channel.invokeListMethod<String>('supportedAbis')) ?? const [];

  @override
  Future<String> prepareFile(String name) async =>
      (await _channel.invokeMethod<String>('prepareFile', {'name': name}))!;

  @override
  Future<String?> sha256(String path) =>
      _channel.invokeMethod<String>('sha256', {'path': path});

  @override
  Future<void> clear() => _channel.invokeMethod<void>('clear');

  @override
  Future<InstallOutcome> install(String path) async {
    final outcome = await _channel.invokeMethod<String>('install', {
      'path': path,
    });
    return outcome == 'permission'
        ? InstallOutcome.needsPermission
        : InstallOutcome.installing;
  }
}
