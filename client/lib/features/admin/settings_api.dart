import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';

const _settingsPath = '/api/admin/settings';

class AdminSettings {
  const AdminSettings({
    required this.serverId,
    required this.serverName,
    this.publicUrl,
    this.pluginsProxy,
    required this.extrasMinMb,
    required this.missingGraceDays,
  });

  factory AdminSettings.fromJson(Map<String, dynamic> json) {
    final server = json['server'] as Map<String, dynamic>;
    final plugins = json['plugins'] as Map<String, dynamic>?;
    final library = json['library'] as Map<String, dynamic>;
    return AdminSettings(
      serverId: server['id'] as String,
      serverName: server['name'] as String,
      publicUrl: server['public_url'] as String?,
      pluginsProxy: plugins?['proxy'] as String?,
      extrasMinMb: library['extras_min_mb'] as int,
      missingGraceDays: library['missing_grace_days'] as int,
    );
  }

  final String serverId;
  final String serverName;

  /// 对外访问地址，发给播放器的地址都基于它生成；没设置时不能播放（ADR 0015）
  final String? publicUrl;

  /// 所有插件默认使用的代理地址，密码以 *** 代替；原样传回表示不修改
  final String? pluginsProxy;

  /// 电影资料库中小于这个大小的视频视为附加内容
  final int extrasMinMb;

  /// 来源上消失的文件保留这么多天后从库里删除，期间移回来可以恢复
  final int missingGraceDays;
}

final adminSettingsProvider = FutureProvider.autoDispose<AdminSettings>((
  ref,
) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<Map<String, dynamic>>(_settingsPath);
  return AdminSettings.fromJson(response.data!);
});

Future<AdminSettings> _put(Dio dio, String section, Object data) async {
  final response = await dio.put<Map<String, dynamic>>(
    '$_settingsPath/$section',
    data: data,
  );
  return AdminSettings.fromJson(response.data!);
}

/// publicUrl 空字符串表示清除。
Future<AdminSettings> saveServer(
  Dio dio, {
  required String name,
  required String publicUrl,
}) => _put(dio, 'server', {'name': name, 'public_url': publicUrl});

/// 所有插件默认走的代理。空字符串表示清除；打码地址原样传回表示不修改密码。
Future<AdminSettings> savePluginsProxy(Dio dio, {required String proxy}) =>
    _put(dio, 'plugins', {'proxy': proxy});

Future<AdminSettings> saveLibrarySettings(
  Dio dio, {
  required int extrasMinMb,
  required int missingGraceDays,
}) => _put(dio, 'library', {
  'extras_min_mb': extrasMinMb,
  'missing_grace_days': missingGraceDays,
});
