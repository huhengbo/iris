import 'package:dio/dio.dart';
import 'package:image_picker/image_picker.dart'
    show ImagePicker, ImageSource, XFile;
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/paging.dart';
import '../../core/providers.dart';

const librariesPath = '/api/admin/libraries';
const syncPath = '/api/admin/sync';

class LibraryFolder {
  const LibraryFolder({
    required this.path,
    required this.sourceId,
    required this.sourceName,
    required this.pluginId,
    required this.locator,
  });

  factory LibraryFolder.fromJson(Map<String, dynamic> json) => LibraryFolder(
    path: json['path'] as String,
    sourceId: json['source_id'] as int,
    sourceName: json['source_name'] as String,
    pluginId: json['plugin_id'] as String,
    locator: json['locator'] as String?,
  );

  /// 目录在来源里的完整路径，如 /影视/电影
  final String path;

  /// 目录所在的资源来源
  final int sourceId;
  final String sourceName;
  final String pluginId;

  /// 目录在来源里的定位符，由插件解释；null 表示来源根目录
  final String? locator;

  /// 同一个来源里的同一个目录 key 相同
  String get key => '$sourceId:${locator ?? ''}';
}

class AdminLibrary {
  const AdminLibrary({
    required this.id,
    required this.name,
    required this.kind,
    required this.folders,
    required this.videoCount,
    required this.missingCount,
    this.subtitleLanguage,
    required this.metadataProvider,
    required this.cover,
    required this.coverCustom,
  });

  /// [baseUrl] 用来把 cover 里的相对地址拼成完整地址。
  factory AdminLibrary.fromJson(
    Map<String, dynamic> json, [
    String baseUrl = '',
  ]) => AdminLibrary(
    id: json['id'] as int,
    name: json['name'] as String,
    kind: json['kind'] as String,
    folders: [
      for (final item in json['folders'] as List<dynamic>)
        LibraryFolder.fromJson(item as Map<String, dynamic>),
    ],
    videoCount: json['video_count'] as int,
    missingCount: json['missing_count'] as int,
    subtitleLanguage: json['subtitle_language'] as String?,
    metadataProvider: json['metadata_provider'] as String,
    cover: Artwork.fromJson(json['cover'], baseUrl),
    coverCustom: json['cover_custom'] as bool,
  );

  final int id;
  final String name;

  /// movie / series，创建后不能修改
  final String kind;
  final List<LibraryFolder> folders;
  final int videoCount;
  final int missingCount;
  final String? subtitleLanguage;

  /// 新匹配的条目用哪个元数据插件刮削
  final String metadataProvider;

  /// 横版封面，没有图片时为空
  final Artwork cover;

  /// true 为管理员上传；false 为自动取最近入库条目的背景图
  final bool coverCustom;
}

/// 新资料库默认的元数据插件：内置 TMDB
const defaultMetadataProvider = 'builtin.tmdb';

/// 资料库可以选用的元数据插件。
class MetadataProviderInfo {
  const MetadataProviderInfo({
    required this.id,
    required this.name,
    required this.builtIn,
    required this.enabled,
    this.unavailableReason,
  });

  factory MetadataProviderInfo.fromJson(Map<String, dynamic> json) =>
      MetadataProviderInfo(
        id: json['id'] as String,
        name: json['name'] as String,
        builtIn: json['built_in'] as bool,
        enabled: json['enabled'] as bool,
        unavailableReason: json['unavailable_reason'] as String?,
      );

  final String id;
  final String name;
  final bool builtIn;
  final bool enabled;

  /// 启用但还不能请求的原因，例如没填 Key
  final String? unavailableReason;

  String get label => '$name（${builtIn ? '内置' : '外置'}）';
}

String libraryKindLabel(String kind) => kind == 'series' ? '剧集' : '电影';

/// 目录浏览器面包屑的一层。
class DirCrumb {
  const DirCrumb({required this.id, required this.name});

  final String id;
  final String name;
}

class SyncStats {
  const SyncStats({
    required this.added,
    required this.removed,
    required this.renamed,
    required this.restored,
    required this.failed,
    required this.paused,
    required this.purged,
    required this.matched,
    required this.unmatched,
    required this.ignored,
    required this.waiting,
    required this.problems,
  });

  factory SyncStats.fromJson(Map<String, dynamic> json) => SyncStats(
    added: json['added'] as int,
    removed: json['removed'] as int,
    renamed: json['renamed'] as int,
    restored: json['restored'] as int,
    failed: json['failed'] as int,
    paused: json['paused'] as int,
    purged: json['purged'] as int,
    matched: json['matched'] as int,
    unmatched: json['unmatched'] as int,
    ignored: json['ignored'] as int,
    waiting: json['waiting'] as int,
    problems: [for (final p in json['problems'] as List<dynamic>) p as String],
  );

  final int added;
  final int removed;
  final int renamed;
  final int restored;
  final int failed;

  /// 因来源限流暂停的次数
  final int paused;

  /// 失效超过保留期、这次从库里删除的视频数
  final int purged;

  /// 这次新匹配上的作品、没匹配上（进入待处理）的作品、新识别为附加内容而忽略的视频，
  /// 以及结束时仍在等待匹配的作品（元数据插件不能用或请求失败）
  final int matched;
  final int unmatched;
  final int ignored;
  final int waiting;

  /// 最多 20 条问题说明，failed 是总数
  final List<String> problems;
}

class SyncJob {
  const SyncJob({
    required this.id,
    required this.trigger,
    required this.scope,
    this.libraryName,
    this.target,
    required this.status,
    required this.phase,
    required this.progressDone,
    required this.progressTotal,
    required this.stats,
    this.error,
    this.waitingUntil,
    this.waitingReason,
    required this.startedAt,
    this.finishedAt,
  });

  factory SyncJob.fromJson(Map<String, dynamic> json) => SyncJob(
    id: json['id'] as int,
    trigger: json['trigger'] as String,
    scope: json['scope'] as String,
    libraryName: json['library_name'] as String?,
    target: json['target'] as String?,
    status: json['status'] as String,
    phase: json['phase'] as String,
    progressDone: json['progress_done'] as int,
    progressTotal: json['progress_total'] as int,
    stats: SyncStats.fromJson(json['stats'] as Map<String, dynamic>),
    error: json['error'] as String?,
    waitingUntil: _time(json['waiting_until']),
    waitingReason: json['waiting_reason'] as String?,
    startedAt: _time(json['started_at'])!,
    finishedAt: _time(json['finished_at']),
  );

  /// manual / scheduled / event（来源变动）
  final String trigger;

  /// all / library / dir（只同步指定的目录）
  final String scope;
  final int id;
  final String? libraryName;

  /// 定向同步的范围说明
  final String? target;

  /// running / succeeded / failed
  final String status;

  /// preparing / exporting / listing / enriching / recognizing / matching / scraping /
  /// filling / images / done
  final String phase;
  final int progressDone;
  final int progressTotal;
  final SyncStats stats;
  final String? error;
  final DateTime? waitingUntil;

  /// rate_limited / source_busy
  final String? waitingReason;
  final DateTime startedAt;
  final DateTime? finishedAt;

  bool get running => status == 'running';

  /// 等待时间已过但任务还没更新状态时，不再显示等待提示
  bool waitingAt(DateTime now) =>
      waitingUntil != null && waitingUntil!.isAfter(now);

  String get title => switch (scope) {
    'all' => '全部资料库',
    'dir' => target ?? '指定目录',
    _ => '资料库「${libraryName ?? '已删除'}」',
  };
}

class SyncScheduleConfig {
  const SyncScheduleConfig({
    required this.times,
    required this.timezone,
    this.nextRunAt,
  });

  factory SyncScheduleConfig.fromJson(Map<String, dynamic> json) =>
      SyncScheduleConfig(
        times: [for (final t in json['times'] as List<dynamic>) t as String],
        timezone: json['timezone'] as String,
        nextRunAt: _time(json['next_run_at']),
      );

  /// 每天的时间点，HH:MM，按服务器时区
  final List<String> times;
  final String timezone;
  final DateTime? nextRunAt;
}

/// 成批失效、同步时没有自动删除，等管理员确认的资料库
class HeldDeletion {
  const HeldDeletion({
    required this.libraryId,
    required this.libraryName,
    required this.due,
    required this.total,
  });

  factory HeldDeletion.fromJson(Map<String, dynamic> json) => HeldDeletion(
    libraryId: json['library_id'] as int,
    libraryName: json['library_name'] as String,
    due: json['due'] as int,
    total: json['total'] as int,
  );

  final int libraryId;
  final String libraryName;

  /// 失效超过保留期的视频数和资料库的视频总数
  final int due;
  final int total;
}

final metadataProvidersProvider =
    FutureProvider.autoDispose<List<MetadataProviderInfo>>((ref) async {
      final response = await ref
          .watch(apiClientProvider)
          .get<List<dynamic>>('$librariesPath/metadata-providers');
      return [
        for (final item in response.data!)
          MetadataProviderInfo.fromJson(item as Map<String, dynamic>),
      ];
    });

final adminLibrariesProvider = FutureProvider.autoDispose<List<AdminLibrary>>((
  ref,
) async {
  final baseUrl = ref.watch(apiBaseUrlProvider);
  final response = await ref
      .watch(apiClientProvider)
      .get<List<dynamic>>(librariesPath);
  return [
    for (final item in response.data!)
      AdminLibrary.fromJson(item as Map<String, dynamic>, baseUrl),
  ];
});

final currentSyncProvider = FutureProvider.autoDispose<SyncJob?>((ref) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<Map<String, dynamic>>('$syncPath/current');
  final job = response.data!['job'] as Map<String, dynamic>?;
  return job == null ? null : SyncJob.fromJson(job);
});

final syncJobsProvider = FutureProvider.autoDispose<List<SyncJob>>((ref) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<List<dynamic>>('$syncPath/jobs');
  return [
    for (final item in response.data!)
      SyncJob.fromJson(item as Map<String, dynamic>),
  ];
});

final syncScheduleProvider = FutureProvider.autoDispose<SyncScheduleConfig>((
  ref,
) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<Map<String, dynamic>>('$syncPath/schedule');
  return SyncScheduleConfig.fromJson(response.data!);
});

final heldDeletionsProvider = FutureProvider.autoDispose<List<HeldDeletion>>((
  ref,
) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<List<dynamic>>('$syncPath/held-deletions');
  return [
    for (final item in response.data!)
      HeldDeletion.fromJson(item as Map<String, dynamic>),
  ];
});

Map<String, Object> _folderData(List<LibraryFolder> folders) => {
  'roots': [
    for (final f in folders) {'source_id': f.sourceId, 'locator': f.locator},
  ],
};

Future<void> createLibrary(
  Dio dio, {
  required String name,
  required String kind,
  required List<LibraryFolder> folders,
  String? metadataProvider,
}) => dio.post<void>(
  librariesPath,
  data: {
    'name': name,
    'kind': kind,
    ..._folderData(folders),
    'metadata_provider': ?metadataProvider,
  },
);

/// 只提交有变化的字段；目录有变化时提交完整列表，服务端会拒绝同步进行中的修改。
Future<void> updateLibrary(
  Dio dio,
  int id, {
  String? name,
  List<LibraryFolder>? folders,
  String? subtitleLanguage,
  bool setSubtitleLanguage = false,
  String? metadataProvider,
}) => dio.patch<void>(
  '$librariesPath/$id',
  data: {
    'name': ?name,
    'metadata_provider': ?metadataProvider,
    if (folders != null) ..._folderData(folders),
    if (setSubtitleLanguage) 'subtitle_language': subtitleLanguage,
  },
);

Future<void> deleteLibrary(Dio dio, int id) =>
    dio.delete<void>('$librariesPath/$id');

/// 封面操作的结果：当前封面和是否为自定义。
typedef LibraryCover = ({Artwork cover, bool custom});

LibraryCover _coverResult(Object? data, String baseUrl) {
  final json = data as Map<String, dynamic>;
  return (
    cover: Artwork.fromJson(json['cover'], baseUrl),
    custom: json['custom'] as bool? ?? false,
  );
}

/// 选择封面图片的方式，测试里可以替换。
final libraryCoverPickerProvider = Provider<Future<XFile?> Function()>(
  (ref) =>
      () => ImagePicker().pickImage(source: ImageSource.gallery),
);

/// 上传自定义封面（≤ 8MB，建议横版 16:9）。
Future<LibraryCover> uploadLibraryCover(
  Dio dio,
  int id,
  XFile file,
  String baseUrl,
) async {
  final bytes = await file.readAsBytes();
  final response = await dio.put<Map<String, dynamic>>(
    '$librariesPath/$id/cover',
    data: FormData.fromMap({
      'file': MultipartFile.fromBytes(
        bytes,
        filename: file.name.isEmpty ? 'cover' : file.name,
      ),
    }),
  );
  return _coverResult(response.data, baseUrl);
}

/// 恢复为自动封面。
Future<LibraryCover> resetLibraryCover(Dio dio, int id, String baseUrl) async {
  final response = await dio.delete<Map<String, dynamic>>(
    '$librariesPath/$id/cover',
  );
  return _coverResult(response.data, baseUrl);
}

Future<void> reorderLibraries(Dio dio, List<int> ids) =>
    dio.put<void>('$librariesPath/order', data: {'ids': ids});

/// 不传 libraryId 表示同步全部资料库。
Future<SyncJob> startSync(Dio dio, {int? libraryId}) async {
  final response = await dio.post<Map<String, dynamic>>(
    syncPath,
    data: {'library_id': libraryId},
  );
  return SyncJob.fromJson(response.data!);
}

Future<SyncScheduleConfig> saveSyncSchedule(Dio dio, List<String> times) async {
  final response = await dio.put<Map<String, dynamic>>(
    '$syncPath/schedule',
    data: {'times': times},
  );
  return SyncScheduleConfig.fromJson(response.data!);
}

/// 返回删除的视频数。
Future<int> confirmHeldDeletion(Dio dio, int libraryId) async {
  final response = await dio.post<Map<String, dynamic>>(
    '$syncPath/held-deletions/$libraryId/confirm',
  );
  return response.data!['deleted'] as int;
}

DateTime? _time(Object? value) =>
    value is String ? DateTime.parse(value).toLocal() : null;
