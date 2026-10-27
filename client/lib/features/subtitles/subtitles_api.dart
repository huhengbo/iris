import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../core/providers.dart';
import '../library/browse_api.dart';

/// 在线字幕支持的语言码，和 API 契约一致。zh-CN / zh-TW 是用户偏好里的写法，
/// 发给服务端前统一换成 zh-Hans / zh-Hant（ADR 0025）。
const subtitleLanguages = ['zh-Hans', 'zh-Hant', 'zh', 'en', 'ja', 'ko'];

String? normalizeSubtitleLanguage(String? code) {
  if (code == null || code.isEmpty) return null;
  return switch (code.toLowerCase().replaceAll('_', '-')) {
    'zh-cn' || 'zh-hans' || 'zh-hans-cn' => 'zh-Hans',
    'zh-tw' || 'zh-hk' || 'zh-hant' || 'zh-hant-tw' => 'zh-Hant',
    'zh' => 'zh',
    'en' || 'eng' => 'en',
    'ja' || 'jpn' => 'ja',
    'ko' || 'kor' => 'ko',
    _ => code,
  };
}

String subtitleLanguageName(String? code) =>
    switch (normalizeSubtitleLanguage(code)) {
      'zh-Hans' => '简体中文',
      'zh-Hant' => '繁体中文',
      'zh' => '中文',
      'en' => '英语',
      'ja' => '日语',
      'ko' => '韩语',
      final other => other ?? '未知语言',
    };

/// 字幕接口统一的中文提示。后端的 message 有时只是「请求失败」，权限、限流
/// 这类需要明确告诉用户该做什么的情况在这里补上。
String subtitleErrorMessage(Object error) {
  final api = ApiError.from(error);
  return switch (api.code) {
    'subtitle_download_not_allowed' => '管理员没有允许你下载在线字幕',
    'subtitle_rate_limited' => '搜索或下载太频繁，请稍后再试',
    'subtitle_provider_unavailable' => '这个字幕下载器暂时不可用，请稍后再试',
    'subtitle_provider_failed' => '字幕下载器出错，可以换个下载器或稍后再试',
    'invalid_subtitle' => '这个字幕文件格式不支持或内容无效',
    'subtitle_limit_reached' => '这个版本保存的在线字幕已达上限',
    _ when api.kind == ApiErrorKind.forbidden => '没有权限使用在线字幕',
    _ when api.kind == ApiErrorKind.rateLimited => '操作太频繁，请稍后再试',
    _ => api.message,
  };
}

// ---- 用户接口 ----

class SubtitleProviderInfo {
  const SubtitleProviderInfo({
    required this.id,
    required this.name,
    required this.ready,
    this.reason,
  });

  factory SubtitleProviderInfo.fromJson(Map<String, dynamic> json) =>
      SubtitleProviderInfo(
        id: json['id'] as String,
        name: json['name'] as String,
        ready: json['ready'] as bool? ?? false,
        reason: json['reason'] as String?,
      );

  final String id;
  final String name;

  /// 配置完整、可以搜索和下载
  final bool ready;

  /// 不能用的原因，只对管理员返回
  final String? reason;
}

class SubtitleProviders {
  const SubtitleProviders({required this.allowed, required this.providers});

  factory SubtitleProviders.fromJson(Map<String, dynamic> json) {
    final providers = [
      for (final raw in json['providers'] as List<dynamic>? ?? const [])
        SubtitleProviderInfo.fromJson(raw as Map<String, dynamic>),
    ];
    return SubtitleProviders(
      allowed: json['allowed'] as bool? ?? true,
      providers: providers,
    );
  }

  /// 当前用户被允许下载在线字幕（管理员始终允许）
  final bool allowed;
  final List<SubtitleProviderInfo> providers;

  bool get hasReady => providers.any((p) => p.ready);

  /// 当前用户被允许，且至少有一个下载器可以搜索
  bool get usable => allowed && hasReady;
}

class SubtitleSearchProviderStatus {
  const SubtitleSearchProviderStatus({
    required this.id,
    required this.name,
    required this.status,
    this.message,
  });

  factory SubtitleSearchProviderStatus.fromJson(Map<String, dynamic> json) =>
      SubtitleSearchProviderStatus(
        id: json['id'] as String,
        name: json['name'] as String,
        status: json['status'] as String? ?? 'failed',
        message: json['message'] as String?,
      );

  final String id;
  final String name;

  /// ok、failed、unavailable
  final String status;
  final String? message;

  bool get ok => status == 'ok';

  String get statusLabel => switch (status) {
    'unavailable' => '不可用',
    'failed' => '失败',
    _ => status,
  };
}

class SubtitleCandidate {
  const SubtitleCandidate({
    required this.providerId,
    required this.providerName,
    required this.remoteId,
    required this.label,
    this.release,
    this.language,
    this.format,
    this.forced = false,
    this.hearingImpaired = false,
    this.score,
    this.downloads,
    this.uploadedAt,
    this.downloaded = false,
  });

  factory SubtitleCandidate.fromJson(Map<String, dynamic> json) =>
      SubtitleCandidate(
        providerId: json['provider_id'] as String,
        providerName: json['provider_name'] as String,
        remoteId: json['remote_id'] as String,
        label: json['label'] as String,
        release: json['release'] as String?,
        language: normalizeSubtitleLanguage(json['language'] as String?),
        format: json['format'] as String?,
        forced: json['forced'] as bool? ?? false,
        hearingImpaired: json['hearing_impaired'] as bool? ?? false,
        score: (json['score'] as num?)?.toDouble(),
        downloads: json['downloads'] as int?,
        uploadedAt: switch (json['uploaded_at']) {
          final String value => DateTime.tryParse(value)?.toLocal(),
          _ => null,
        },
        downloaded: json['downloaded'] as bool? ?? false,
      );

  final String providerId;
  final String providerName;
  final String remoteId;
  final String label;
  final String? release;
  final String? language;
  final String? format;
  final bool forced;
  final bool hearingImpaired;
  final double? score;
  final int? downloads;
  final DateTime? uploadedAt;

  /// 这个版本已经保存过同一条字幕
  final bool downloaded;

  String get key => '$providerId/$remoteId';
}

class SubtitleSearchResults {
  const SubtitleSearchResults({
    required this.versionId,
    this.language,
    this.providers = const [],
    this.results = const [],
  });

  factory SubtitleSearchResults.fromJson(Map<String, dynamic> json) =>
      SubtitleSearchResults(
        versionId: json['version_id'] as int,
        language: normalizeSubtitleLanguage(json['language'] as String?),
        providers: [
          for (final raw in json['providers'] as List<dynamic>? ?? const [])
            SubtitleSearchProviderStatus.fromJson(raw as Map<String, dynamic>),
        ],
        results: [
          for (final raw in json['results'] as List<dynamic>? ?? const [])
            SubtitleCandidate.fromJson(raw as Map<String, dynamic>),
        ],
      );

  final int versionId;
  final String? language;
  final List<SubtitleSearchProviderStatus> providers;
  final List<SubtitleCandidate> results;

  List<SubtitleSearchProviderStatus> get failures =>
      providers.where((p) => !p.ok).toList();

  /// 所有下载器都失败或不可用，没有一条结果
  bool get allFailed =>
      results.isEmpty && providers.isNotEmpty && providers.every((p) => !p.ok);
}

class SubtitleDownloadRequest {
  const SubtitleDownloadRequest({
    required this.providerId,
    required this.remoteId,
    required this.label,
    this.language,
    this.release,
    this.format,
    this.forced = false,
    this.hearingImpaired = false,
  });

  factory SubtitleDownloadRequest.fromCandidate(
    SubtitleCandidate candidate, {
    String? language,
  }) => SubtitleDownloadRequest(
    providerId: candidate.providerId,
    remoteId: candidate.remoteId,
    label: candidate.label,
    language: candidate.language ?? language,
    release: candidate.release,
    format: candidate.format,
    forced: candidate.forced,
    hearingImpaired: candidate.hearingImpaired,
  );

  final String providerId;
  final String remoteId;
  final String? language;
  final String label;
  final String? release;
  final String? format;
  final bool forced;
  final bool hearingImpaired;

  Map<String, Object> toJson() => {
    'provider_id': providerId,
    'remote_id': remoteId,
    'label': label,
    'language': ?language,
    'release': ?release,
    'format': ?format,
    'forced': forced,
    'hearing_impaired': hearingImpaired,
  };
}

class SubtitleDownloadResult {
  const SubtitleDownloadResult({
    required this.subtitle,
    required this.created,
    this.playPath,
    this.playUrl,
  });

  factory SubtitleDownloadResult.fromJson(Map<String, dynamic> json) {
    final play = switch (json['play']) {
      final Map<String, dynamic> value => value,
      _ => const <String, dynamic>{},
    };
    return SubtitleDownloadResult(
      subtitle: MediaSubtitle.fromJson(
        json['subtitle'] as Map<String, dynamic>,
      ),
      created: json['created'] as bool? ?? true,
      playPath: play['path'] as String?,
      playUrl: play['url'] as String?,
    );
  }

  /// 新建或命中的已有在线字幕
  final MediaSubtitle subtitle;

  /// false 表示这个版本已经有同内容的字幕，服务端直接返回了它
  final bool created;

  /// 带签名的相对路径，内置播放器用当前服务器地址拼
  final String? playPath;
  final String? playUrl;
}

// ---- 管理员接口 ----

class AdminSubtitleVersion {
  const AdminSubtitleVersion({
    required this.id,
    required this.label,
    this.filename,
  });

  factory AdminSubtitleVersion.fromJson(Map<String, dynamic> json) =>
      AdminSubtitleVersion(
        id: json['id'] as int,
        label: json['label'] as String,
        filename: json['filename'] as String?,
      );

  final int id;
  final String label;
  final String? filename;
}

class AdminExternalSubtitle {
  const AdminExternalSubtitle({
    required this.relPath,
    required this.deletable,
    this.deleteBlockedReason,
  });

  factory AdminExternalSubtitle.fromJson(Map<String, dynamic> json) =>
      AdminExternalSubtitle(
        relPath: json['rel_path'] as String? ?? '',
        deletable: json['deletable'] as bool? ?? false,
        deleteBlockedReason: json['delete_blocked_reason'] as String?,
      );

  final String relPath;
  final bool deletable;

  /// 不能删除时的说明，例如本地来源不支持删除
  final String? deleteBlockedReason;
}

class AdminSubtitleUser {
  const AdminSubtitleUser({required this.id, required this.username});

  factory AdminSubtitleUser.fromJson(Map<String, dynamic> json) =>
      AdminSubtitleUser(
        id: json['id'] as int,
        username: json['username'] as String,
      );

  final int id;
  final String username;
}

class AdminOnlineSubtitle {
  const AdminOnlineSubtitle({
    required this.providerId,
    required this.providerName,
    this.release,
    this.size,
    this.createdAt,
    this.createdBy,
  });

  factory AdminOnlineSubtitle.fromJson(Map<String, dynamic> json) =>
      AdminOnlineSubtitle(
        providerId: json['provider_id'] as String? ?? '',
        providerName: json['provider_name'] as String? ?? '',
        release: json['release'] as String?,
        size: json['size'] as int?,
        createdAt: switch (json['created_at']) {
          final String value => DateTime.tryParse(value)?.toLocal(),
          _ => null,
        },
        createdBy: switch (json['created_by']) {
          final Map<String, dynamic> value => AdminSubtitleUser.fromJson(value),
          _ => null,
        },
      );

  final String providerId;
  final String providerName;
  final String? release;
  final int? size;
  final DateTime? createdAt;
  final AdminSubtitleUser? createdBy;
}

class AdminSubtitle {
  const AdminSubtitle({
    required this.id,
    required this.source,
    required this.label,
    this.language,
    this.forced = false,
    this.hearingImpaired = false,
    this.isDefault = false,
    this.format,
    this.external,
    this.online,
  });

  factory AdminSubtitle.fromJson(Map<String, dynamic> json) => AdminSubtitle(
    id: json['id'] as int,
    source: json['source'] as String? ?? 'external',
    label: json['label'] as String,
    language: json['language'] as String?,
    forced: json['forced'] as bool? ?? false,
    hearingImpaired: json['hearing_impaired'] as bool? ?? false,
    isDefault: json['default'] as bool? ?? false,
    format: json['format'] as String?,
    external: switch (json['external']) {
      final Map<String, dynamic> value => AdminExternalSubtitle.fromJson(value),
      _ => null,
    },
    online: switch (json['online']) {
      final Map<String, dynamic> value => AdminOnlineSubtitle.fromJson(value),
      _ => null,
    },
  );

  final int id;

  /// external 或 online
  final String source;
  final String label;
  final String? language;
  final bool forced;
  final bool hearingImpaired;
  final bool isDefault;
  final String? format;
  final AdminExternalSubtitle? external;
  final AdminOnlineSubtitle? online;

  bool get isOnline => source == 'online';

  bool get deletable => isOnline || (external?.deletable ?? false);
}

class AdminEmbeddedSubtitle {
  const AdminEmbeddedSubtitle({
    this.language,
    this.title,
    this.codec,
    this.forced = false,
    this.isDefault = false,
  });

  factory AdminEmbeddedSubtitle.fromJson(Map<String, dynamic> json) =>
      AdminEmbeddedSubtitle(
        language: json['language'] as String?,
        title: json['title'] as String?,
        codec: json['codec'] as String?,
        forced: json['forced'] as bool? ?? false,
        isDefault: json['default'] as bool? ?? false,
      );

  final String? language;
  final String? title;
  final String? codec;
  final bool forced;
  final bool isDefault;
}

class AdminVersionSubtitles {
  const AdminVersionSubtitles({
    required this.version,
    this.subtitles = const [],
    this.embedded,
  });

  factory AdminVersionSubtitles.fromJson(Map<String, dynamic> json) =>
      AdminVersionSubtitles(
        version: AdminSubtitleVersion.fromJson(
          json['version'] as Map<String, dynamic>,
        ),
        subtitles: [
          for (final raw in json['subtitles'] as List<dynamic>? ?? const [])
            AdminSubtitle.fromJson(raw as Map<String, dynamic>),
        ],
        embedded: switch (json['embedded']) {
          final List<dynamic> value => [
            for (final raw in value)
              AdminEmbeddedSubtitle.fromJson(raw as Map<String, dynamic>),
          ],
          _ => null,
        },
      );

  final AdminSubtitleVersion version;
  final List<AdminSubtitle> subtitles;

  /// null 表示内置播放器还没有上报过流信息
  final List<AdminEmbeddedSubtitle>? embedded;

  List<AdminSubtitle> get external =>
      subtitles.where((s) => !s.isOnline).toList();
  List<AdminSubtitle> get online => subtitles.where((s) => s.isOnline).toList();
}

// ---- 接口 ----

class SubtitlesApi {
  const SubtitlesApi(this._dio);

  final Dio _dio;

  /// 传 [versionId] 时按该版本所在资料库的「字幕下载器」列表过滤。
  Future<SubtitleProviders> providers({int? versionId}) async {
    final response = await _dio.get<Map<String, dynamic>>(
      '/api/subtitles/providers',
      queryParameters: {'version_id': ?versionId},
    );
    return SubtitleProviders.fromJson(response.data!);
  }

  Future<SubtitleSearchResults> search(
    int versionId, {
    String? language,
    String? providerId,
  }) async {
    final response = await _dio.get<Map<String, dynamic>>(
      '/api/versions/$versionId/subtitles/search',
      queryParameters: {'language': ?language, 'provider_id': ?providerId},
    );
    return SubtitleSearchResults.fromJson(response.data!);
  }

  Future<SubtitleDownloadResult> download(
    int versionId,
    SubtitleDownloadRequest request,
  ) async {
    final response = await _dio.post<Map<String, dynamic>>(
      '/api/versions/$versionId/subtitles',
      data: request.toJson(),
    );
    return SubtitleDownloadResult.fromJson(response.data!);
  }

  Future<AdminVersionSubtitles> adminList(int versionId) async {
    final response = await _dio.get<Map<String, dynamic>>(
      '/api/admin/versions/$versionId/subtitles',
    );
    return AdminVersionSubtitles.fromJson(response.data!);
  }

  Future<void> delete(int subtitleId) => _dio.delete<void>(
    '/api/admin/subtitles/$subtitleId',
    queryParameters: const {'confirm': true},
  );
}

final subtitlesApiProvider = Provider<SubtitlesApi>(
  (ref) => SubtitlesApi(ref.watch(apiClientProvider)),
);

/// 某个版本的可用字幕下载器；[versionId] 为空时是全局列表。
final subtitleProvidersProvider = FutureProvider.autoDispose
    .family<SubtitleProviders, int?>(
      (ref, versionId) =>
          ref.watch(subtitlesApiProvider).providers(versionId: versionId),
    );
