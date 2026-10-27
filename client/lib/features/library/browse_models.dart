part of 'browse_api.dart';

/// 海报墙和搜索每次取的条数
const browsePageSize = 60;

enum MediaKind { movie, series }

MediaKind _kind(Object? json) =>
    json == 'series' ? MediaKind.series : MediaKind.movie;

List<String> _strings(Object? json) => [
  for (final item in json as List<dynamic>) item as String,
];

class LibrarySummary {
  const LibrarySummary({
    required this.id,
    required this.name,
    required this.kind,
    required this.itemCount,
    required this.cover,
  });

  /// [baseUrl] 用来把 cover 里的相对地址拼成完整地址。
  factory LibrarySummary.fromJson(
    Map<String, dynamic> json, [
    String baseUrl = '',
  ]) => LibrarySummary(
    id: json['id'] as int,
    name: json['name'] as String,
    kind: _kind(json['kind']),
    itemCount: json['item_count'] as int,
    cover: Artwork.fromJson(json['cover'], baseUrl),
  );

  final int id;
  final String name;
  final MediaKind kind;
  final int itemCount;

  /// 横版 16:9 封面，没有图片时为空
  final Artwork cover;
}

/// 海报墙页头：资料库和可选的类型、年份（只含这个资料库里出现过的）。
class LibraryDetail {
  const LibraryDetail({
    required this.library,
    required this.genres,
    required this.years,
    required this.categories,
  });

  factory LibraryDetail.fromJson(
    Map<String, dynamic> json, [
    String baseUrl = '',
  ]) => LibraryDetail(
    library: LibrarySummary.fromJson(json, baseUrl),
    genres: _strings(json['genres']),
    years: [for (final y in json['years'] as List<dynamic>) y as int],
    categories: [
      for (final item in json['categories'] as List<dynamic>)
        CategoryCard.fromJson(item as Map<String, dynamic>, baseUrl),
    ],
  );

  final LibrarySummary library;
  final List<String> genres;

  /// 从新到旧
  final List<int> years;

  /// 「分类」标签的类型卡片，按条目数从多到少
  final List<CategoryCard> categories;
}

/// 海报卡：首页、海报墙、搜索和演员页共用。
class MediaCard {
  const MediaCard({
    required this.id,
    required this.libraryId,
    required this.kind,
    required this.title,
    this.originalTitle,
    this.year,
    this.rating,
    this.genres = const [],
    this.runtime,
    this.poster = Artwork.none,
    this.backdrop = Artwork.none,
    this.watched = false,
  });

  factory MediaCard.fromJson(Map<String, dynamic> json, String baseUrl) =>
      MediaCard(
        id: json['id'] as int,
        libraryId: json['library_id'] as int,
        kind: _kind(json['kind']),
        title: json['title'] as String,
        originalTitle: json['original_title'] as String?,
        year: json['year'] as int?,
        rating: (json['rating'] as num?)?.toDouble(),
        genres: _strings(json['genres']),
        runtime: json['runtime'] as int?,
        poster: Artwork.fromJson(json['poster'], baseUrl),
        backdrop: Artwork.fromJson(json['backdrop'], baseUrl),
        watched: json['watched'] as bool,
      );

  final int id;
  final int libraryId;
  final MediaKind kind;
  final String title;
  final String? originalTitle;
  final int? year;

  /// TMDB 评分，0–10
  final double? rating;
  final List<String> genres;

  /// 分钟；剧集为空
  final int? runtime;
  final Artwork poster;
  final Artwork backdrop;

  /// 当前用户看过：电影标记了已看，剧集的每一集都标记了已看
  final bool watched;
}

class SearchPersonResult {
  const SearchPersonResult({
    required this.id,
    required this.name,
    this.originalName,
    this.profile = Artwork.none,
  });

  factory SearchPersonResult.fromJson(
    Map<String, dynamic> json,
    String baseUrl,
  ) => SearchPersonResult(
    id: json['id'] as int,
    name: json['name'] as String,
    originalName: json['original_name'] as String?,
    profile: Artwork.fromJson(json['profile'], baseUrl),
  );

  final int id;
  final String name;
  final String? originalName;
  final Artwork profile;
}

class SearchEpisodeResult {
  const SearchEpisodeResult({
    required this.id,
    required this.itemId,
    required this.seasonId,
    required this.seasonNumber,
    required this.number,
    required this.showTitle,
    this.name,
    this.still = Artwork.none,
  });

  factory SearchEpisodeResult.fromJson(
    Map<String, dynamic> json,
    String baseUrl,
  ) => SearchEpisodeResult(
    id: json['id'] as int,
    itemId: json['item_id'] as int,
    seasonId: json['season_id'] as int,
    seasonNumber: json['season_number'] as int,
    number: json['number'] as int,
    name: json['name'] as String?,
    showTitle: json['show_title'] as String,
    still: Artwork.fromJson(json['still'], baseUrl),
  );

  final int id;
  final int itemId;
  final int seasonId;
  final int seasonNumber;
  final int number;
  final String? name;
  final String showTitle;
  final Artwork still;
}

class SearchDiscovery {
  const SearchDiscovery({required this.people, required this.episodes});

  factory SearchDiscovery.fromJson(Map<String, dynamic> json, String baseUrl) =>
      SearchDiscovery(
        people: [
          for (final value in json['people'] as List<dynamic>)
            SearchPersonResult.fromJson(value as Map<String, dynamic>, baseUrl),
        ],
        episodes: [
          for (final value in json['episodes'] as List<dynamic>)
            SearchEpisodeResult.fromJson(
              value as Map<String, dynamic>,
              baseUrl,
            ),
        ],
      );

  final List<SearchPersonResult> people;
  final List<SearchEpisodeResult> episodes;
}

/// App 内置播放器上报的续播位置。只有 App 播放过、没看完才有。
class PlayProgress {
  const PlayProgress({required this.position, required this.duration});

  static PlayProgress? fromJson(Object? json) {
    if (json is! Map<String, dynamic>) return null;
    return PlayProgress(
      position: Duration(seconds: json['position'] as int),
      duration: Duration(seconds: json['duration'] as int),
    );
  }

  final Duration position;
  final Duration duration;

  double get fraction =>
      (position.inSeconds / duration.inSeconds).clamp(0.0, 1.0).toDouble();

  /// 剩余分钟数，向上取整，至少 1 分钟
  int get remainingMinutes {
    final seconds = (duration - position).inSeconds;
    return seconds <= 0 ? 1 : (seconds / 60).ceil();
  }
}

/// 首页「最近播放」的一条：每部作品一条，剧集带最近播放的那一集。
class RecentPlay {
  const RecentPlay({
    required this.item,
    this.episode,
    this.still = Artwork.none,
    this.progress,
  });

  factory RecentPlay.fromJson(Map<String, dynamic> json, String baseUrl) =>
      RecentPlay(
        item: MediaCard.fromJson(json['item'] as Map<String, dynamic>, baseUrl),
        episode: json['episode'] == null
            ? null
            : EpisodeRef.fromJson(json['episode'] as Map<String, dynamic>),
        still: Artwork.fromJson(json['still'], baseUrl),
        progress: PlayProgress.fromJson(json['progress']),
      );

  final MediaCard item;
  final EpisodeRef? episode;

  /// 那一集的剧照；电影为空
  final Artwork still;

  /// 电影或那一集的续播位置；有它时首页这一行叫「继续观看」
  final PlayProgress? progress;
}

class HomeFeed {
  const HomeFeed({
    required this.libraries,
    required this.recent,
    required this.favorites,
    required this.watchlist,
    required this.shelves,
  });

  factory HomeFeed.fromJson(Map<String, dynamic> json, String baseUrl) {
    return HomeFeed(
      libraries: [
        for (final item in json['libraries'] as List<dynamic>)
          LibrarySummary.fromJson(item as Map<String, dynamic>, baseUrl),
      ],
      recent: [
        for (final item in json['recent'] as List<dynamic>)
          RecentPlay.fromJson(item as Map<String, dynamic>, baseUrl),
      ],
      favorites: [
        for (final item in json['favorites'] as List<dynamic>)
          MediaCard.fromJson(item as Map<String, dynamic>, baseUrl),
      ],
      watchlist: [
        for (final item in json['watchlist'] as List<dynamic>)
          MediaCard.fromJson(item as Map<String, dynamic>, baseUrl),
      ],
      shelves: [
        for (final item in json['shelves'] as List<dynamic>)
          HubShelf.fromJson(item as Map<String, dynamic>, baseUrl),
      ],
    );
  }

  final List<LibrarySummary> libraries;

  /// 当前用户最近播放的作品，新的在前
  final List<RecentPlay> recent;

  /// 最近收藏的电影和剧集，新的在前
  final List<MediaCard> favorites;

  /// 稍后观看的电影和剧集，最多 20 条，新的在前
  final List<MediaCard> watchlist;

  /// 各资料库配置为在首页显示的推荐行
  final List<HubShelf> shelves;

  bool get isEmpty =>
      recent.isEmpty &&
      favorites.isEmpty &&
      watchlist.isEmpty &&
      shelves.every((shelf) => shelf.hub.isEmpty);
}
