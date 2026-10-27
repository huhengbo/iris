import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/features/admin/maintenance_api.dart'
    show scheduledTaskLabel;
import 'package:iris/features/admin/tasks_page.dart';

import 'support/fake_api.dart';

Map<String, dynamic> _task(
  int id,
  String status, {
  String? error,
  String kind = 'sync',
}) => {
  'id': id,
  'kind': kind,
  'phase': 'exporting',
  'status': status,
  'progress_done': 2,
  'progress_total': 5,
  'retry_count': 0,
  'cancel_requested': status == 'cancel_requested',
  'error': error,
  'result': <String, dynamic>{},
  'created_by': 1,
  'idempotency_key': null,
  'created_at': '2026-10-03T10:00:00Z',
  'started_at': '2026-10-03T10:00:00Z',
  'finished_at': status == 'running' || status == 'cancel_requested'
      ? null
      : '2026-10-03T10:01:00Z',
};

int _count(FakeAdminApi api, String route) =>
    api.requests.where((r) => r == route).length;

void main() {
  testWidgets('lists active tasks and can request cancellation', (
    tester,
  ) async {
    final api = await pumpAdmin(tester, const TasksPage(), (api) {
      api.on('GET /api/admin/tasks', [
        {
          'id': 7,
          'kind': 'sync',
          'phase': 'exporting',
          'status': 'running',
          'progress_done': 2,
          'progress_total': 5,
          'retry_count': 0,
          'cancel_requested': false,
          'error': null,
          'result': <String, dynamic>{},
          'created_by': 1,
          'idempotency_key': null,
          'created_at': '2026-10-03T10:00:00Z',
          'started_at': '2026-10-03T10:00:00Z',
          'finished_at': null,
        },
      ]);
      api.on('POST /api/admin/tasks/7/cancel', {
        'id': 7,
        'kind': 'sync',
        'phase': 'exporting',
        'status': 'cancel_requested',
        'progress_done': 2,
        'progress_total': 5,
        'retry_count': 0,
        'cancel_requested': true,
        'error': null,
        'result': <String, dynamic>{},
        'created_by': 1,
        'idempotency_key': null,
        'created_at': '2026-10-03T10:00:00Z',
        'started_at': '2026-10-03T10:00:00Z',
        'finished_at': null,
      });
    });

    expect(find.text('同步'), findsOneWidget);
    expect(find.textContaining('导出目录树 · 2 / 5 · '), findsOneWidget);
    await tester.tap(find.text('取消'));
    await tester.pumpAndSettle();
    expect(api.requests, contains('POST /api/admin/tasks/7/cancel'));
  });

  testWidgets('cancel refreshes the list and disables the button', (
    tester,
  ) async {
    final api = await pumpAdmin(tester, const TasksPage(), (api) {
      api
        ..on('GET /api/admin/tasks', [_task(7, 'running')])
        ..on('POST /api/admin/tasks/7/cancel', null, status: 204);
    });
    expect(_count(api, 'GET /api/admin/tasks'), 1);

    api.on('GET /api/admin/tasks', [_task(7, 'cancel_requested')]);
    await tester.tap(find.text('取消'));
    await tester.pump();
    await tester.pumpAndSettle();

    expect(_count(api, 'POST /api/admin/tasks/7/cancel'), 1);
    expect(_count(api, 'GET /api/admin/tasks'), 2);
    expect(find.text('已请求取消'), findsOneWidget);
    expect(find.text('取消中'), findsNothing);
    final button = tester.widget<TextButton>(
      find.widgetWithText(TextButton, '取消'),
    );
    expect(button.onPressed, isNull, reason: '已请求取消后不能重复提交');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('retry on a failed task calls retry and reloads the list', (
    tester,
  ) async {
    final api = await pumpAdmin(tester, const TasksPage(), (api) {
      api
        ..on('GET /api/admin/tasks', [_task(9, 'failed', error: '来源登录失效')])
        ..on('POST /api/admin/tasks/9/retry', null, status: 204);
    });
    expect(find.textContaining('来源登录失效'), findsOneWidget);
    expect(find.text('取消'), findsNothing);

    api.on('GET /api/admin/tasks', [_task(10, 'running'), _task(9, 'failed')]);
    await tester.tap(find.text('重试').first);
    await tester.pumpAndSettle();

    expect(_count(api, 'POST /api/admin/tasks/9/retry'), 1);
    expect(_count(api, 'GET /api/admin/tasks'), 2);
    expect(find.text('已重新开始'), findsOneWidget);
    expect(find.text('取消'), findsOneWidget, reason: '新任务出现在列表里');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets(
    'cancelled tasks can be retried, a failed retry shows the error',
    (tester) async {
      final api = await pumpAdmin(tester, const TasksPage(), (api) {
        api
          ..on('GET /api/admin/tasks', [_task(11, 'cancelled')])
          ..on('POST /api/admin/tasks/11/retry', {
            'error': {'code': 'task_not_retryable', 'message': '任务没有可重试的输入'},
          }, status: 409);
      });
      await tester.tap(find.text('重试'));
      await tester.pumpAndSettle();

      expect(_count(api, 'POST /api/admin/tasks/11/retry'), 1);
      expect(find.text('任务没有可重试的输入'), findsOneWidget);
      expect(find.text('重试'), findsOneWidget, reason: '失败后按钮恢复可再次点击');
      expect(_count(api, 'GET /api/admin/tasks'), 1, reason: '失败时不刷新');
      await tester.pumpWidget(const SizedBox());
    },
  );

  testWidgets('polls only while a task is running', (tester) async {
    final api = await pumpAdmin(tester, const TasksPage(), (api) {
      api.on('GET /api/admin/tasks', [
        {
          'id': 8,
          'kind': 'backup_create',
          'phase': 'done',
          'status': 'succeeded',
          'progress_done': 0,
          'progress_total': 0,
          'retry_count': 0,
          'cancel_requested': false,
          'error': null,
          'result': <String, dynamic>{},
          'created_by': 1,
          'idempotency_key': null,
          'created_at': '2026-10-03T10:00:00Z',
          'started_at': '2026-10-03T10:00:00Z',
          'finished_at': '2026-10-03T10:01:00Z',
        },
      ]);
    });

    expect(find.text('创建备份'), findsOneWidget);
    expect(find.textContaining(' 结束'), findsOneWidget);
    final before = api.requests.length;
    await tester.pump(const Duration(seconds: 7));
    expect(api.requests.length, before);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('download subtitle tasks and schedules are labelled', (
    tester,
  ) async {
    await pumpAdmin(tester, const TasksPage(), (api) {
      api.on('GET /api/admin/tasks', [
        _task(21, 'running', kind: 'download_subtitles'),
        _task(22, 'succeeded', kind: 'scheduled:download_subtitles'),
      ]);
    });
    expect(find.text('下载字幕'), findsOneWidget);
    expect(find.text('定时下载字幕'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  test('the download subtitles schedule has a label', () {
    expect(scheduledTaskLabel('download_subtitles'), '下载字幕');
  });
}
