import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';

const _librariesPath = '/api/admin/libraries';

final libraryAdvancedSettingsProvider = FutureProvider.autoDispose
    .family<Map<String, String>, int>((ref, id) async {
      final response = await ref
          .watch(apiClientProvider)
          .get<Map<String, dynamic>>('$_librariesPath/$id/settings');
      return {
        for (final entry in (response.data ?? const {}).entries)
          entry.key: '${entry.value}',
      };
    });

final libraryAdvancedStatsProvider = FutureProvider.autoDispose
    .family<Map<String, dynamic>, int>((ref, id) async {
      final response = await ref
          .watch(apiClientProvider)
          .get<Map<String, dynamic>>('$_librariesPath/$id/stats');
      return response.data ?? const {};
    });

/// 资料库的推荐行配置。管理员还没保存过时返回的是默认布局（id 为 0）。
class AdminHub {
  const AdminHub({
    required this.id,
    required this.kind,
    required this.arg,
    required this.position,
    required this.showHome,
    required this.showLibrary,
    required this.title,
  });

  factory AdminHub.fromJson(Map<String, dynamic> json) => AdminHub(
    id: json['id'] as int,
    kind: json['kind'] as String,
    arg: json['arg'] as String?,
    position: json['position'] as int,
    showHome: json['show_home'] as bool,
    showLibrary: json['show_library'] as bool,
    title: json['title'] as String,
  );

  final int id;
  final String kind;
  final String? arg;
  final int position;
  final bool showHome;
  final bool showLibrary;

  /// 客户端不自己拼标题，和服务端、首页保持一致
  final String title;

  AdminHub copyWith({bool? showHome, bool? showLibrary}) => AdminHub(
    id: id,
    kind: kind,
    arg: arg,
    position: position,
    showHome: showHome ?? this.showHome,
    showLibrary: showLibrary ?? this.showLibrary,
    title: title,
  );

  Map<String, Object?> toRequest() => {
    'kind': kind,
    'arg': arg,
    'show_home': showHome,
    'show_library': showLibrary,
  };
}

final libraryHubsAdminProvider = FutureProvider.autoDispose
    .family<List<AdminHub>, int>((ref, id) async {
      final response = await ref
          .watch(apiClientProvider)
          .get<List<dynamic>>('$_librariesPath/$id/hubs');
      return [
        for (final item in response.data ?? const <dynamic>[])
          AdminHub.fromJson(item as Map<String, dynamic>),
      ];
    });

Future<void> refreshLibraryMetadata(Dio dio, int id) =>
    dio.post<void>('$_librariesPath/$id/refresh-metadata');

Future<List<AdminHub>> saveLibraryHubs(
  Dio dio,
  int id,
  List<AdminHub> rows,
) async {
  final response = await dio.put<List<dynamic>>(
    '$_librariesPath/$id/hubs',
    data: [for (final row in rows) row.toRequest()],
  );
  return [
    for (final item in response.data ?? const <dynamic>[])
      AdminHub.fromJson(item as Map<String, dynamic>),
  ];
}

Future<void> saveLibrarySetting(Dio dio, int id, String key, String value) =>
    dio.put<void>('$_librariesPath/$id/settings/$key', data: {'value': value});
