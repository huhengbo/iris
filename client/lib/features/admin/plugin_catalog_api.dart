import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import 'plugins_api.dart';
import 'tasks_api.dart';

/// 仓库状态
enum RepositoryStatus {
  pending('检查中'),
  ok('正常'),
  error('拉取失败'),
  unsupported('版本不支持');

  const RepositoryStatus(this.label);

  factory RepositoryStatus.fromJson(String? value) => switch (value) {
    'ok' => RepositoryStatus.ok,
    'error' => RepositoryStatus.error,
    'unsupported' => RepositoryStatus.unsupported,
    _ => RepositoryStatus.pending,
  };

  final String label;
}

class PluginRepository {
  const PluginRepository({
    required this.id,
    required this.name,
    required this.url,
    required this.official,
    required this.enabled,
    required this.status,
    this.error,
    this.schema,
    required this.pluginCount,
    required this.invalidCount,
    this.lastCheckedAt,
    this.lastSuccessAt,
    required this.createdAt,
  });

  factory PluginRepository.fromJson(Map<String, dynamic> json) =>
      PluginRepository(
        id: json['id'] as int,
        name: json['name'] as String? ?? '',
        url: json['url'] as String,
        official: json['official'] as bool? ?? false,
        enabled: json['enabled'] as bool? ?? true,
        status: RepositoryStatus.fromJson(json['status'] as String?),
        error: json['error'] as String?,
        schema: json['schema'] as int?,
        pluginCount: json['plugin_count'] as int? ?? 0,
        invalidCount: json['invalid_count'] as int? ?? 0,
        lastCheckedAt: json['last_checked_at'] != null
            ? DateTime.parse(json['last_checked_at'] as String).toLocal()
            : null,
        lastSuccessAt: json['last_success_at'] != null
            ? DateTime.parse(json['last_success_at'] as String).toLocal()
            : null,
        createdAt: DateTime.parse(json['created_at'] as String).toLocal(),
      );

  final int id;
  final String name;
  final String url;
  final bool official;
  final bool enabled;
  final RepositoryStatus status;
  final String? error;
  final int? schema;
  final int pluginCount;
  final int invalidCount;
  final DateTime? lastCheckedAt;
  final DateTime? lastSuccessAt;
  final DateTime createdAt;
}

/// 目录中插件的安装状态
enum CatalogPluginStatus {
  available('可安装'),
  installed('已安装'),
  updateAvailable('可更新'),
  conflict('冲突'),
  incompatible('不兼容');

  const CatalogPluginStatus(this.label);

  factory CatalogPluginStatus.fromJson(String? value) => switch (value) {
    'installed' => CatalogPluginStatus.installed,
    'update_available' => CatalogPluginStatus.updateAvailable,
    'conflict' => CatalogPluginStatus.conflict,
    'incompatible' => CatalogPluginStatus.incompatible,
    _ => CatalogPluginStatus.available,
  };

  final String label;
}

class CatalogPlugin {
  const CatalogPlugin({
    required this.id,
    required this.name,
    required this.description,
    required this.version,
    required this.repositoryId,
    required this.repositoryName,
    required this.category,
    required this.sdk,
    required this.provides,
    required this.capabilities,
    this.homepage,
    required this.size,
    required this.status,
    this.installedVersion,
    this.reason,
  });

  factory CatalogPlugin.fromJson(Map<String, dynamic> json) => CatalogPlugin(
    id: json['id'] as String,
    name: json['name'] as String,
    description: json['description'] as String? ?? '',
    version: json['version'] as String,
    repositoryId: json['repository_id'] as int,
    repositoryName: json['repository_name'] as String? ?? '',
    category: PluginCategory.fromJson(json['category'] as String?),
    sdk: json['sdk'] as String? ?? '',
    provides: [
      for (final p in (json['provides'] as List<dynamic>? ?? const []))
        p as String,
    ],
    capabilities: [
      for (final c in (json['capabilities'] as List<dynamic>? ?? const []))
        c as String,
    ],
    homepage: json['homepage'] as String?,
    size: json['size'] as int? ?? 0,
    status: CatalogPluginStatus.fromJson(json['status'] as String?),
    installedVersion: json['installed_version'] as String?,
    reason: json['reason'] as String?,
  );

  final String id;
  final String name;
  final String description;
  final String version;
  final int repositoryId;
  final String repositoryName;
  final PluginCategory category;
  final String sdk;
  final List<String> provides;
  final List<String> capabilities;
  final String? homepage;
  final int size;
  final CatalogPluginStatus status;
  final String? installedVersion;
  final String? reason;
}

class PluginCatalog {
  const PluginCatalog({
    required this.sdkVersion,
    required this.repositories,
    required this.plugins,
  });

  factory PluginCatalog.fromJson(Map<String, dynamic> json) => PluginCatalog(
    sdkVersion: json['sdk_version'] as String? ?? '',
    repositories: [
      for (final r in (json['repositories'] as List<dynamic>? ?? const []))
        PluginRepository.fromJson(r as Map<String, dynamic>),
    ],
    plugins: [
      for (final p in (json['plugins'] as List<dynamic>? ?? const []))
        CatalogPlugin.fromJson(p as Map<String, dynamic>),
    ],
  );

  final String sdkVersion;
  final List<PluginRepository> repositories;
  final List<CatalogPlugin> plugins;
}

class PluginUploadManifest {
  const PluginUploadManifest({
    required this.id,
    required this.name,
    required this.version,
    this.description = '',
    this.sdk = '',
    this.provides = const [],
    this.capabilities = const [],
  });

  factory PluginUploadManifest.fromJson(Map<String, dynamic> json) =>
      PluginUploadManifest(
        id: json['id'] as String,
        name: json['name'] as String,
        version: json['version'] as String,
        description: json['description'] as String? ?? '',
        sdk: json['sdk'] as String? ?? '',
        provides: [
          for (final p in (json['provides'] as List<dynamic>? ?? const []))
            p as String,
        ],
        capabilities: [
          for (final c in (json['capabilities'] as List<dynamic>? ?? const []))
            c as String,
        ],
      );

  final String id;
  final String name;
  final String version;
  final String description;
  final String sdk;
  final List<String> provides;
  final List<String> capabilities;
}

class PluginUpload {
  const PluginUpload({
    required this.uploadId,
    required this.sha256,
    required this.size,
    required this.manifest,
    required this.category,
    required this.action,
    this.installedVersion,
    this.expiresAt,
  });

  factory PluginUpload.fromJson(Map<String, dynamic> json) => PluginUpload(
    uploadId: json['upload_id'] as String,
    sha256: json['sha256'] as String,
    size: json['size'] as int,
    manifest: PluginUploadManifest.fromJson(
      json['manifest'] as Map<String, dynamic>,
    ),
    category: PluginCategory.fromJson(json['category'] as String?),
    action: json['action'] as String? ?? 'install',
    installedVersion: json['installed_version'] as String?,
    expiresAt: json['expires_at'] != null
        ? DateTime.tryParse(json['expires_at'] as String)?.toLocal()
        : null,
  );

  final String uploadId;
  final String sha256;
  final int size;
  final PluginUploadManifest manifest;
  final PluginCategory category;
  final String action;
  final String? installedVersion;
  final DateTime? expiresAt;
}

/// 仓库列表提供者
final pluginRepositoriesProvider =
    FutureProvider.autoDispose<List<PluginRepository>>((ref) async {
      final response = await ref
          .watch(apiClientProvider)
          .get<Map<String, dynamic>>('/api/admin/plugin-repositories');
      return [
        for (final item in (response.data!['repositories'] as List<dynamic>))
          PluginRepository.fromJson(item as Map<String, dynamic>),
      ];
    });

/// 目录提供者
final pluginCatalogProvider = FutureProvider.autoDispose<PluginCatalog>((
  ref,
) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<Map<String, dynamic>>('/api/admin/plugin-catalog');
  return PluginCatalog.fromJson(response.data!);
});

/// 添加仓库
Future<PluginRepository> addPluginRepository(
  Dio dio, {
  required String url,
  bool acceptRisk = true,
}) async {
  final response = await dio.post<Map<String, dynamic>>(
    '/api/admin/plugin-repositories',
    data: {'url': url, 'accept_risk': acceptRisk},
  );
  return PluginRepository.fromJson(response.data!);
}

/// 启停仓库
Future<PluginRepository> setPluginRepositoryEnabled(
  Dio dio,
  int id,
  bool enabled,
) async {
  final response = await dio.patch<Map<String, dynamic>>(
    '/api/admin/plugin-repositories/$id',
    data: {'enabled': enabled},
  );
  return PluginRepository.fromJson(response.data!);
}

/// 删除仓库
Future<void> deletePluginRepository(Dio dio, int id) async {
  await dio.delete<void>('/api/admin/plugin-repositories/$id');
}

/// 立即并发刷新所有已启用的仓库
Future<List<PluginRepository>> refreshPluginRepositories(Dio dio) async {
  final response = await dio.post<Map<String, dynamic>>(
    '/api/admin/plugin-repositories/refresh',
  );
  return [
    for (final item in (response.data!['repositories'] as List<dynamic>))
      PluginRepository.fromJson(item as Map<String, dynamic>),
  ];
}

/// 从仓库安装插件
Future<int> installCatalogPlugin(
  Dio dio, {
  required int repositoryId,
  required String pluginId,
  required String version,
  bool acceptPermissions = true,
}) async {
  final response = await dio.post<Map<String, dynamic>>(
    '/api/admin/plugins/install',
    data: {
      'repository_id': repositoryId,
      'plugin_id': pluginId,
      'version': version,
      'accept_permissions': acceptPermissions,
    },
  );
  return response.data!['task_id'] as int;
}

/// 上传插件压缩包
Future<PluginUpload> uploadPluginZip(Dio dio, Uint8List bytes) async {
  final response = await dio.post<Map<String, dynamic>>(
    '/api/admin/plugins/uploads',
    data: Stream.fromIterable([bytes]),
    options: Options(
      contentType: 'application/zip',
      headers: {Headers.contentLengthHeader: bytes.length},
    ),
  );
  return PluginUpload.fromJson(response.data!);
}

/// 安装已上传的本地插件
Future<int> installUploadedPlugin(
  Dio dio,
  String uploadId, {
  bool acceptRisk = true,
}) async {
  final response = await dio.post<Map<String, dynamic>>(
    '/api/admin/plugins/uploads/$uploadId/install',
    data: {'accept_risk': acceptRisk},
  );
  return response.data!['task_id'] as int;
}

/// 删除/废弃已上传的插件
Future<void> deleteUploadedPlugin(Dio dio, String uploadId) async {
  await dio.delete<void>('/api/admin/plugins/uploads/$uploadId');
}

/// 将插件后台任务错误码或回滚状态转为用户友好的中文提示
String pluginTaskErrorMessage(AdminTask task) {
  final result = task.result;
  final errorCode = result?['error_code'] as String?;
  final rolledBack = result?['rolled_back'] as bool? ?? false;
  final rollbackSuffix = rolledBack ? '（已自动回滚至上一版本）' : '';

  final codeMsg = switch (errorCode) {
    'package_download_failed' => '下载插件包失败',
    'package_checksum_mismatch' => '插件包校验和不匹配',
    'package_too_large' => '插件包超出大小限制',
    'package_invalid' => '插件包格式无效',
    'package_mismatch' => '压缩包信息与索引条目不一致',
    'dependency_install_failed' => '安装插件依赖失败',
    'plugin_start_failed' => '插件启动失败',
    _ => null,
  };

  if (codeMsg != null) {
    return '$codeMsg$rollbackSuffix';
  }
  if (task.error != null && task.error!.isNotEmpty) {
    return '${task.error!}$rollbackSuffix';
  }
  return '任务执行失败$rollbackSuffix';
}
