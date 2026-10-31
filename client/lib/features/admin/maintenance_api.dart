import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import 'catalog_api.dart' show adminPageSize;

// 播放历史、警报、计划任务、回收站、Webhook 和日志的接口。

DateTime? _time(Object? value) => value is String && value.isNotEmpty
    ? DateTime.tryParse(value)?.toLocal()
    : null;

// ───────────────────────── 播放历史 ─────────────────────────

/// 能从后台请求停止的播放方式：内置播放器和系统播放器会定时上报进度，
/// 收到停止请求后由客户端停下；外部播放器、投屏、网页和 WebDAV 停不了。
const stoppablePlayModes = {'builtin', 'android', 'ios'};

/// 发起播放的客户端
String playClientLabel(String client) => switch (client) {
  'web' => '网页',
  'android' => 'Android',
  'ios' => 'iOS',
  _ => client,
};

/// 播放方式
String playModeLabel(String mode) => switch (mode) {
  'builtin' => '内置播放器',
  'external' => '外部播放器',
  'android' || 'ios' => '系统播放器',
  'web' => '网页',
  'cast' => '投屏',
  'webdav' => 'WebDAV',
  _ => mode,
};

class PlayHistoryEntry {
  const PlayHistoryEntry({
    required this.id,
    required this.username,
    required this.title,
    required this.client,
    required this.mode,
    required this.createdAt,
    this.endedAt,
    this.watchedSeconds,
  });

  factory PlayHistoryEntry.fromJson(Map<String, dynamic> json) {
    final client = json['client'] as String? ?? '';
    return PlayHistoryEntry(
      id: json['id'] as int,
      username: json['username'] as String? ?? '',
      title: json['title'] as String? ?? '未知条目',
      client: client,
      mode: json['mode'] as String,
      createdAt: _time(json['created_at']) ?? DateTime.now(),
      endedAt: _time(json['ended_at']),
      watchedSeconds: json['watched_seconds'] as int?,
    );
  }

  final int id;
  final String username;
  final String title;
  final String client;
  final String mode;
  final DateTime createdAt;
  final DateTime? endedAt;

  /// 最后上报的播放位置，秒；只有会上报进度的播放器有
  final int? watchedSeconds;

  /// 还没结束、播放方式支持远程停止
  bool get stoppable => endedAt == null && stoppablePlayModes.contains(mode);
}

class PlayHistorySlice {
  const PlayHistorySlice({required this.items, required this.total});

  final List<PlayHistoryEntry> items;
  final int total;
}

class PlayStats {
  const PlayStats({
    required this.plays,
    required this.watchedSeconds,
    required this.byMode,
  });

  factory PlayStats.fromJson(Map<String, dynamic> json) => PlayStats(
    plays: json['plays'] as int? ?? 0,
    watchedSeconds: json['watched_seconds'] as int? ?? 0,
    byMode: [
      for (final row in (json['by_client'] as List<dynamic>?) ?? const [])
        (
          (row as Map<String, dynamic>)['mode'] as String,
          row['count'] as int? ?? 0,
        ),
    ],
  );

  final int plays;
  final int watchedSeconds;

  /// 各播放方式的次数，多的在前
  final List<(String, int)> byMode;
}

final playStatsProvider = FutureProvider.autoDispose<PlayStats>((ref) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<Map<String, dynamic>>('/api/admin/activity/stats');
  return PlayStats.fromJson(response.data!);
});

/// 播放历史的一页，参数是偏移
final playHistoryProvider = FutureProvider.autoDispose
    .family<PlayHistorySlice, int>((ref, offset) async {
      final response = await ref
          .watch(apiClientProvider)
          .get<Map<String, dynamic>>(
            '/api/admin/activity/history',
            queryParameters: {'offset': offset, 'limit': adminPageSize},
          );
      final data = response.data!;
      return PlayHistorySlice(
        items: [
          for (final row in data['items'] as List<dynamic>)
            PlayHistoryEntry.fromJson(row as Map<String, dynamic>),
        ],
        total: data['total'] as int? ?? 0,
      );
    });

/// 请求停止一次播放，播放器下次上报进度时停下
Future<void> stopPlaySession(Dio dio, int eventId) =>
    dio.post<void>('/api/admin/activity/sessions/$eventId/stop');

// ───────────────────────── 警报 ─────────────────────────

class AdminAlert {
  const AdminAlert({
    required this.id,
    required this.level,
    required this.message,
    required this.createdAt,
  });

  factory AdminAlert.fromJson(Map<String, dynamic> json) => AdminAlert(
    id: json['id'] as int,
    level: json['level'] as String? ?? 'warning',
    message: json['message'] as String? ?? '系统警报',
    createdAt: _time(json['created_at']),
  );

  final int id;

  /// info、warning、error
  final String level;
  final String message;
  final DateTime? createdAt;
}

/// 还没处理的警报。管理首页也用它统计需要处理的数量。
final activeAlertsProvider = FutureProvider.autoDispose<List<AdminAlert>>((
  ref,
) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<List<dynamic>>(
        '/api/admin/alerts',
        queryParameters: {'active': true, 'limit': 200},
      );
  return [
    for (final row in response.data!)
      AdminAlert.fromJson(row as Map<String, dynamic>),
  ];
});

Future<void> resolveAlert(Dio dio, int id) => dio.post<void>(
  '/api/admin/alerts/$id',
  data: {'read': true, 'resolved': true},
);

// ───────────────────────── 计划任务 ─────────────────────────

class ScheduledTask {
  const ScheduledTask({
    required this.key,
    required this.enabled,
    required this.interval,
    this.lastRunAt,
  });

  factory ScheduledTask.fromJson(Map<String, dynamic> json) => ScheduledTask(
    key: json['key'] as String,
    enabled: switch (json['enabled']) {
      final bool value => value,
      final int value => value != 0,
      _ => false,
    },
    interval: json['interval'] as int? ?? 86400,
    lastRunAt: _time(json['last_run_at']),
  );

  final String key;
  final bool enabled;

  /// 间隔，秒
  final int interval;
  final DateTime? lastRunAt;

  /// 维护循环的到期判定：没运行过的启用后马上运行，否则上次运行加间隔
  DateTime? get nextRunAt =>
      !enabled ? null : lastRunAt?.add(Duration(seconds: interval));
}

String scheduledTaskLabel(String key) => switch (key) {
  'backup' => '自动备份',
  'optimize_database' => '优化数据库',
  'vacuum' => '压缩数据库',
  'cleanup_images' => '清理图片缓存',
  'cleanup_sessions' => '清理过期登录',
  'cleanup_play_events' => '清理一年前的播放记录',
  'refresh_metadata' => '刷新元数据',
  'download_subtitles' => '下载字幕',
  'empty_recycle_bin' => '清空回收站',
  'check_plugin_updates' => '检查插件更新',
  _ => key,
};

/// 「每天」「每 6 小时」「每 30 天」
String scheduleIntervalLabel(int seconds) {
  const minute = 60, hour = 3600, day = 86400;
  if (seconds == day) return '每天';
  if (seconds == 7 * day) return '每周';
  if (seconds % day == 0) return '每 ${seconds ~/ day} 天';
  if (seconds == hour) return '每小时';
  if (seconds % hour == 0) return '每 ${seconds ~/ hour} 小时';
  return '每 ${(seconds / minute).ceil()} 分钟';
}

final scheduledTasksProvider = FutureProvider.autoDispose<List<ScheduledTask>>((
  ref,
) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<List<dynamic>>('/api/admin/scheduled-tasks');
  return [
    for (final row in response.data!)
      ScheduledTask.fromJson(row as Map<String, dynamic>),
  ];
});

Future<void> setScheduledTaskEnabled(Dio dio, String key, bool enabled) => dio
    .patch<void>('/api/admin/scheduled-tasks/$key', data: {'enabled': enabled});

/// 立即运行一次，返回后台任务 ID
Future<int> runScheduledTask(Dio dio, String key) async {
  final response = await dio.post<Map<String, dynamic>>(
    '/api/admin/scheduled-tasks/$key/run',
  );
  return response.data!['task_id'] as int;
}

// ───────────────────────── 回收站 ─────────────────────────

/// 来源上已经找不到的文件，本地记录还留着
class RecycledFile {
  const RecycledFile({
    required this.mediaFileId,
    required this.relPath,
    this.size,
    this.missingAt,
    required this.libraryId,
    required this.libraryName,
    this.mediaItemId,
    this.title,
  });

  factory RecycledFile.fromJson(Map<String, dynamic> json) => RecycledFile(
    mediaFileId: json['media_file_id'] as int,
    relPath: json['rel_path'] as String? ?? '',
    size: json['size'] as int?,
    missingAt: _time(json['missing_at']),
    libraryId: json['library_id'] as int,
    libraryName: json['library_name'] as String? ?? '',
    mediaItemId: json['media_item_id'] as int?,
    title: json['title'] as String?,
  );

  final int mediaFileId;
  final String relPath;
  final int? size;

  /// 发现文件消失的时间
  final DateTime? missingAt;
  final int libraryId;
  final String libraryName;

  /// 文件归属的条目；没归入条目的视频为空
  final int? mediaItemId;
  final String? title;

  String get fileName => relPath.split('/').last;
}

final recycleBinProvider = FutureProvider.autoDispose<List<RecycledFile>>((
  ref,
) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<List<dynamic>>('/api/admin/recycle-bin');
  return [
    for (final row in response.data!)
      RecycledFile.fromJson(row as Map<String, dynamic>),
  ];
});

/// 清理回收站里的本地记录，[libraryId] 为空时清理全部；返回清理的文件数。
/// 服务端处理完才返回。
Future<int> emptyRecycleBin(Dio dio, {int? libraryId}) async {
  final response = await dio.post<Map<String, dynamic>>(
    '/api/admin/recycle-bin/empty',
    queryParameters: {'library_id': ?libraryId},
  );
  return response.data?['deleted'] as int? ?? 0;
}

// ───────────────────────── Webhook ─────────────────────────

/// 服务端会发出的事件，「*」表示全部
const webhookEvents = {
  'playback_started': '开始播放',
  'playback_paused': '暂停播放',
  'playback_ended': '播放结束',
  'new_content': '新增内容',
  'sync_completed': '同步完成',
  'sync_failed': '同步失败',
  'alert': '系统警报',
};

class AdminWebhook {
  const AdminWebhook({
    required this.id,
    required this.url,
    required this.events,
    required this.enabled,
  });

  factory AdminWebhook.fromJson(Map<String, dynamic> json) {
    // 列表接口返回的是 JSON 字符串，新建接口返回数组
    final raw = json['events'];
    final events = switch (raw) {
      final List<dynamic> list => list,
      final String text when text.isNotEmpty => _decodeList(text),
      _ => const <dynamic>[],
    };
    return AdminWebhook(
      id: json['id'] as int,
      url: json['url'] as String? ?? '',
      events: [for (final event in events) '$event'],
      enabled: switch (json['enabled']) {
        final bool value => value,
        final int value => value != 0,
        _ => false,
      },
    );
  }

  final int id;
  final String url;
  final List<String> events;
  final bool enabled;

  String get eventsLabel {
    if (events.contains('*')) return '全部事件';
    if (events.isEmpty) return '没有选择事件';
    return events.map((e) => webhookEvents[e] ?? e).join('、');
  }
}

List<dynamic> _decodeList(String text) {
  try {
    final value = jsonDecode(text);
    return value is List<dynamic> ? value : const [];
  } on FormatException {
    return const [];
  }
}

final webhooksProvider = FutureProvider.autoDispose<List<AdminWebhook>>((
  ref,
) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<List<dynamic>>('/api/admin/webhooks');
  return [
    for (final row in response.data!)
      AdminWebhook.fromJson(row as Map<String, dynamic>),
  ];
});

Future<void> createWebhook(Dio dio, String url, List<String> events) =>
    dio.post<void>('/api/admin/webhooks', data: {'url': url, 'events': events});

Future<void> setWebhookEnabled(Dio dio, AdminWebhook hook, bool enabled) =>
    dio.patch<void>(
      '/api/admin/webhooks/${hook.id}',
      data: {'url': hook.url, 'events': hook.events, 'enabled': enabled},
    );

Future<void> deleteWebhook(Dio dio, int id) =>
    dio.delete<void>('/api/admin/webhooks/$id');

/// 一次投递的结果
class WebhookDelivery {
  const WebhookDelivery({required this.status, this.response});

  /// pending、succeeded、failed
  final String status;

  /// 对方的 HTTP 状态或出错原因
  final String? response;
}

/// 发一条测试事件，等投递有了结果再返回；超过等待次数时状态仍是 pending。
Future<WebhookDelivery> testWebhook(
  Dio dio,
  int id, {
  Duration interval = const Duration(seconds: 1),
  int attempts = 15,
}) async {
  final sent = await dio.post<Map<String, dynamic>>(
    '/api/admin/webhooks/$id/test',
  );
  final deliveryId = sent.data!['delivery_id'] as int;
  var delivery = const WebhookDelivery(status: 'pending');
  for (var attempt = 1; attempt <= attempts; attempt++) {
    final response = await dio.get<List<dynamic>>(
      '/api/admin/webhooks/$id/deliveries',
    );
    for (final row in response.data!) {
      final json = row as Map<String, dynamic>;
      if (json['id'] == deliveryId) {
        delivery = WebhookDelivery(
          status: json['status'] as String? ?? 'pending',
          response: json['response'] as String?,
        );
      }
    }
    if (delivery.status != 'pending' || attempt == attempts) break;
    await Future<void>.delayed(interval);
  }
  return delivery;
}

// ───────────────────────── 日志 ─────────────────────────

class ServerLogEntry {
  const ServerLogEntry({
    required this.at,
    required this.level,
    required this.module,
    required this.message,
  });

  factory ServerLogEntry.fromJson(Map<String, dynamic> json) => ServerLogEntry(
    at: _time(json['at']),
    level: json['level'] as String? ?? 'info',
    module: json['module'] as String? ?? '',
    message: json['message'] as String? ?? '',
  );

  final DateTime? at;

  /// debug、info、warning、error、critical
  final String level;
  final String module;
  final String message;
}

/// 服务进程最近的日志，新的在前
final serverLogsProvider = FutureProvider.autoDispose<List<ServerLogEntry>>((
  ref,
) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<List<dynamic>>('/api/admin/logs');
  return [
    for (final row in response.data!.reversed)
      ServerLogEntry.fromJson(row as Map<String, dynamic>),
  ];
});
