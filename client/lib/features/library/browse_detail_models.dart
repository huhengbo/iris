part of 'browse_api.dart';

/// 导演或演员。character 是演员饰演的角色。
class Credit {
  const Credit({
    required this.personId,
    required this.name,
    this.character,
    this.profile = Artwork.none,
  });

  factory Credit.fromJson(Map<String, dynamic> json, String baseUrl) => Credit(
    personId: json['person_id'] as int,
    name: json['name'] as String,
    character: json['character'] as String?,
    profile: Artwork.fromJson(json['profile'], baseUrl),
  );

  final int personId;
  final String name;
  final String? character;
  final Artwork profile;
}

class MediaVersion {
  const MediaVersion({
    required this.id,
    required this.label,
    this.size,
    required this.subtitles,
    this.media,
  });

  factory MediaVersion.fromJson(Map<String, dynamic> json) => MediaVersion(
    id: json['id'] as int,
    label: json['label'] as String,
    size: json['size'] as int?,
    subtitles: [
      for (final item in (json['subtitles'] as List<dynamic>))
        MediaSubtitle.fromJson(item as Map<String, dynamic>),
    ],
    media: switch (json['media']) {
      final Map<String, dynamic> media => MediaInfo.fromJson(media),
      _ => null,
    },
  );

  final int id;

  /// 服务端拼好的显示名，已含大小，如「2160p · WEB-DL · DV · 58 GB」
  final String label;
  final int? size;

  /// 外挂字幕
  final List<MediaSubtitle> subtitles;

  /// 内置播放器播过之后才有（ADR 0019）
  final MediaInfo? media;
}

/// 文件里实际的流信息，由内置播放器打开文件后上报。
class MediaInfo {
  const MediaInfo({
    this.width,
    this.height,
    this.videoCodec,
    this.hdr,
    this.audio = const [],
    this.subtitles = const [],
  });

  factory MediaInfo.fromJson(Map<String, dynamic> json) => MediaInfo(
    width: json['width'] as int?,
    height: json['height'] as int?,
    videoCodec: json['video_codec'] as String?,
    hdr: json['hdr'] as String?,
    audio: [
      for (final item in json['audio'] as List<dynamic>)
        MediaAudio.fromJson(item as Map<String, dynamic>),
    ],
    subtitles: [
      for (final item in json['subtitles'] as List<dynamic>)
        MediaEmbeddedSubtitle.fromJson(item as Map<String, dynamic>),
    ],
  );

  final int? width;
  final int? height;

  /// mpv 的编码名，如 hevc、h264
  final String? videoCodec;

  /// hdr10、hlg、dolby_vision；SDR 时为空
  final String? hdr;
  final List<MediaAudio> audio;

  /// 内封字幕
  final List<MediaEmbeddedSubtitle> subtitles;
}

class MediaAudio {
  const MediaAudio({
    this.language,
    this.title,
    this.codec,
    this.channels,
    this.isDefault = false,
  });

  factory MediaAudio.fromJson(Map<String, dynamic> json) => MediaAudio(
    language: json['language'] as String?,
    title: json['title'] as String?,
    codec: json['codec'] as String?,
    channels: json['channels'] as int?,
    isDefault: json['default'] as bool,
  );

  /// 片源里标的语言代码，如 chi、eng
  final String? language;
  final String? title;
  final String? codec;
  final int? channels;
  final bool isDefault;
}

class MediaEmbeddedSubtitle {
  const MediaEmbeddedSubtitle({
    this.language,
    this.title,
    this.codec,
    this.forced = false,
    this.isDefault = false,
  });

  factory MediaEmbeddedSubtitle.fromJson(Map<String, dynamic> json) =>
      MediaEmbeddedSubtitle(
        language: json['language'] as String?,
        title: json['title'] as String?,
        codec: json['codec'] as String?,
        forced: json['forced'] as bool,
        isDefault: json['default'] as bool,
      );

  final String? language;
  final String? title;
  final String? codec;
  final bool forced;
  final bool isDefault;
}

class MediaSubtitle {
  const MediaSubtitle({
    required this.id,
    required this.label,
    this.language,
    required this.forced,
    required this.source,
  });

  factory MediaSubtitle.fromJson(Map<String, dynamic> json) => MediaSubtitle(
    id: json['id'] as int,
    label: json['label'] as String,
    language: json['language'] as String?,
    forced: json['forced'] as bool,
    source: json['source'] as String,
  );

  final int id;
  final String label;
  final String? language;
  final bool forced;

  /// external 外挂字幕、online 服务端保存的在线字幕
  final String source;

  bool get isOnline => source == 'online';
}

List<MediaVersion> _versions(Object? json) => [
  for (final item in json as List<dynamic>)
    MediaVersion.fromJson(item as Map<String, dynamic>),
];

String seasonLabel(int number) => number == 0 ? '特别篇' : '第 $number 季';

class SeasonSummary {
  const SeasonSummary({
    required this.id,
    required this.number,
    this.name,
    required this.episodeCount,
  });

  factory SeasonSummary.fromJson(Map<String, dynamic> json) => SeasonSummary(
    id: json['id'] as int,
    number: json['number'] as int,
    name: json['name'] as String?,
    episodeCount: json['episode_count'] as int,
  );

  final int id;
  final int number;
  final String? name;

  /// 有文件的集数
  final int episodeCount;

  String get label => seasonLabel(number);
}

/// 某一集的位置：剧集详情里接着看的一集，或首页最近播放到的一集。
class EpisodeRef {
  const EpisodeRef({
    required this.id,
    required this.seasonId,
    required this.seasonNumber,
    required this.number,
    this.progress,
  });

  factory EpisodeRef.fromJson(Map<String, dynamic> json) => EpisodeRef(
    id: json['id'] as int,
    seasonId: json['season_id'] as int,
    seasonNumber: json['season_number'] as int,
    number: json['number'] as int,
    progress: PlayProgress.fromJson(json['progress']),
  );

  final int id;
  final int seasonId;
  final int seasonNumber;
  final int number;
  final PlayProgress? progress;
}

/// 详情里的一个评分来源。label 由服务端按插件给出，如 TMDB、豆瓣。
class SourceRating {
  const SourceRating({
    required this.source,
    required this.label,
    required this.value,
  });

  factory SourceRating.fromJson(Map<String, dynamic> json) => SourceRating(
    source: json['source'] as String,
    label: json['label'] as String,
    value: (json['value'] as num).toDouble(),
  );

  /// 插件 ID，如 builtin.tmdb
  final String source;
  final String label;

  /// 0–10
  final double value;
}

class MediaDetail {
  const MediaDetail({
    required this.card,
    this.overview,
    this.contentRating,
    this.releaseDate,
    required this.studios,
    required this.countries,
    required this.directors,
    required this.writers,
    required this.cast,
    required this.versions,
    required this.seasons,
    required this.ratings,
    this.nextEpisode,
    this.progress,
    required this.watchlisted,
    required this.favorite,
  });

  factory MediaDetail.fromJson(Map<String, dynamic> json, String baseUrl) =>
      MediaDetail(
        card: MediaCard.fromJson(json, baseUrl),
        overview: json['overview'] as String?,
        contentRating: json['content_rating'] as String?,
        releaseDate: json['release_date'] as String?,
        studios: _strings(json['studios']),
        countries: _strings(json['countries']),
        directors: [
          for (final item in json['directors'] as List<dynamic>)
            Credit.fromJson(item as Map<String, dynamic>, baseUrl),
        ],
        writers: [
          for (final item in json['writers'] as List<dynamic>)
            Credit.fromJson(item as Map<String, dynamic>, baseUrl),
        ],
        cast: [
          for (final item in json['cast'] as List<dynamic>)
            Credit.fromJson(item as Map<String, dynamic>, baseUrl),
        ],
        versions: _versions(json['versions']),
        seasons: [
          for (final item in json['seasons'] as List<dynamic>)
            SeasonSummary.fromJson(item as Map<String, dynamic>),
        ],
        ratings: [
          for (final item in json['ratings'] as List<dynamic>)
            SourceRating.fromJson(item as Map<String, dynamic>),
        ],
        nextEpisode: json['next_episode'] == null
            ? null
            : EpisodeRef.fromJson(json['next_episode'] as Map<String, dynamic>),
        progress: PlayProgress.fromJson(json['progress']),
        watchlisted: json['watchlisted'] as bool,
        favorite: json['favorite'] as bool,
      );

  final MediaCard card;
  final String? overview;

  /// 内容分级，如 TV-MA、PG-13，只用于展示
  final String? contentRating;

  /// 上映日期，`YYYY-MM-DD`
  final String? releaseDate;

  /// 制作公司和制作国家，只用于展示
  final List<String> studios;
  final List<String> countries;
  final List<Credit> directors;
  final List<Credit> writers;
  final List<Credit> cast;

  /// 电影的版本，大的在前；剧集的版本在每一集上
  final List<MediaVersion> versions;

  /// 特别篇在最后
  final List<SeasonSummary> seasons;

  /// 按主插件、资料库元数据下载器顺序列出；没有评分的来源不在里面
  final List<SourceRating> ratings;
  final EpisodeRef? nextEpisode;

  /// 电影的续播位置；剧集看 nextEpisode 的
  final PlayProgress? progress;

  /// 当前用户已经把这部电影或整部剧集加入稍后观看
  final bool watchlisted;

  /// 当前用户收藏了这部电影或剧集
  final bool favorite;

  int get episodeCount => seasons.fold(0, (sum, s) => sum + s.episodeCount);
}

class Episode {
  const Episode({
    required this.id,
    required this.number,
    this.name,
    this.overview,
    this.airDate,
    this.runtime,
    this.rating,
    this.still = Artwork.none,
    this.versions = const [],
    this.watched = false,
    this.progress,
    required this.favorite,
  });

  factory Episode.fromJson(Map<String, dynamic> json, String baseUrl) =>
      Episode(
        id: json['id'] as int,
        number: json['number'] as int,
        name: json['name'] as String?,
        overview: json['overview'] as String?,
        airDate: json['air_date'] as String?,
        runtime: json['runtime'] as int?,
        rating: (json['rating'] as num?)?.toDouble(),
        still: Artwork.fromJson(json['still'], baseUrl),
        versions: _versions(json['versions']),
        watched: json['watched'] as bool,
        progress: PlayProgress.fromJson(json['progress']),
        favorite: json['favorite'] as bool,
      );

  final int id;
  final int number;
  final String? name;
  final String? overview;

  /// 首播日期，`YYYY-MM-DD`
  final String? airDate;
  final int? runtime;

  /// TMDB 评分
  final double? rating;
  final Artwork still;

  /// 大的在前
  final List<MediaVersion> versions;
  final bool watched;
  final PlayProgress? progress;

  /// 当前用户收藏了这一集
  final bool favorite;
}

class SeasonDetail {
  const SeasonDetail({
    required this.id,
    required this.itemId,
    required this.number,
    required this.episodes,
  });

  factory SeasonDetail.fromJson(Map<String, dynamic> json, String baseUrl) =>
      SeasonDetail(
        id: json['id'] as int,
        itemId: json['item_id'] as int,
        number: json['number'] as int,
        episodes: [
          for (final item in json['episodes'] as List<dynamic>)
            Episode.fromJson(item as Map<String, dynamic>, baseUrl),
        ],
      );

  final int id;

  /// 这一季所属的剧集
  final int itemId;
  final int number;
  final List<Episode> episodes;
}

class PersonWork {
  const PersonWork({
    required this.card,
    required this.directed,
    required this.acted,
    this.wrote = false,
    this.character,
  });

  factory PersonWork.fromJson(Map<String, dynamic> json, String baseUrl) {
    final roles = _strings(json['roles']);
    return PersonWork(
      card: MediaCard.fromJson(json, baseUrl),
      directed: roles.contains('director'),
      acted: roles.contains('cast'),
      wrote: roles.contains('writer'),
      character: json['character'] as String?,
    );
  }

  final MediaCard card;
  final bool directed;
  final bool acted;
  final bool wrote;
  final String? character;

  /// 海报下的一行：饰演的角色，或「导演」「编剧」
  String? get role => [
    if (directed) '导演',
    if (wrote) '编剧',
    if (acted && character != null) '饰 $character',
  ].join(' · ').nullIfEmpty;
}

extension on String {
  String? get nullIfEmpty => isEmpty ? null : this;
}

class PersonDetail {
  const PersonDetail({
    required this.id,
    required this.name,
    this.originalName,
    this.biography,
    this.birthday,
    this.deathday,
    this.placeOfBirth,
    this.profile = Artwork.none,
    this.works = const [],
    required this.favorite,
  });

  factory PersonDetail.fromJson(Map<String, dynamic> json, String baseUrl) =>
      PersonDetail(
        id: json['id'] as int,
        name: json['name'] as String,
        originalName: json['original_name'] as String?,
        biography: json['biography'] as String?,
        birthday: json['birthday'] as String?,
        deathday: json['deathday'] as String?,
        placeOfBirth: json['place_of_birth'] as String?,
        profile: Artwork.fromJson(json['profile'], baseUrl),
        works: [
          for (final item in json['works'] as List<dynamic>)
            PersonWork.fromJson(item as Map<String, dynamic>, baseUrl),
        ],
        favorite: json['favorite'] as bool,
      );

  final int id;
  final String name;
  final String? originalName;
  final String? biography;

  /// YYYY-MM-DD
  final String? birthday;
  final String? deathday;
  final String? placeOfBirth;
  final Artwork profile;

  /// 可见范围内的作品，按年份从新到旧
  final List<PersonWork> works;

  /// 当前用户收藏了这位人物
  final bool favorite;
}
