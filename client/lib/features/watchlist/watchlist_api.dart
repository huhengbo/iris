import 'dart:math' as math;

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import '../library/browse_api.dart';

/// 稍后观看页每页的条数
const watchlistPageSize = 40;

class WatchlistSlice {
  const WatchlistSlice({required this.items, required this.total});

  final List<MediaCard> items;
  final int total;
}

/// 已经取到的稍后观看。滚到底部附近时取下一页，失败时停在原地等重试。
class WatchlistList {
  const WatchlistList({
    required this.items,
    required this.total,
    this.loadingMore = false,
    this.moreError,
  });

  final List<MediaCard> items;
  final int total;
  final bool loadingMore;
  final Object? moreError;

  bool get hasMore => items.length < total;
}

final watchlistApiProvider = Provider<WatchlistApi>(
  (ref) =>
      WatchlistApi(ref.watch(apiClientProvider), ref.watch(apiBaseUrlProvider)),
);

/// 稍后观看接口：加入/移出返回 204，列表返回 `{items, total}`，按加入时间倒序。
/// 只能放电影和整部剧集，由服务端校验。
class WatchlistApi {
  const WatchlistApi(this._dio, this._baseUrl);

  final Dio _dio;
  final String _baseUrl;

  Future<void> setWatchlisted(int itemId, {required bool watchlisted}) async {
    final path = '/api/items/$itemId/watchlist';
    if (watchlisted) {
      await _dio.put<void>(path);
    } else {
      await _dio.delete<void>(path);
    }
  }

  Future<WatchlistSlice> list({
    int offset = 0,
    int limit = watchlistPageSize,
  }) async {
    final response = await _dio.get<Map<String, dynamic>>(
      '/api/me/watchlist',
      queryParameters: {'limit': limit, 'offset': offset},
    );
    final data = response.data ?? const <String, dynamic>{};
    final raw = data['items'] as List<dynamic>? ?? const [];
    return WatchlistSlice(
      items: [
        for (final item in raw)
          MediaCard.fromJson(item as Map<String, dynamic>, _baseUrl),
      ],
      total: data['total'] as int? ?? raw.length,
    );
  }
}

final watchlistProvider =
    AsyncNotifierProvider.autoDispose<WatchlistNotifier, WatchlistList>(
      WatchlistNotifier.new,
    );

class WatchlistNotifier extends AsyncNotifier<WatchlistList> {
  @override
  Future<WatchlistList> build() async {
    final page = await ref.watch(watchlistApiProvider).list();
    return WatchlistList(items: page.items, total: page.total);
  }

  /// 取下一页。正在取、已经取完或上次失败（等 [retryMore]）时什么都不做。
  Future<void> loadMore() async {
    final current = state.value;
    if (current == null ||
        current.loadingMore ||
        current.moreError != null ||
        !current.hasMore) {
      return;
    }
    state = AsyncData(
      WatchlistList(
        items: current.items,
        total: current.total,
        loadingMore: true,
      ),
    );
    try {
      final page = await ref
          .read(watchlistApiProvider)
          .list(offset: current.items.length);
      if (!ref.mounted) return;
      state = AsyncData(
        WatchlistList(
          items: [...current.items, ...page.items],
          total: page.total,
        ),
      );
    } catch (error) {
      if (!ref.mounted) return;
      state = AsyncData(
        WatchlistList(
          items: current.items,
          total: current.total,
          moreError: error,
        ),
      );
    }
  }

  Future<void> retryMore() {
    final current = state.value;
    if (current != null) {
      state = AsyncData(
        WatchlistList(items: current.items, total: current.total),
      );
    }
    return loadMore();
  }

  /// 移出一条：先乐观地从列表里拿掉，请求失败时放回原来的位置并把错误抛给页面。
  Future<void> remove(MediaCard media) async {
    final current = state.value;
    if (current == null) return;
    final index = current.items.indexWhere((item) => item.id == media.id);
    if (index < 0) return;
    final items = [...current.items]..removeAt(index);
    state = AsyncData(
      WatchlistList(items: items, total: math.max(0, current.total - 1)),
    );
    try {
      await ref
          .read(watchlistApiProvider)
          .setWatchlisted(media.id, watchlisted: false);
      // 页面可能已经关掉，provider 已释放
      if (ref.mounted) _invalidate(media.id);
    } catch (error) {
      final now = state.value;
      if (ref.mounted &&
          now != null &&
          !now.items.any((m) => m.id == media.id)) {
        final restored = [...now.items]
          ..insert(math.min(index, now.items.length), media);
        state = AsyncData(WatchlistList(items: restored, total: now.total + 1));
      }
      rethrow;
    }
  }

  /// 撤销移出：重新加入，再拉一次列表让它回到原来的位置（服务端按加入时间倒序）。
  Future<void> restore(MediaCard media) async {
    await ref
        .read(watchlistApiProvider)
        .setWatchlisted(media.id, watchlisted: true);
    if (!ref.mounted) return;
    _invalidate(media.id);
    ref.invalidateSelf();
  }

  /// 加入或移出后，首页的稍后观看行、海报墙筛选和条目详情都可能变了。
  void _invalidate(int itemId) {
    ref
      ..invalidate(homeFeedProvider)
      ..invalidate(libraryItemsProvider)
      ..invalidate(mediaDetailProvider(itemId));
  }
}
