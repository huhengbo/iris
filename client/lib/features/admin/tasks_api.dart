import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';

const tasksPath = '/api/admin/tasks';

class AdminTask {
  const AdminTask({
    required this.id,
    required this.kind,
    required this.phase,
    required this.status,
    required this.progressDone,
    required this.progressTotal,
    required this.cancelRequested,
    this.error,
    this.result,
    required this.startedAt,
    this.finishedAt,
  });

  factory AdminTask.fromJson(Map<String, dynamic> json) => AdminTask(
    id: json['id'] as int,
    kind: json['kind'] as String,
    phase: json['phase'] as String,
    status: json['status'] as String,
    progressDone: json['progress_done'] as int,
    progressTotal: json['progress_total'] as int,
    cancelRequested: json['cancel_requested'] as bool,
    error: json['error'] as String?,
    result: json['result'] as Map<String, dynamic>?,
    startedAt: DateTime.parse(json['started_at'] as String).toLocal(),
    finishedAt: json['finished_at'] == null
        ? null
        : DateTime.parse(json['finished_at'] as String).toLocal(),
  );

  final int id;
  final String kind;
  final String phase;
  final String status;
  final int progressDone;
  final int progressTotal;
  final bool cancelRequested;
  final String? error;
  final Map<String, dynamic>? result;
  final DateTime startedAt;
  final DateTime? finishedAt;

  bool get active => status == 'running' || status == 'cancel_requested';
}

final adminTasksProvider = FutureProvider.autoDispose<List<AdminTask>>((
  ref,
) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<List<dynamic>>(tasksPath);
  return [
    for (final value in response.data!)
      AdminTask.fromJson(value as Map<String, dynamic>),
  ];
});

/// 等后台任务结束；超过等待次数时返回最后一次查到的状态。
Future<AdminTask> waitAdminTask(
  Dio dio,
  int id, {
  Duration interval = const Duration(seconds: 1),
  int attempts = 120,
}) async {
  for (var attempt = 1; ; attempt++) {
    final response = await dio.get<Map<String, dynamic>>('$tasksPath/$id');
    final task = AdminTask.fromJson(response.data!);
    if (!task.active || attempt >= attempts) return task;
    await Future<void>.delayed(interval);
  }
}

Future<void> cancelAdminTask(Dio dio, int id) =>
    dio.post<void>('$tasksPath/$id/cancel');

Future<void> retryAdminTask(Dio dio, int id) =>
    dio.post<void>('$tasksPath/$id/retry');
