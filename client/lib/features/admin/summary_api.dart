import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/format.dart';
import '../../core/provider_cache.dart';
import '../../core/providers.dart';
import 'libraries_api.dart';
import 'source_status.dart';

class AdminSummary {
  const AdminSummary({
    required this.sources,
    this.sync,
    required this.heldDeletions,
    this.setupComplete = true,
    this.pending = 0,
    this.nextSyncAt,
  });

  factory AdminSummary.fromJson(Map<String, dynamic> json) {
    final pending = json['pending'] as Map<String, dynamic>?;
    final nextSync = json['next_sync_at'] as String?;
    return AdminSummary(
      sources: [
        for (final item in json['sources'] as List<dynamic>)
          SourceStatus.fromJson(item as Map<String, dynamic>),
      ],
      sync: json['sync'] == null
          ? null
          : SyncJob.fromJson(json['sync'] as Map<String, dynamic>),
      heldDeletions: [
        for (final item in json['held_deletions'] as List<dynamic>)
          HeldDeletion.fromJson(item as Map<String, dynamic>),
      ],
      setupComplete: json['setup_complete'] as bool? ?? true,
      pending: pending == null
          ? 0
          : (pending['unmatched'] as int) + (pending['unplaced'] as int),
      nextSyncAt: nextSync == null ? null : DateTime.parse(nextSync).toLocal(),
    );
  }

  /// 每个已启用资源来源的状态
  final List<SourceStatus> sources;

  /// 最近一次同步
  final SyncJob? sync;
  final List<HeldDeletion> heldDeletions;

  /// 配置向导的步骤都完成了
  final bool setupComplete;

  /// 待处理的数量：没匹配上的作品加没归入条目的视频，不含已忽略的
  final int pending;
  final DateTime? nextSyncAt;
}

final adminSummaryProvider = FutureProvider.autoDispose<AdminSummary>((
  ref,
) async {
  keepProviderAlive(ref);
  final response = await ref
      .watch(apiClientProvider)
      .get<Map<String, dynamic>>('/api/admin/summary');
  return AdminSummary.fromJson(response.data!);
});

/// 一条播放动态：正在播放（带进度）或最近播放（带客户端）。
class PlayActivity {
  const PlayActivity({
    required this.username,
    required this.itemId,
    required this.title,
    this.seasonNumber,
    this.episodeNumber,
    this.position,
    this.duration,
    this.client,
    required this.at,
  });

  factory PlayActivity.fromJson(Map<String, dynamic> json) => PlayActivity(
    username: json['username'] as String,
    itemId: json['item_id'] as int,
    title: json['title'] as String,
    seasonNumber: json['season_number'] as int?,
    episodeNumber: json['episode_number'] as int?,
    position: json['position'] as int?,
    duration: json['duration'] as int?,
    client: json['client'] as String?,
    at: DateTime.parse(json['at'] as String).toLocal(),
  );

  final String username;
  final int itemId;
  final String title;
  final int? seasonNumber;
  final int? episodeNumber;

  /// 秒；只有内置播放器和投屏上报
  final int? position;
  final int? duration;

  /// web、android、ios；只有最近播放有
  final String? client;
  final DateTime at;

  /// 剧集带上季和集，如「三体 · 第 1 季 第 2 集」
  String get label => seasonNumber == null || episodeNumber == null
      ? title
      : '$title · ${formatEpisode(seasonNumber!, episodeNumber!)}';
}

class AdminActivity {
  const AdminActivity({required this.nowPlaying, required this.recent});

  factory AdminActivity.fromJson(Map<String, dynamic> json) => AdminActivity(
    nowPlaying: [
      for (final item in json['now_playing'] as List<dynamic>)
        PlayActivity.fromJson(item as Map<String, dynamic>),
    ],
    recent: [
      for (final item in json['recent'] as List<dynamic>)
        PlayActivity.fromJson(item as Map<String, dynamic>),
    ],
  );

  final List<PlayActivity> nowPlaying;
  final List<PlayActivity> recent;
}

final adminActivityProvider = FutureProvider.autoDispose<AdminActivity>((
  ref,
) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<Map<String, dynamic>>('/api/admin/activity');
  return AdminActivity.fromJson(response.data!);
});
