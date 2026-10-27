import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/features/admin/activity_page.dart';
import 'package:iris/features/admin/alerts_page.dart';
import 'package:iris/features/admin/logs_page.dart';
import 'package:iris/features/admin/maintenance_api.dart';
import 'package:iris/features/admin/recycle_bin_page.dart';
import 'package:iris/features/admin/schedules_page.dart';
import 'package:iris/features/admin/webhooks_page.dart';

import 'support/fake_api.dart';

const _history = 'GET /api/admin/activity/history?offset=0&limit=50';
const _stats = 'GET /api/admin/activity/stats';
const _alerts = 'GET /api/admin/alerts?active=true&limit=200';
const _webhooks = 'GET /api/admin/webhooks';

Map<String, dynamic> _play(int id, {required String mode, String? ended}) => {
  'id': id,
  'user_id': 1,
  'playable_id': 3,
  'version_id': 4,
  'client': mode == 'builtin' ? 'android' : 'web',
  'mode': mode,
  'created_at': DateTime.now().toUtc().toIso8601String(),
  'ended_at': ended,
  'watched_seconds': 1500,
  'username': 'alice',
  'title': '片名 $id',
  'library_id': 1,
};

Map<String, dynamic> _hook({int enabled = 1}) => {
  'id': 7,
  'url': 'https://example.com/hook',
  'events': '["alert"]',
  'enabled': enabled,
  'created_at': '2026-10-01T08:00:00Z',
  'updated_at': '2026-10-01T08:00:00Z',
};

void main() {
  test('only the builtin and system players can be stopped', () {
    PlayHistoryEntry entry(String mode, {String? ended}) =>
        PlayHistoryEntry.fromJson(_play(1, mode: mode, ended: ended));
    expect(entry('builtin').stoppable, isTrue);
    expect(entry('android').stoppable, isTrue);
    expect(entry('ios').stoppable, isTrue);
    expect(entry('web').stoppable, isFalse);
    expect(entry('external').stoppable, isFalse);
    expect(entry('builtin', ended: '2026-10-01T08:00:00Z').stoppable, isFalse);
  });

  testWidgets('history shows stats and stops only stoppable sessions', (
    tester,
  ) async {
    final api = await pumpAdmin(tester, const ActivityPage(), (api) {
      api.on(_stats, {
        'plays': 3,
        'watched_seconds': 5400,
        'by_client': [
          {'mode': 'builtin', 'count': 2},
          {'mode': 'web', 'count': 1},
        ],
        'top_items': <Object>[],
      });
      api.on(_history, {
        'items': [
          _play(1, mode: 'web'),
          _play(2, mode: 'builtin'),
          _play(3, mode: 'external'),
        ],
        'total': 3,
      });
      api.on('POST /api/admin/activity/sessions/2/stop', {
        'event_id': 2,
        'stopped': true,
      });
    });
    expect(find.text('1 小时 30 分'), findsOneWidget);
    expect(find.text('内置播放器 2 次 · 网页 1 次'), findsOneWidget);
    expect(find.text('alice · 网页 · 刚刚 · 观看 25 分钟'), findsOneWidget);
    expect(
      find.text('alice · Android · 内置播放器 · 刚刚 · 观看 25 分钟'),
      findsOneWidget,
    );
    // 网页和外部播放器停不了
    expect(find.byTooltip('停止播放'), findsOneWidget);

    await tester.tap(find.byTooltip('停止播放'));
    await tester.pumpAndSettle();
    expect(find.text('停止「片名 2」的播放？'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, '停止播放'));
    await tester.pumpAndSettle();
    expect(api.requests, contains('POST /api/admin/activity/sessions/2/stop'));
    expect(find.text('已请求停止播放'), findsOneWidget);
  });

  testWidgets('alerts are colored by level and can be resolved', (
    tester,
  ) async {
    var alerts = <Object>[
      {
        'id': 1,
        'kind': 'sync',
        'level': 'error',
        'message': '同步失败',
        'created_at': DateTime.now().toUtc().toIso8601String(),
      },
      {
        'id': 2,
        'kind': 'disk',
        'level': 'info',
        'message': '已清理缓存',
        'created_at': DateTime.now().toUtc().toIso8601String(),
      },
    ];
    final api = await pumpAdmin(tester, const AlertsPage(), (api) {
      api.routes[_alerts] = (_) => (200, alerts);
      api.routes['POST /api/admin/alerts/1'] = (_) {
        alerts = [alerts.last];
        return (200, {'id': 1});
      };
    });
    Icon iconOf(String message) => tester.widget<Icon>(
      find.descendant(
        of: find.widgetWithText(ListTile, message),
        matching: find.byType(Icon),
      ),
    );
    expect(iconOf('同步失败').color, CinemaColors.error);
    expect(iconOf('已清理缓存').color, CinemaColors.text2);

    await tester.tap(find.text('标记已处理').first);
    await tester.pumpAndSettle();
    expect(api.bodies['POST /api/admin/alerts/1'], {
      'read': true,
      'resolved': true,
    });
    expect(find.text('同步失败'), findsNothing);
    expect(find.text('已清理缓存'), findsOneWidget);
  });

  testWidgets('no active alerts shows an empty state', (tester) async {
    await pumpAdmin(tester, const AlertsPage(), (api) {
      api.on(_alerts, <Object>[]);
    });
    expect(find.text('没有待处理的警报'), findsOneWidget);
  });

  testWidgets('deleting a webhook asks for confirmation', (tester) async {
    final api = await pumpAdmin(tester, const WebhooksPage(), (api) {
      api.on(_webhooks, [_hook()]);
      api.on('DELETE /api/admin/webhooks/7', null, status: 204);
    });
    expect(find.text('已启用 · 系统警报'), findsOneWidget);

    Future<void> askToDelete() async {
      await tester.tap(find.byTooltip('更多操作'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('删除'));
      await tester.pumpAndSettle();
      expect(find.text('删除 Webhook？'), findsOneWidget);
    }

    await askToDelete();
    await tester.tap(find.text('取消'));
    await tester.pumpAndSettle();
    expect(api.requests, isNot(contains('DELETE /api/admin/webhooks/7')));

    await askToDelete();
    await tester.tap(find.widgetWithText(FilledButton, '删除'));
    await tester.pumpAndSettle();
    expect(api.requests, contains('DELETE /api/admin/webhooks/7'));
    expect(find.text('已删除 Webhook'), findsOneWidget);
  });

  testWidgets('sending a test reports the delivery result', (tester) async {
    await pumpAdmin(tester, const WebhooksPage(), (api) {
      api.on(_webhooks, [_hook()]);
      api.on('POST /api/admin/webhooks/7/test', {'delivery_id': 9});
      api.on('GET /api/admin/webhooks/7/deliveries', [
        {'id': 9, 'event': 'test', 'status': 'failed', 'response': '连接超时'},
      ]);
    });
    await tester.tap(find.byTooltip('更多操作'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('发送测试'));
    await tester.pumpAndSettle();
    expect(find.text('测试投递失败：连接超时'), findsOneWidget);
  });

  testWidgets('creating a webhook checks the address', (tester) async {
    final api = await pumpAdmin(tester, const WebhooksPage(), (api) {
      api.on(_webhooks, <Object>[]);
      api.on('POST /api/admin/webhooks', {'id': 1}, status: 201);
    });
    expect(find.text('还没有 Webhook'), findsOneWidget);
    await tester.tap(find.text('新建 Webhook'));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField), 'example.com');
    await tester.tap(find.text('添加'));
    await tester.pumpAndSettle();
    expect(find.text('请填写 http:// 或 https:// 开头的地址'), findsOneWidget);
    expect(api.requests, isNot(contains('POST /api/admin/webhooks')));

    await tester.enterText(find.byType(TextField), 'https://example.com/hook');
    await tester.tap(find.text('添加'));
    await tester.pumpAndSettle();
    expect(api.bodies['POST /api/admin/webhooks'], {
      'url': 'https://example.com/hook',
      'events': ['*'],
    });
    expect(find.text('已添加 Webhook'), findsOneWidget);
  });

  testWidgets('scheduled tasks read as schedules and report a run', (
    tester,
  ) async {
    final api = await pumpAdmin(tester, const SchedulesPage(), (api) {
      api.on('GET /api/admin/scheduled-tasks', [
        {
          'key': 'backup',
          'enabled': 1,
          'interval': 86400,
          'last_run_at': null,
          'last_task_id': null,
        },
        {
          'key': 'vacuum',
          'enabled': 0,
          'interval': 2592000,
          'last_run_at': null,
          'last_task_id': null,
        },
      ]);
      api.on('POST /api/admin/scheduled-tasks/backup/run', {
        'task_id': 5,
      }, status: 202);
      api.on('GET /api/admin/tasks/5', {
        'id': 5,
        'kind': 'scheduled:backup',
        'phase': 'done',
        'status': 'succeeded',
        'progress_done': 0,
        'progress_total': 0,
        'cancel_requested': false,
        'error': null,
        'started_at': '2026-10-01T08:00:00Z',
        'finished_at': '2026-10-01T08:00:01Z',
      });
    });
    expect(find.text('自动备份'), findsOneWidget);
    expect(find.text('每天 · 还没运行过 · 即将运行'), findsOneWidget);
    expect(find.text('压缩数据库'), findsOneWidget);
    expect(find.text('已停用 · 还没运行过'), findsOneWidget);

    await tester.tap(find.byTooltip('立即运行').first);
    await tester.pumpAndSettle();
    expect(api.requests, contains('GET /api/admin/tasks/5'));
    expect(find.text('「自动备份」已完成'), findsOneWidget);
  });

  testWidgets('recycle bin purges a library after confirmation', (
    tester,
  ) async {
    var files = <Object>[
      {
        'media_file_id': 1,
        'rel_path': '电影/流浪地球/流浪地球.mkv',
        'size': 1073741824,
        'missing_at': DateTime.now().toUtc().toIso8601String(),
        'library_id': 1,
        'library_name': '电影',
        'media_item_id': 3,
        'title': '流浪地球',
      },
      {
        'media_file_id': 2,
        'rel_path': '剧集/未知.mkv',
        'size': null,
        'missing_at': null,
        'library_id': 2,
        'library_name': '剧集',
        'media_item_id': null,
        'title': null,
      },
    ];
    final api = await pumpAdmin(tester, const RecycleBinPage(), (api) {
      api.routes['GET /api/admin/recycle-bin'] = (_) => (200, files);
      api.routes['POST /api/admin/recycle-bin/empty?library_id=1'] = (_) {
        files = [files.last];
        return (202, {'task_id': 1, 'deleted': 1});
      };
    });
    expect(find.text('流浪地球'), findsOneWidget);
    expect(find.text('1.0 GB · 刚刚 移入'), findsOneWidget);
    expect(find.text('未知.mkv'), findsOneWidget);
    expect(find.text('没归入条目 · 大小未知'), findsOneWidget);

    await tester.tap(find.text('清空此资料库').first);
    await tester.pumpAndSettle();
    expect(find.text('清空「电影」的回收站？'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, '清空'));
    await tester.pumpAndSettle();
    expect(
      api.requests,
      contains('POST /api/admin/recycle-bin/empty?library_id=1'),
    );
    expect(find.text('已清理 1 个视频的记录'), findsOneWidget);
    expect(find.text('流浪地球'), findsNothing);
  });

  testWidgets('logs list newest first with readable times', (tester) async {
    await pumpAdmin(tester, const LogsPage(), (api) {
      api.on('GET /api/admin/logs', [
        {
          'id': 1.0,
          'at': '2026-10-01T08:00:05+00:00',
          'level': 'info',
          'module': 'app.sync',
          'message': '同步开始',
        },
        {
          'id': 2.0,
          'at': '2026-10-01T08:00:09+00:00',
          'level': 'error',
          'module': 'app.sync',
          'message': '同步失败',
        },
      ]);
    });
    expect(
      tester.getTopLeft(find.text('同步失败')).dy,
      lessThan(tester.getTopLeft(find.text('同步开始')).dy),
    );
    expect(find.textContaining(':09 · app.sync'), findsOneWidget);
    expect(find.textContaining('T08'), findsNothing);
  });
}
