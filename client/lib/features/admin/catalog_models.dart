part of 'catalog_api.dart';

class ItemRef {
  const ItemRef({
    required this.id,
    required this.providerId,
    required this.remoteId,
    required this.title,
  });

  factory ItemRef.fromJson(Map<String, dynamic> json) => ItemRef(
    id: json['id'] as int,
    providerId: json['provider_id'] as String,
    remoteId: json['remote_id'] as String,
    title: json['title'] as String,
  );

  final int id;
  final String providerId;
  final String remoteId;
  final String title;

  MetadataCandidate get candidate => MetadataCandidate(
    providerId: providerId,
    remoteId: remoteId,
    title: title,
  );
}

/// 元数据插件里的一个条目：匹配候选或搜索结果。
class MetadataCandidate {
  const MetadataCandidate({
    this.providerId,
    required this.remoteId,
    required this.title,
    this.originalTitle,
    this.year,
    this.overview,
    this.score,
    this.reasons = const [],
  });

  factory MetadataCandidate.fromJson(Map<String, dynamic> json) =>
      MetadataCandidate(
        providerId: json['provider_id'] as String,
        remoteId: json['remote_id'] as String,
        title: json['title'] as String,
        originalTitle: json['original_title'] as String?,
        year: json['year'] as int?,
        overview: json['overview'] as String?,
        score: json['score'] as int?,
        reasons: [
          for (final r in json['reasons'] as List<dynamic>? ?? const [])
            r as String,
        ],
      );

  /// 为空时（手动输入的 ID）由服务端按资料库选择的插件解释
  final String? providerId;
  final String remoteId;
  final String title;
  final String? originalTitle;
  final int? year;
  final String? overview;

  /// 待处理候选的匹配度（0–100）和依据；搜索结果没有
  final int? score;
  final List<String> reasons;

  String get label => year == null ? title : '$title（$year）';
}

class PendingSummary {
  const PendingSummary({
    this.metadataProblem,
    this.metadataAction,
    required this.waiting,
    required this.unmatched,
    required this.unplaced,
    required this.ignored,
  });

  factory PendingSummary.fromJson(Map<String, dynamic> json) => PendingSummary(
    metadataProblem: json['metadata_problem'] as String?,
    metadataAction: json['metadata_action'] as String?,
    waiting: json['waiting'] as int,
    unmatched: json['unmatched'] as int,
    unplaced: json['unplaced'] as int,
    ignored: json['ignored'] as int,
  );

  /// 资料库选用的元数据插件不能用的原因（没配置、停用等），和处理它的管理页面
  final String? metadataProblem;
  final String? metadataAction;

  /// 等待匹配的作品（元数据插件不能用或上次请求失败），下次同步时匹配
  final int waiting;
  final int unmatched;
  final int unplaced;
  final int ignored;
}

/// 没有匹配上的作品（一个电影目录或一部剧的目录）。
class PendingWork {
  const PendingWork({
    required this.id,
    required this.libraryId,
    required this.libraryName,
    required this.kind,
    required this.path,
    this.title,
    this.year,
    this.reason,
    required this.candidates,
    required this.fileCount,
  });

  factory PendingWork.fromJson(Map<String, dynamic> json) => PendingWork(
    id: json['id'] as int,
    libraryId: json['library_id'] as int,
    libraryName: json['library_name'] as String,
    kind: json['kind'] as String,
    path: json['path'] as String,
    title: json['title'] as String?,
    year: json['year'] as int?,
    reason: json['reason'] as String?,
    candidates: [
      for (final item in json['candidates'] as List<dynamic>)
        MetadataCandidate.fromJson(item as Map<String, dynamic>),
    ],
    fileCount: json['file_count'] as int,
  );

  final int id;
  final int libraryId;
  final String libraryName;
  final String kind;

  /// 来源里的完整路径
  final String path;

  /// 从目录名识别出的片名和年份
  final String? title;
  final int? year;
  final String? reason;
  final List<MetadataCandidate> candidates;
  final int fileCount;
}

/// 没有归入任何条目的视频，或被识别为附加内容而忽略的视频。
class PendingFile {
  const PendingFile({
    required this.id,
    required this.libraryId,
    required this.libraryName,
    required this.kind,
    required this.path,
    this.size,
    required this.reason,
    this.item,
  });

  factory PendingFile.fromJson(Map<String, dynamic> json) => PendingFile(
    id: json['id'] as int,
    libraryId: json['library_id'] as int,
    libraryName: json['library_name'] as String,
    kind: json['kind'] as String,
    path: json['path'] as String,
    size: json['size'] as int?,
    reason: json['reason'] as String,
    item: json['item'] == null
        ? null
        : ItemRef.fromJson(json['item'] as Map<String, dynamic>),
  );

  final int id;
  final int libraryId;
  final String libraryName;
  final String kind;
  final String path;
  final int? size;
  final String reason;

  /// 所在作品已匹配的条目，指定时可以直接选它
  final ItemRef? item;

  String get name => path.substring(path.lastIndexOf('/') + 1);
}

/// 未归入/已忽略视频的一页；reasons 是当前范围内出现过的原因，供筛选
class PendingFilePage extends PageOf<PendingFile> {
  const PendingFilePage({
    required super.items,
    required super.total,
    required this.reasons,
  });

  factory PendingFilePage.fromJson(Map<String, dynamic> json) {
    final page = PageOf.fromJson(json, PendingFile.fromJson);
    return PendingFilePage(
      items: page.items,
      total: page.total,
      reasons: [
        for (final reason in json['reasons'] as List<dynamic>) reason as String,
      ],
    );
  }

  final List<String> reasons;
}

class AdminItem {
  const AdminItem({
    required this.id,
    required this.libraryId,
    required this.libraryName,
    required this.kind,
    required this.providerId,
    required this.remoteId,
    required this.title,
    this.originalTitle,
    this.year,
    required this.poster,
    required this.versionCount,
  });

  factory AdminItem.fromJson(Map<String, dynamic> json, String baseUrl) =>
      AdminItem(
        id: json['id'] as int,
        libraryId: json['library_id'] as int,
        libraryName: json['library_name'] as String,
        kind: json['kind'] as String,
        providerId: json['provider_id'] as String,
        remoteId: json['remote_id'] as String,
        title: json['title'] as String,
        originalTitle: json['original_title'] as String?,
        year: json['year'] as int?,
        poster: Artwork.fromJson(json['poster'], baseUrl),
        versionCount: json['version_count'] as int,
      );

  final int id;
  final int libraryId;
  final String libraryName;
  final String kind;

  /// 负责这个条目元数据的插件和插件里的条目 ID
  final String providerId;
  final String remoteId;
  final String title;
  final String? originalTitle;
  final int? year;
  final Artwork poster;
  final int versionCount;
}

class ItemWork {
  const ItemWork({required this.id, required this.path, required this.manual});

  factory ItemWork.fromJson(Map<String, dynamic> json) => ItemWork(
    id: json['id'] as int,
    path: json['path'] as String,
    manual: json['manual'] as bool,
  );

  final int id;
  final String path;

  /// 手动指定的作品，同步不会改动它的匹配结果
  final bool manual;
}

class ItemVersion {
  const ItemVersion({
    required this.id,
    required this.mediaFileId,
    required this.path,
    this.size,
    required this.state,
    required this.label,
    required this.manual,
    this.season,
    this.episode,
  });

  factory ItemVersion.fromJson(Map<String, dynamic> json) => ItemVersion(
    id: json['id'] as int,
    mediaFileId: json['media_file_id'] as int,
    path: json['path'] as String,
    size: json['size'] as int?,
    state: json['state'] as String,
    label: json['label'] as String,
    manual: json['manual'] as bool,
    season: json['season'] as int?,
    episode: json['episode'] as int?,
  );

  final int id;
  final int mediaFileId;
  final String path;
  final int? size;

  /// active / missing 等，missing 表示来源上已找不到
  final String state;

  /// 版本标签，如「4K HDR · 60 GB」
  final String label;
  final bool manual;
  final int? season;
  final int? episode;

  String get name => path.substring(path.lastIndexOf('/') + 1);
  bool get missing => state == 'missing';
}

class AdminItemDetail {
  const AdminItemDetail({
    required this.item,
    required this.providerName,
    this.externalUrl,
    this.overview,
    this.rating,
    required this.genres,
    this.runtime,
    required this.backdrop,
    required this.scrapedAt,
    required this.works,
    required this.versions,
  });

  factory AdminItemDetail.fromJson(Map<String, dynamic> json, String baseUrl) =>
      AdminItemDetail(
        item: AdminItem.fromJson(json, baseUrl),
        providerName: json['provider_name'] as String,
        externalUrl: json['external_url'] as String?,
        overview: json['overview'] as String?,
        rating: (json['rating'] as num?)?.toDouble(),
        genres: [for (final g in json['genres'] as List<dynamic>) g as String],
        runtime: json['runtime'] as int?,
        backdrop: Artwork.fromJson(json['backdrop'], baseUrl),
        scrapedAt: DateTime.parse(json['scraped_at'] as String).toLocal(),
        works: [
          for (final w in json['works'] as List<dynamic>)
            ItemWork.fromJson(w as Map<String, dynamic>),
        ],
        versions: [
          for (final v in json['versions'] as List<dynamic>)
            ItemVersion.fromJson(v as Map<String, dynamic>),
        ],
      );

  final AdminItem item;
  final String providerName;

  /// 条目在插件网站上的页面，插件不提供时为空
  final String? externalUrl;
  final String? overview;
  final double? rating;
  final List<String> genres;

  /// 分钟
  final int? runtime;
  final Artwork backdrop;
  final DateTime scrapedAt;
  final List<ItemWork> works;
  final List<ItemVersion> versions;
}

class MetadataField {
  const MetadataField({
    required this.field,
    required this.value,
    required this.source,
    required this.locked,
    required this.updatedAt,
  });

  factory MetadataField.fromJson(Map<String, dynamic> json) => MetadataField(
    field: json['field'] as String,
    value: json['value'] as String?,
    source: json['source'] as String,
    locked: json['locked'] as bool,
    updatedAt: DateTime.parse(json['updated_at'] as String).toLocal(),
  );

  final String field;
  final String? value;
  final String source;
  final bool locked;
  final DateTime updatedAt;

  String get label => switch (field) {
    'title' => '标题',
    'original_title' => '原始标题',
    'en_title' => '英文标题',
    'overview' => '简介',
    'poster_path' => '海报来源',
    'backdrop_path' => '背景图来源',
    'thumb_path' => '横版图来源',
    'logo_path' => 'Logo 来源',
    _ => field,
  };

  String get sourceLabel => switch (source) {
    'manual' => '手动',
    'nfo' => 'NFO',
    _ => '元数据插件',
  };
}

class BatchAssignmentResult {
  const BatchAssignmentResult({
    required this.id,
    this.itemId,
    this.errorCode,
    this.errorMessage,
  });

  factory BatchAssignmentResult.fromJson(Map<String, dynamic> json) {
    final error = json['error'] as Map<String, dynamic>?;
    return BatchAssignmentResult(
      id: json['id'] as int,
      itemId: json['item_id'] as int?,
      errorCode: error?['code'] as String?,
      errorMessage: error?['message'] as String?,
    );
  }

  final int id;
  final int? itemId;
  final String? errorCode;
  final String? errorMessage;

  bool get succeeded => errorCode == null;
}

class BatchAssignmentReport {
  const BatchAssignmentReport({
    required this.taskId,
    required this.requested,
    required this.succeeded,
    required this.failed,
    required this.unprocessed,
    required this.results,
  });

  factory BatchAssignmentReport.fromJson(Map<String, dynamic> json) =>
      BatchAssignmentReport(
        taskId: json['task_id'] as int,
        requested: json['requested'] as int,
        succeeded: json['succeeded'] as int,
        failed: json['failed'] as int,
        unprocessed: json['unprocessed'] as int,
        results: [
          for (final result in json['results'] as List<dynamic>)
            BatchAssignmentResult.fromJson(result as Map<String, dynamic>),
        ],
      );

  final int requested;

  /// 持久化后台任务 ID
  final int taskId;
  final int succeeded;
  final int failed;
  final int unprocessed;
  final List<BatchAssignmentResult> results;
}
