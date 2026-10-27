import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';

const resourcesPath = '/api/admin/resources';

/// 一个资源来源：某个资源插件的一份配置，比如一个网盘账号、一个服务器本地目录。
class ResourceSourceInfo {
  const ResourceSourceInfo({
    required this.id,
    required this.pluginId,
    required this.pluginName,
    required this.pluginEnabled,
    required this.name,
    required this.enabled,
    required this.status,
    this.config = const <String, dynamic>{},
    this.lastError,
    this.checkedAt,
    this.folderCount = 0,
    this.supportsDelete = false,
  });

  factory ResourceSourceInfo.fromJson(Map<String, dynamic> json) =>
      ResourceSourceInfo(
        id: json['id'] as int,
        pluginId: json['plugin_id'] as String,
        pluginName: json['plugin_name'] as String,
        pluginEnabled: json['plugin_enabled'] as bool,
        name: json['name'] as String,
        enabled: json['enabled'] as bool,
        status: json['status'] as String,
        config: (json['config'] as Map<String, dynamic>?) ?? const {},
        lastError: json['last_error'] as String?,
        checkedAt: json['checked_at'] as String?,
        folderCount: json['folder_count'] as int? ?? 0,
        supportsDelete: json['supports_delete'] as bool? ?? false,
      );

  final int id;
  final String pluginId;
  final String pluginName;

  /// 插件停用时来源整体不可用，即使来源自己是启用的
  final bool pluginEnabled;
  final String name;
  final bool enabled;

  /// unknown：还没同步过；ok：上次同步正常；error：上次同步出错
  final String status;
  final String? lastError;
  final String? checkedAt;
  final Map<String, dynamic> config;

  /// 资料库用到的目录数
  final int folderCount;

  /// 能否在本系统里删除来源上的文件
  final bool supportsDelete;

  /// 能浏览目录、能同步
  bool get usable => enabled && pluginEnabled;

  /// 不可用时给用户看的原因；可用时返回 null
  String? get unusableReason {
    if (!pluginEnabled) return '插件「$pluginName」已停用';
    if (!enabled) return '来源已停用';
    return null;
  }
}

String resourceStatusLabel(String status) => switch (status) {
  'ok' => '正常',
  'error' => '出错',
  _ => '尚未同步',
};

class ResourceConfigFieldInfo {
  const ResourceConfigFieldInfo({
    required this.key,
    required this.label,
    this.help = '',
    this.required = true,
    this.defaultValue = '',
  });

  factory ResourceConfigFieldInfo.fromJson(Map<String, dynamic> json) =>
      ResourceConfigFieldInfo(
        key: json['key'] as String,
        label: json['label'] as String,
        help: json['help'] as String? ?? '',
        required: json['required'] as bool? ?? true,
        defaultValue: json['default'] as String? ?? '',
      );

  final String key;
  final String label;
  final String help;
  final bool required;
  final String defaultValue;
}

/// 提供资源来源能力的插件
class ResourceProviderInfo {
  const ResourceProviderInfo({
    required this.pluginId,
    required this.name,
    required this.enabled,
    required this.multiple,
    required this.configFields,
    this.sourceCount = 0,
  });

  factory ResourceProviderInfo.fromJson(Map<String, dynamic> json) =>
      ResourceProviderInfo(
        pluginId: json['plugin_id'] as String,
        name: json['name'] as String,
        enabled: json['enabled'] as bool,
        multiple: json['multiple'] as bool,
        configFields: [
          for (final item in json['config_fields'] as List<dynamic>)
            ResourceConfigFieldInfo.fromJson(item as Map<String, dynamic>),
        ],
        sourceCount: json['source_count'] as int? ?? 0,
      );

  final String pluginId;
  final String name;
  final bool enabled;

  /// 能否建多个来源；有的插件全局只绑定一个账号
  final bool multiple;
  final List<ResourceConfigFieldInfo> configFields;
  final int sourceCount;

  /// 还能再新建来源
  bool get canCreate => enabled && (multiple || sourceCount == 0);
}

/// 来源里一个目录下的子目录
class SourceListing {
  const SourceListing({
    required this.locator,
    required this.path,
    required this.selectable,
    required this.entries,
  });

  factory SourceListing.fromJson(Map<String, dynamic> json) => SourceListing(
    locator: json['locator'] as String?,
    path: json['path'] as String,
    selectable: json['selectable'] as bool,
    entries: [
      for (final item in json['entries'] as List<dynamic>)
        SourceEntry(
          locator: (item as Map<String, dynamic>)['locator'] as String,
          name: item['name'] as String,
        ),
    ],
  );

  /// 目录的定位符；null 表示来源根目录
  final String? locator;

  /// 目录在来源里的路径，根目录为 /
  final String path;

  /// 这个目录能否作为资料库目录
  final bool selectable;
  final List<SourceEntry> entries;

  String childPath(SourceEntry entry) =>
      path == '/' ? '/${entry.name}' : '$path/${entry.name}';
}

class SourceEntry {
  const SourceEntry({required this.locator, required this.name});

  final String locator;
  final String name;
}

final resourceSourcesProvider =
    FutureProvider.autoDispose<List<ResourceSourceInfo>>((ref) async {
      final response = await ref
          .watch(apiClientProvider)
          .get<List<dynamic>>(resourcesPath);
      return [
        for (final item in response.data ?? const [])
          ResourceSourceInfo.fromJson(item as Map<String, dynamic>),
      ];
    });

final resourceProvidersProvider =
    FutureProvider.autoDispose<List<ResourceProviderInfo>>((ref) async {
      final response = await ref
          .watch(apiClientProvider)
          .get<List<dynamic>>('$resourcesPath/providers');
      return [
        for (final item in response.data ?? const [])
          ResourceProviderInfo.fromJson(item as Map<String, dynamic>),
      ];
    });

/// 浏览来源的目录；locator 为空时是来源的根目录
final sourceListingProvider = FutureProvider.autoDispose
    .family<SourceListing, ({int sourceId, String? locator})>((ref, key) async {
      final response = await ref
          .watch(apiClientProvider)
          .get<Map<String, dynamic>>(
            '$resourcesPath/${key.sourceId}/browse',
            queryParameters: {'locator': ?key.locator},
          );
      return SourceListing.fromJson(response.data!);
    });

Future<ResourceSourceInfo> createResourceSource(
  Dio dio, {
  required String pluginId,
  required String name,
  required Map<String, dynamic> config,
}) async {
  final response = await dio.post<Map<String, dynamic>>(
    resourcesPath,
    data: {'plugin_id': pluginId, 'name': name, 'config': config},
  );
  return ResourceSourceInfo.fromJson(response.data!);
}

Future<void> updateResourceSource(
  Dio dio,
  int sourceId, {
  String? name,
  Map<String, dynamic>? config,
}) async {
  await dio.patch<Map<String, dynamic>>(
    '$resourcesPath/$sourceId',
    data: {'name': ?name, 'config': ?config},
  );
}

Future<void> setResourceEnabled(Dio dio, int sourceId, bool enabled) async {
  await dio.put<Map<String, dynamic>>(
    '$resourcesPath/$sourceId/enabled',
    data: {'enabled': enabled},
  );
}

Future<void> deleteResourceSource(Dio dio, int sourceId) async {
  await dio.delete<void>('$resourcesPath/$sourceId');
}
