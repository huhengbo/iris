import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';

final playlistsProvider = FutureProvider.autoDispose<List<dynamic>>((ref) {
  return ref.watch(playlistsApiProvider).list();
});

final playlistDetailProvider = FutureProvider.autoDispose
    .family<Map<String, dynamic>, int>((ref, id) {
      return ref.watch(playlistsApiProvider).detail(id);
    });

final playlistsApiProvider = Provider<PlaylistsApi>(
  (ref) => PlaylistsApi(ref.watch(apiClientProvider)),
);

/// 播放列表相关的接口。
class PlaylistsApi {
  const PlaylistsApi(this._dio);

  final Dio _dio;

  Future<List<dynamic>> list() async {
    final response = await _dio.get<List<dynamic>>('/api/playlists');
    return response.data ?? const [];
  }

  Future<Map<String, dynamic>> detail(int id) async {
    final response = await _dio.get<Map<String, dynamic>>('/api/playlists/$id');
    return response.data ?? const {};
  }

  /// 新建列表，返回新列表的 id。
  Future<int> create(String title) async {
    final response = await _dio.post<Map<String, dynamic>>(
      '/api/playlists',
      data: {'title': title, 'shared': false},
    );
    return response.data!['id'] as int;
  }

  Future<void> rename(int id, String title) =>
      _dio.patch<void>('/api/playlists/$id', data: {'title': title});

  Future<void> delete(Object? id) => _dio.delete<void>('/api/playlists/$id');

  /// 整体覆盖列表里的条目顺序。
  Future<void> setItems(int id, List<Object?> playableIds) => _dio.put<void>(
    '/api/playlists/$id/items',
    data: {'item_ids': playableIds},
  );

  Future<void> addItem(int id, {required int itemId, int? episodeId}) =>
      _dio.post<void>(
        '/api/playlists/$id/items',
        data: {'item_id': itemId, 'episode_id': ?episodeId},
      );
}
