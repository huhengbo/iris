import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/paging.dart';
import '../../core/provider_cache.dart';
import '../../core/providers.dart';

part 'browse_detail_models.dart';
part 'browse_models.dart';
part 'library_hubs_models.dart';
part 'browse_query.dart';

/// 影视墙的数据：首页、海报墙、详情、演员页和搜索（ADR 0014、0017）。
/// 服务端已按当前用户的可见范围过滤，看不到的内容一律是 404。

// ---- 请求 ----

Future<T> _get<T>(
  Ref ref,
  String path,
  T Function(Map<String, dynamic> json, String baseUrl) parse, {
  Map<String, dynamic>? query,
}) async {
  final baseUrl = ref.watch(apiBaseUrlProvider);
  final response = await ref
      .watch(apiClientProvider)
      .get<Map<String, dynamic>>(path, queryParameters: query);
  return parse(response.data!, baseUrl);
}

final homeFeedProvider = FutureProvider.autoDispose<HomeFeed>((ref) {
  keepProviderAlive(ref);
  return _get(ref, '/api/home', HomeFeed.fromJson);
});

final librariesProvider = FutureProvider.autoDispose<List<LibrarySummary>>((
  ref,
) async {
  keepProviderAlive(ref);
  final baseUrl = ref.watch(apiBaseUrlProvider);
  final response = await ref
      .watch(apiClientProvider)
      .get<List<dynamic>>('/api/libraries');
  return [
    for (final item in response.data ?? const <dynamic>[])
      LibrarySummary.fromJson(item as Map<String, dynamic>, baseUrl),
  ];
});

final libraryProvider = FutureProvider.autoDispose.family<LibraryDetail, int>((
  ref,
  id,
) {
  keepProviderAlive(ref);
  return _get(ref, '/api/libraries/$id', LibraryDetail.fromJson);
});

/// 资料库「推荐」标签的推荐行；空行由服务端去掉。
final libraryHubsProvider = FutureProvider.autoDispose
    .family<List<HubRow>, int>((ref, id) async {
      keepProviderAlive(ref);
      final baseUrl = ref.watch(apiBaseUrlProvider);
      final response = await ref
          .watch(apiClientProvider)
          .get<List<dynamic>>('/api/libraries/$id/hubs');
      return [
        for (final item in response.data ?? const <dynamic>[])
          HubRow.fromJson(item as Map<String, dynamic>, baseUrl),
      ];
    });

final mediaDetailProvider = FutureProvider.autoDispose.family<MediaDetail, int>(
  (ref, id) {
    keepProviderAlive(ref);
    return _get(ref, '/api/items/$id', MediaDetail.fromJson);
  },
);

/// 一次性取详情，不经过 autoDispose 的 [mediaDetailProvider]。
/// 管理员的「编辑字幕」从海报卡也能打开，那里没有页面在监听 provider。
Future<MediaDetail> fetchMediaDetail(Dio dio, String baseUrl, int id) async {
  final response = await dio.get<Map<String, dynamic>>('/api/items/$id');
  return MediaDetail.fromJson(response.data!, baseUrl);
}

final seasonProvider = FutureProvider.autoDispose.family<SeasonDetail, int>((
  ref,
  id,
) {
  keepProviderAlive(ref);
  return _get(ref, '/api/seasons/$id', SeasonDetail.fromJson);
});

final personProvider = FutureProvider.autoDispose.family<PersonDetail, int>((
  ref,
  id,
) {
  keepProviderAlive(ref);
  return _get(ref, '/api/people/$id', PersonDetail.fromJson);
});
