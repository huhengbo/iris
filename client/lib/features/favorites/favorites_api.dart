import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/paging.dart';
import '../../core/format.dart';
import '../../core/providers.dart';
import '../library/browse_api.dart';

/// 收藏页每页的条数
const favoritesPageSize = 40;

/// 收藏的目标类型，和 `/api/me/favorites` 的 kind 参数一致。
/// 电影和剧集都是 `media_item`，收藏接口都走 `/api/items/{id}/favorite`。
enum FavoriteKind {
  movie('电影', 'items'),
  series('剧集', 'items'),
  episode('单集', 'episodes'),
  person('人物', 'people');

  const FavoriteKind(this.label, this.target);

  /// 收藏页的分组名
  final String label;

  /// 收藏接口里的资源段：items / episodes / people
  final String target;

  static FavoriteKind? parse(String? value) =>
      values.where((kind) => kind.name == value).firstOrNull;
}

/// 收藏页的一条。电影的卡片沿用影视墙，单集沿用首页「继续观看」，
/// 人物沿用演员卡片。
sealed class FavoriteEntry {
  const FavoriteEntry();

  int get id;
  String get title;
}

class FavoriteMediaEntry extends FavoriteEntry {
  const FavoriteMediaEntry(this.card);

  final MediaCard card;

  @override
  int get id => card.id;

  @override
  String get title => card.title;
}

class FavoriteEpisodeEntry extends FavoriteEntry {
  const FavoriteEpisodeEntry({
    required this.item,
    required this.episode,
    required this.still,
    this.name,
  });

  factory FavoriteEpisodeEntry.fromJson(
    Map<String, dynamic> json,
    String baseUrl,
  ) {
    final episode = json['episode'] as Map<String, dynamic>;
    return FavoriteEpisodeEntry(
      item: MediaCard.fromJson(json['item'] as Map<String, dynamic>, baseUrl),
      episode: EpisodeRef.fromJson(episode),
      still: Artwork.fromJson(json['still'], baseUrl),
      name: episode['name'] as String?,
    );
  }

  /// 所属剧集
  final MediaCard item;
  final EpisodeRef episode;

  /// 剧照；没有时详情页会用剧集的海报
  final Artwork still;
  final String? name;

  @override
  int get id => episode.id;

  @override
  String get title => item.title;

  /// 「第 1 季 第 2 集」
  String get label => formatEpisode(episode.seasonNumber, episode.number);
}

class FavoritePersonEntry extends FavoriteEntry {
  const FavoritePersonEntry({
    required this.id,
    required this.name,
    this.originalName,
    this.profile = Artwork.none,
  });

  factory FavoritePersonEntry.fromJson(
    Map<String, dynamic> json,
    String baseUrl,
  ) => FavoritePersonEntry(
    id: json['id'] as int,
    name: json['name'] as String,
    originalName: json['original_name'] as String?,
    profile: Artwork.fromJson(json['profile'], baseUrl),
  );

  @override
  final int id;

  final String name;
  final String? originalName;
  final Artwork profile;

  @override
  String get title => name;
}

FavoriteEntry _entryFromJson(
  FavoriteKind kind,
  Map<String, dynamic> json,
  String baseUrl,
) => switch (kind) {
  FavoriteKind.movie ||
  FavoriteKind.series => FavoriteMediaEntry(MediaCard.fromJson(json, baseUrl)),
  FavoriteKind.episode => FavoriteEpisodeEntry.fromJson(json, baseUrl),
  FavoriteKind.person => FavoritePersonEntry.fromJson(json, baseUrl),
};

class FavoritePage {
  const FavoritePage({required this.items, required this.total});

  final List<FavoriteEntry> items;
  final int total;
}

/// 已经取到的收藏。滚到底部附近时取下一页，失败时停在原地等重试。
class FavoriteList {
  const FavoriteList({
    required this.items,
    required this.total,
    this.loadingMore = false,
    this.moreError,
  });

  final List<FavoriteEntry> items;
  final int total;
  final bool loadingMore;
  final Object? moreError;

  bool get hasMore => items.length < total;
}

final favoritesApiProvider = Provider<FavoritesApi>(
  (ref) =>
      FavoritesApi(ref.watch(apiClientProvider), ref.watch(apiBaseUrlProvider)),
);

/// 我的收藏接口（B1）：增删返回 204，列表返回 `{items, total}`。
class FavoritesApi {
  const FavoritesApi(this._dio, this._baseUrl);

  final Dio _dio;
  final String _baseUrl;

  Future<void> setFavorite(
    FavoriteKind kind,
    int id, {
    required bool favorite,
  }) async {
    final path = '/api/${kind.target}/$id/favorite';
    if (favorite) {
      await _dio.put<void>(path);
    } else {
      await _dio.delete<void>(path);
    }
  }

  Future<FavoritePage> list(
    FavoriteKind kind, {
    int offset = 0,
    int limit = favoritesPageSize,
  }) async {
    final response = await _dio.get<Map<String, dynamic>>(
      '/api/me/favorites',
      queryParameters: {'kind': kind.name, 'offset': offset, 'limit': limit},
    );
    final data = response.data ?? const <String, dynamic>{};
    final raw = data['items'] as List<dynamic>? ?? const [];
    return FavoritePage(
      items: [
        for (final item in raw)
          _entryFromJson(kind, item as Map<String, dynamic>, _baseUrl),
      ],
      total: data['total'] as int? ?? raw.length,
    );
  }
}

final favoritesProvider = AsyncNotifierProvider.autoDispose
    .family<FavoritesNotifier, FavoriteList, FavoriteKind>(
      FavoritesNotifier.new,
    );

class FavoritesNotifier extends AsyncNotifier<FavoriteList> {
  FavoritesNotifier(this.kind);

  final FavoriteKind kind;

  @override
  Future<FavoriteList> build() async {
    final page = await ref.watch(favoritesApiProvider).list(kind);
    return FavoriteList(items: page.items, total: page.total);
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
      FavoriteList(
        items: current.items,
        total: current.total,
        loadingMore: true,
      ),
    );
    try {
      final page = await ref
          .read(favoritesApiProvider)
          .list(kind, offset: current.items.length);
      if (!ref.mounted) return;
      state = AsyncData(
        FavoriteList(
          items: [...current.items, ...page.items],
          total: page.total,
        ),
      );
    } catch (error) {
      if (!ref.mounted) return;
      state = AsyncData(
        FavoriteList(
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
        FavoriteList(items: current.items, total: current.total),
      );
    }
    return loadMore();
  }
}
