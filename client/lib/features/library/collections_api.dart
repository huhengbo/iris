import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/paging.dart';
import '../../core/providers.dart';
import 'browse_api.dart';

final libraryCollectionsProvider = FutureProvider.autoDispose
    .family<List<CollectionSummary>, int>((ref, libraryId) {
      final baseUrl = ref.watch(apiBaseUrlProvider);
      return ref.watch(collectionsApiProvider).inLibrary(libraryId, baseUrl);
    });

/// 合集列表里的一项：封面是第一个有海报的成员的海报，成员数只算当前用户看得到的。
class CollectionSummary {
  const CollectionSummary({
    required this.id,
    required this.title,
    this.overview,
    this.itemCount = 0,
    this.cover = Artwork.none,
  });

  factory CollectionSummary.fromJson(
    Map<String, dynamic> json,
    String baseUrl,
  ) => CollectionSummary(
    id: json['id'] as int,
    title: json['title'] as String? ?? '未命名合集',
    overview: (json['overview'] as String?)?.trim(),
    itemCount: json['item_count'] as int? ?? 0,
    cover: Artwork.fromJson(json['cover'], baseUrl),
  );

  final int id;
  final String title;
  final String? overview;
  final int itemCount;
  final Artwork cover;
}

/// 合集详情：成员是和影视墙一样的海报卡片。
final collectionDetailProvider = FutureProvider.autoDispose
    .family<CollectionDetail, int>((ref, collectionId) {
      final baseUrl = ref.watch(apiBaseUrlProvider);
      return ref.watch(collectionsApiProvider).detail(collectionId, baseUrl);
    });

class CollectionDetail {
  const CollectionDetail({
    required this.id,
    required this.libraryId,
    required this.title,
    this.overview,
    this.cover = Artwork.none,
    this.backdrop = Artwork.none,
    this.coverItemId,
    this.items = const [],
  });

  final int id;
  final int libraryId;
  final String title;
  final String? overview;

  /// 第一个有海报的成员，或合集自己的海报
  final Artwork cover;

  /// 头图：合集自己的背景图，没有时用成员的
  final Artwork backdrop;

  /// 管理员指定作封面的成员，没有指定时为空
  final int? coverItemId;
  final List<MediaCard> items;
}

final collectionsApiProvider = Provider<CollectionsApi>(
  (ref) => CollectionsApi(ref.watch(apiClientProvider)),
);

class CollectionsApi {
  const CollectionsApi(this._dio);

  final Dio _dio;

  Future<List<CollectionSummary>> inLibrary(
    int libraryId,
    String baseUrl,
  ) async {
    final response = await _dio.get<List<dynamic>>(
      '/api/libraries/$libraryId/collections',
    );
    return [
      for (final raw in response.data ?? const [])
        CollectionSummary.fromJson(raw as Map<String, dynamic>, baseUrl),
    ];
  }

  Future<CollectionDetail> detail(int collectionId, String baseUrl) async {
    final response = await _dio.get<Map<String, dynamic>>(
      '/api/collections/$collectionId',
    );
    final data = response.data ?? const {};
    final collection = data['collection'] as Map<String, dynamic>? ?? const {};
    final overview = (collection['overview'] as String?)?.trim();
    return CollectionDetail(
      id: collectionId,
      libraryId: collection['library_id'] as int? ?? 0,
      title: collection['title'] as String? ?? '合集',
      overview: overview == null || overview.isEmpty ? null : overview,
      cover: Artwork.fromJson(collection['cover'], baseUrl),
      backdrop: Artwork.fromJson(collection['backdrop'], baseUrl),
      coverItemId: collection['cover_item_id'] as int?,
      items: [
        for (final raw in (data['items'] as List<dynamic>?) ?? const [])
          MediaCard.fromJson(raw as Map<String, dynamic>, baseUrl),
      ],
    );
  }

  Future<void> create(int libraryId, String title) => _dio.post<void>(
    '/api/admin/collections',
    data: {'library_id': libraryId, 'title': title},
  );

  Future<void> removeItem(int collectionId, int itemId) =>
      _dio.delete<void>('/api/admin/collections/$collectionId/items/$itemId');

  /// 指定用某个成员的海报作封面；[itemId] 为空时恢复默认。
  Future<void> setCover(int collectionId, int? itemId) => _dio.put<void>(
    '/api/admin/collections/$collectionId/cover',
    data: {'item_id': itemId},
  );
}
