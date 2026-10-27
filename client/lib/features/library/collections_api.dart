import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import 'browse_api.dart';

final libraryCollectionsProvider = FutureProvider.autoDispose
    .family<List<dynamic>, int>((ref, libraryId) {
      return ref.watch(collectionsApiProvider).inLibrary(libraryId);
    });

/// 合集详情：成员是和影视墙一样的海报卡片。
final collectionDetailProvider = FutureProvider.autoDispose
    .family<({String title, String? overview, List<MediaCard> items}), int>((
      ref,
      collectionId,
    ) {
      final baseUrl = ref.watch(apiBaseUrlProvider);
      return ref.watch(collectionsApiProvider).detail(collectionId, baseUrl);
    });

final collectionsApiProvider = Provider<CollectionsApi>(
  (ref) => CollectionsApi(ref.watch(apiClientProvider)),
);

class CollectionsApi {
  const CollectionsApi(this._dio);

  final Dio _dio;

  Future<List<dynamic>> inLibrary(int libraryId) async {
    final response = await _dio.get<List<dynamic>>(
      '/api/libraries/$libraryId/collections',
    );
    return response.data ?? const [];
  }

  Future<({String title, String? overview, List<MediaCard> items})> detail(
    int collectionId,
    String baseUrl,
  ) async {
    final response = await _dio.get<Map<String, dynamic>>(
      '/api/collections/$collectionId',
    );
    final data = response.data ?? const {};
    final collection = data['collection'] as Map<String, dynamic>? ?? const {};
    return (
      title: collection['title'] as String? ?? '合集',
      overview: (collection['overview'] as String?)?.trim(),
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
}
