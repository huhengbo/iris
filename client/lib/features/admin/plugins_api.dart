import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';

/// 插件在管理后台里的一个自定义页面。
class PluginPageInfo {
  const PluginPageInfo({required this.id, required this.title});

  factory PluginPageInfo.fromJson(Map<String, dynamic> json) =>
      PluginPageInfo(id: json['id'] as String, title: json['title'] as String);

  final String id;
  final String title;
}

/// 插件分类，决定它在插件中心里归到哪一组。
enum PluginCategory {
  metadata('元数据'),
  subtitle('字幕'),
  resource('资源来源'),
  other('其他');

  const PluginCategory(this.label);

  factory PluginCategory.fromJson(String? value) => switch (value) {
    'metadata' => PluginCategory.metadata,
    'subtitle' => PluginCategory.subtitle,
    'resource' => PluginCategory.resource,
    _ => PluginCategory.other,
  };

  final String label;
}

/// 插件的外网请求怎么走代理；只有 custom 时插件自己的地址才生效。
enum PluginProxyMode {
  inherit('沿用全局代理'),
  none('不走代理'),
  custom('自定义');

  const PluginProxyMode(this.label);

  factory PluginProxyMode.fromJson(String? value) => switch (value) {
    'none' => PluginProxyMode.none,
    'custom' => PluginProxyMode.custom,
    _ => PluginProxyMode.inherit,
  };

  final String label;
}

/// 插件在当前进程里的运行状态。
enum PluginState {
  running('运行中'),
  stopped('已停止'),
  failed('启动失败'),
  notInstalled('未安装');

  const PluginState(this.label);

  factory PluginState.fromJson(String value) => switch (value) {
    'running' => PluginState.running,
    'failed' => PluginState.failed,
    'not_installed' => PluginState.notInstalled,
    _ => PluginState.stopped,
  };

  final String label;
}

/// 插件来源
enum PluginSource {
  system('系统'),
  repository('仓库'),
  local('本地');

  const PluginSource(this.label);

  factory PluginSource.fromJson(String? value, {bool builtIn = false}) =>
      switch (value) {
        'system' => PluginSource.system,
        'repository' => PluginSource.repository,
        'local' => PluginSource.local,
        _ => builtIn ? PluginSource.system : PluginSource.local,
      };

  final String label;
}

class AdminPlugin {
  const AdminPlugin({
    required this.id,
    required this.name,
    required this.version,
    required this.sdkVersion,
    required this.provides,
    required this.requires,
    required this.trust,
    required this.builtIn,
    required this.enabled,
    required this.state,
    required this.updatedAt,
    this.source = PluginSource.system,
    this.repositoryId,
    this.repositoryName,
    this.availableVersion,
    this.packageStatus,
    this.packageError,
    this.description = '',
    this.category = PluginCategory.other,
    this.proxyMode = PluginProxyMode.inherit,
    this.proxy,
    this.pages = const [],
  });

  factory AdminPlugin.fromJson(Map<String, dynamic> json) {
    final builtIn = json['built_in'] as bool? ?? false;
    return AdminPlugin(
      id: json['id'] as String,
      name: json['name'] as String,
      version: json['version'] as String,
      sdkVersion: json['sdk_version'] as String,
      provides: [
        for (final item in json['provides'] as List<dynamic>) item as String,
      ],
      requires: [
        for (final item in json['requires'] as List<dynamic>) item as String,
      ],
      trust: json['trust'] as String,
      builtIn: builtIn,
      enabled: json['enabled'] as bool,
      state: PluginState.fromJson(json['state'] as String),
      updatedAt: DateTime.parse(json['updated_at'] as String).toLocal(),
      source: PluginSource.fromJson(
        json['source'] as String?,
        builtIn: builtIn,
      ),
      repositoryId: json['repository_id'] as int?,
      repositoryName: json['repository_name'] as String?,
      availableVersion: json['available_version'] as String?,
      packageStatus: json['package_status'] as String?,
      packageError: json['package_error'] as String?,
      description: json['description'] as String? ?? '',
      category: PluginCategory.fromJson(json['category'] as String?),
      proxyMode: PluginProxyMode.fromJson(json['proxy_mode'] as String?),
      proxy: json['proxy'] as String?,
      pages: [
        for (final item in json['pages'] as List<dynamic>? ?? const [])
          PluginPageInfo.fromJson(item as Map<String, dynamic>),
      ],
    );
  }

  final String id;
  final String name;
  final String version;
  final String sdkVersion;
  final List<String> provides;
  final List<String> requires;
  final String trust;
  final bool builtIn;
  final bool enabled;
  final PluginState state;
  final DateTime updatedAt;
  final PluginSource source;
  final int? repositoryId;
  final String? repositoryName;
  final String? availableVersion;
  final String? packageStatus;
  final String? packageError;

  /// 一句话介绍，可能为空
  final String description;
  final PluginCategory category;

  /// 外网请求的代理策略；[proxy] 只在 custom 时有意义
  final PluginProxyMode proxyMode;

  /// 自定义代理地址，密码以 *** 代替；原样传回表示不修改
  final String? proxy;
  final List<PluginPageInfo> pages;
}

/// 打开插件页面用的会话：令牌只能调用这一个插件的接口，十几分钟后过期。
class PluginSession {
  const PluginSession({required this.token, required this.pages});

  factory PluginSession.fromJson(Map<String, dynamic> json) => PluginSession(
    token: json['token'] as String,
    pages: [
      for (final item in json['pages'] as List<dynamic>)
        PluginPageLink.fromJson(item as Map<String, dynamic>),
    ],
  );

  final String token;
  final List<PluginPageLink> pages;
}

class PluginPageLink {
  const PluginPageLink({
    required this.id,
    required this.title,
    required this.url,
  });

  factory PluginPageLink.fromJson(Map<String, dynamic> json) => PluginPageLink(
    id: json['id'] as String,
    title: json['title'] as String,
    url: json['url'] as String,
  );

  final String id;
  final String title;

  /// 相对服务器根的路径，例如 /plugins/example.hello/ui/index.html
  final String url;
}

final pluginsProvider = FutureProvider.autoDispose<List<AdminPlugin>>((
  ref,
) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<Map<String, dynamic>>('/api/admin/plugins');
  return [
    for (final item in response.data!['plugins'] as List<dynamic>)
      AdminPlugin.fromJson(item as Map<String, dynamic>),
  ];
});

Future<void> setPluginEnabled(Dio dio, AdminPlugin plugin, bool enabled) async {
  await dio.put<Map<String, dynamic>>(
    '/api/admin/plugins/${plugin.id}/enabled',
    data: {'enabled': enabled},
  );
}

/// 保存插件的代理策略。[proxy] 只在 [mode] 为 custom 时发送；
/// 读到的打码地址原样传回表示不修改密码。
Future<AdminPlugin> setPluginProxy(
  Dio dio,
  String pluginId,
  PluginProxyMode mode,
  String? proxy,
) async {
  final response = await dio.put<Map<String, dynamic>>(
    '/api/admin/plugins/$pluginId/proxy',
    data: {
      'mode': mode.name,
      'proxy': mode == PluginProxyMode.custom ? proxy : null,
    },
  );
  return AdminPlugin.fromJson(response.data!);
}

Future<void> deletePlugin(Dio dio, String pluginId) async {
  await dio.delete<void>('/api/admin/plugins/$pluginId');
}

/// 卸载插件，[deleteData] 为 true 时同时删除插件数据与配置
Future<void> uninstallPlugin(
  Dio dio,
  String pluginId, {
  bool deleteData = false,
}) async {
  await dio.post<void>(
    '/api/admin/plugins/$pluginId/uninstall',
    data: {'delete_data': deleteData},
  );
}

/// 升级插件至指定版本，返回后台任务 ID
Future<int> upgradePlugin(
  Dio dio,
  String pluginId, {
  required String version,
  bool acceptPermissions = true,
}) async {
  final response = await dio.post<Map<String, dynamic>>(
    '/api/admin/plugins/$pluginId/upgrade',
    data: {'version': version, 'accept_permissions': acceptPermissions},
  );
  return response.data!['task_id'] as int;
}

Future<PluginSession> createPluginSession(Dio dio, String pluginId) async {
  final response = await dio.post<Map<String, dynamic>>(
    '/api/admin/plugins/$pluginId/session',
  );
  return PluginSession.fromJson(response.data!);
}

/// 打开详情页时取一次页面列表；令牌刷新由 PluginBridge 直接调用 [createPluginSession]。
final pluginSessionProvider = FutureProvider.autoDispose
    .family<PluginSession, String>(
      (ref, pluginId) =>
          createPluginSession(ref.watch(apiClientProvider), pluginId),
    );
