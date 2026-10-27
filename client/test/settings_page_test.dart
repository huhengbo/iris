import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/app/tv.dart';
import 'package:iris/features/admin/dashboard_page.dart';
import 'package:iris/features/admin/maintenance_api.dart' show AdminAlert;
import 'package:iris/features/admin/summary_api.dart';
import 'package:iris/features/admin/summary_hints.dart';
import 'package:iris/features/admin/users_page.dart';
import 'package:iris/features/me/settings_page.dart';

import 'support/fake_api.dart';
import 'support/nav_env.dart';

const _summary = 'GET /api/admin/summary';
const _activity = 'GET /api/admin/activity';
const _alerts = 'GET /api/admin/alerts?active=true&limit=200';

Map<String, dynamic> _source({
  String name = '网盘',
  String state = 'ok',
  String? message,
  String? resumeAt,
  String? page,
}) => {
  'source_id': 1,
  'source_name': name,
  'plugin_id': 'source.115',
  'state': state,
  'message': message,
  'resume_at': resumeAt,
  'page': page,
};

Map<String, dynamic> _summaryJson({
  bool setupComplete = true,
  int pending = 0,
  List<Object> sources = const [],
  Map<String, dynamic>? sync,
  List<Object> held = const [],
  String? nextSyncAt,
}) => {
  'sources': sources,
  'sync': sync,
  'held_deletions': held,
  'setup_complete': setupComplete,
  'pending': {'unmatched': pending, 'unplaced': 0},
  'next_sync_at': nextSyncAt,
};

Map<String, dynamic> _job({
  String status = 'succeeded',
  String? error,
  String? waitingReason,
}) => {
  'id': 1,
  'trigger': 'manual',
  'scope': 'all',
  'library_id': null,
  'library_name': null,
  'target': null,
  'status': status,
  'phase': status == 'running' ? 'enrich' : 'done',
  'progress_done': 0,
  'progress_total': 0,
  'stats': {
    'added': 0,
    'removed': 0,
    'renamed': 0,
    'restored': 0,
    'failed': 0,
    'paused': 0,
    'purged': 0,
    'matched': 0,
    'unmatched': 0,
    'ignored': 0,
    'waiting': 0,
    'problems': <String>[],
  },
  'error': error,
  'waiting_until': waitingReason == null ? null : '2026-10-01T09:30:00Z',
  'waiting_reason': waitingReason,
  'started_at': '2026-10-01T08:00:00Z',
  'finished_at': status == 'running' ? null : '2026-10-01T08:10:00Z',
};

/// 设置页的测试路由：和正式路由同样的设置地址，不经过登录守卫。
List<RouteBase> _settingsRoutes() => [
  GoRoute(
    path: '/',
    builder: (_, _) => const Scaffold(body: Text('首页')),
  ),
  GoRoute(path: '/settings', builder: (_, _) => const SettingsPage()),
  for (final entry in allSettingsEntries())
    GoRoute(
      path: entry.path,
      builder: (_, _) => SettingsPage(location: entry.path),
    ),
];

/// 设置页首帧没有会动的加载圈，pumpAndSettle 可能在假接口的零延时定时器之前退出；
/// 先明确推进几帧，让请求落地。
Future<void> _settle(WidgetTester tester) async {
  for (var i = 0; i < 4; i++) {
    await tester.pump(const Duration(milliseconds: 20));
  }
  await tester.pumpAndSettle();
}

Future<GoRouter> _pump(
  WidgetTester tester, {
  bool admin = true,
  bool tv = false,
  String location = '/settings',
  Size size = const Size(390, 3000),
  void Function(FakeAdminApi api)? setup,
}) async {
  final env = navEnv(admin: admin, setup: setup);
  final router = GoRouter(initialLocation: location, routes: _settingsRoutes());
  addTearDown(router.dispose);
  tester.view.physicalSize = Size(size.width * 3, size.height * 3);
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        ...env.overrides.cast(),
        if (tv) televisionProvider.overrideWithValue(true),
      ],
      child: MaterialApp.router(theme: buildTheme(), routerConfig: router),
    ),
  );
  await _settle(tester);
  return router;
}

double _top(WidgetTester tester, String text) =>
    tester.getTopLeft(find.text(text).first).dy;

void main() {
  group('menu by role', () {
    testWidgets('regular users only see account and local settings', (
      tester,
    ) async {
      await _pump(tester, admin: false);

      expect(find.text('账户'), findsOneWidget);
      expect(find.text('本机'), findsOneWidget);
      // AppBar 的「设置」还在，但服务器分组标题不该出现
      expect(find.text('设置'), findsOneWidget);
      for (final hidden in ['管理', '状态']) {
        expect(find.text(hidden), findsNothing, reason: hidden);
      }
      expect(find.textContaining('服务器「'), findsNothing);
      for (final hidden in ['用户', '插件', '仪表盘', '警报', '计划任务', 'Webhook']) {
        expect(find.text(hidden), findsNothing, reason: hidden);
      }
      for (final shown in ['个人资料与密码', '设备与会话', '常规', '播放器', '字幕与音轨', '关于']) {
        expect(find.text(shown), findsOneWidget, reason: shown);
      }
    });

    testWidgets('admins see every group with the server name divider', (
      tester,
    ) async {
      await _pump(tester, setup: _stubHealthy);

      for (final title in ['账户', '本机', '管理', '状态']) {
        expect(find.text(title), findsOneWidget, reason: title);
      }
      // AppBar 和服务器分组都叫「设置」
      expect(find.text('设置'), findsNWidgets(2));
      expect(find.text('服务器「光圈」'), findsOneWidget);
      expect(find.text('个人资料与密码'), findsOneWidget);
      expect(find.text('远程访问'), findsOneWidget);
      expect(find.text('资源来源'), findsOneWidget);
      expect(find.text('故障排查'), findsOneWidget);
      expect(find.text('仪表盘'), findsOneWidget);
      expect(find.text('播放历史'), findsOneWidget);
    });

    testWidgets('TV hides management even for an admin', (tester) async {
      await _pump(
        tester,
        tv: true,
        size: const Size(1920, 1080),
        setup: _stubHealthy,
      );

      expect(find.text('账户'), findsOneWidget);
      expect(find.text('本机'), findsOneWidget);
      expect(find.text('设置'), findsOneWidget);
      for (final hidden in ['管理', '状态', '服务器「光圈」', '用户', '插件', '仪表盘']) {
        expect(find.text(hidden), findsNothing, reason: hidden);
      }
      // TV 用逐层进入的列表，不是宽屏两栏
      expect(find.byType(VerticalDivider), findsNothing);
      expect(find.byIcon(Icons.chevron_right), findsWidgets);
    });
  });

  group('layouts', () {
    testWidgets('phones go one level at a time and keep a back button', (
      tester,
    ) async {
      final router = await _pump(tester, admin: false);

      await tester.tap(find.text('个人资料与密码'));
      await tester.pumpAndSettle();

      expect(router.state.uri.path, '/settings/account/profile');
      expect(find.byType(BackButton), findsOneWidget);
      expect(find.text('个人资料与密码'), findsOneWidget);
      // 头像菜单的「我的」页不再有单独的关于入口，设置里才有
      expect(find.text('密码'), findsOneWidget);
      expect(find.text('外部访问'), findsOneWidget);
    });

    testWidgets('wide keeps a way back after switching sections', (
      tester,
    ) async {
      final router = await _pump(
        tester,
        location: '/',
        size: const Size(1440, 900),
        setup: _stubHealthy,
      );
      // 顶栏扳手是 push 进来的
      router.push('/settings');
      await _settle(tester);
      expect(find.byType(BackButton), findsOneWidget);

      await tester.scrollUntilVisible(
        find.text('仪表盘'),
        300,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.tap(find.text('仪表盘'));
      await _settle(tester);

      expect(router.state.uri.path, '/settings/status/dashboard');
      // 换一项只是替换设置这一页，不是把返回的路由也换掉
      expect(find.byType(BackButton), findsOneWidget);
    });

    testWidgets('wide screens show the menu beside the selected page', (
      tester,
    ) async {
      final router = await _pump(
        tester,
        size: const Size(1440, 900),
        setup: _stubHealthy,
      );

      // 默认选中账户，右栏是个人资料与密码页，没有返回按钮
      expect(find.text('个人资料与密码'), findsNWidgets(2));
      expect(find.byType(BackButton), findsNothing);

      await tester.scrollUntilVisible(
        find.text('用户'),
        200,
        scrollable: find.byType(Scrollable).first,
      );
      await tester.tap(find.text('用户'));
      await tester.pumpAndSettle();

      expect(router.state.uri.path, '/settings/manage/users');
      expect(find.byType(UsersPage), findsOneWidget);
      expect(find.byType(BackButton), findsNothing);
      // 换一项不用重新进入设置
      expect(find.text('账户'), findsOneWidget);
    });
  });

  testWidgets('the setup wizard leads the menu until it is complete', (
    tester,
  ) async {
    await _pump(
      tester,
      setup: (api) {
        api
          ..on(_summary, _summaryJson(setupComplete: false))
          ..on(_alerts, <Object>[]);
      },
    );
    expect(find.text('配置向导'), findsOneWidget);
    expect(_top(tester, '配置向导'), lessThan(_top(tester, '账户')));

    await _pump(tester, setup: _stubHealthy);
    expect(find.text('配置向导'), findsNothing);
  });

  group('attention items', () {
    test('point at the new settings addresses', () {
      final summary = AdminSummary.fromJson({
        ..._summaryJson(
          setupComplete: false,
          pending: 3,
          sources: [_source(state: 'failed', page: 'login')],
        ),
      });
      const alert = AdminAlert(
        id: 1,
        level: 'error',
        message: '同步失败',
        createdAt: null,
      );
      final items = attentionItems(summary, [alert]);

      expect(items.map((item) => item.$1).toList(), [
        '/settings/setup',
        '/settings/manage/plugins/source.115?page=login',
        '/settings/manage/pending',
        '/settings/status/alerts',
      ]);
      // 设置项换右栏，插件详情整页打开
      expect(items.first.$3, isFalse);
      expect(items[1].$3, isTrue);
    });
  });

  group('dashboard attention', () {
    Future<void> pumpDashboard(
      WidgetTester tester, {
      required Map<String, dynamic> summary,
      List<Object> alerts = const [],
    }) async {
      await pumpAdmin(tester, const DashboardPage(), (api) {
        api
          ..on(_summary, summary)
          ..on(_alerts, alerts)
          ..on(_activity, {'now_playing': <Object>[], 'recent': <Object>[]})
          ..on('GET /api/admin/libraries', <Object>[]);
      });
      await _settle(tester);
    }

    testWidgets('a failed source is flagged with its name', (tester) async {
      await pumpDashboard(
        tester,
        summary: _summaryJson(
          sources: [_source(state: 'failed', message: '登录已失效，同步和播放都停了，需要重新登录')],
        ),
      );
      expect(find.text('网盘：需要处理 · 登录已失效，同步和播放都停了，需要重新登录'), findsOneWidget);
    });

    testWidgets('an unfinished source says what is missing', (tester) async {
      await pumpDashboard(
        tester,
        summary: _summaryJson(
          sources: [_source(state: 'pending', message: '还没有接入账号')],
        ),
      );
      expect(find.text('网盘：待完成 · 还没有接入账号'), findsOneWidget);
    });

    testWidgets('an unavailable source says the plugin is not running', (
      tester,
    ) async {
      await pumpDashboard(
        tester,
        summary: _summaryJson(sources: [_source(state: 'unavailable')]),
      );
      expect(find.text('网盘：插件未运行'), findsOneWidget);
    });

    testWidgets('risk control is flagged on the source and on a paused sync', (
      tester,
    ) async {
      await pumpDashboard(
        tester,
        summary: _summaryJson(
          sources: [
            _source(
              state: 'limited',
              message: '请求受到限制',
              resumeAt: '2026-10-01T09:30:00Z',
            ),
          ],
          sync: _job(status: 'running', waitingReason: 'rate_limited'),
        ),
      );
      expect(find.textContaining('网盘：请求已暂停 · 请求受到限制'), findsOneWidget);
      expect(find.text('来源限制了请求，同步已暂停，恢复后自动继续'), findsOneWidget);
    });

    testWidgets('a failed sync shows the reason', (tester) async {
      await pumpDashboard(
        tester,
        summary: _summaryJson(
          sync: _job(status: 'failed', error: '来源登录已失效'),
        ),
      );
      expect(find.text('上次同步失败：来源登录已失效'), findsOneWidget);
    });

    testWidgets('held deletions ask for confirmation', (tester) async {
      await pumpDashboard(
        tester,
        summary: _summaryJson(
          held: [
            {'library_id': 4, 'library_name': '电影', 'due': 6, 'total': 20},
          ],
        ),
      );
      expect(find.text('1 个资料库有删除等待确认'), findsOneWidget);
    });

    testWidgets('active alerts use the most severe colour', (tester) async {
      await pumpDashboard(
        tester,
        summary: _summaryJson(),
        alerts: [
          {
            'id': 1,
            'kind': 'backup',
            'level': 'warning',
            'message': '备份目录空间不足',
            'created_at': '2026-10-01T08:00:00Z',
          },
          {
            'id': 2,
            'kind': 'sync',
            'level': 'error',
            'message': '同步失败',
            'created_at': '2026-10-01T09:00:00Z',
          },
        ],
      );
      final row = find.widgetWithText(ListTile, '2 条警报待处理');
      expect(row, findsOneWidget);
      final icon = tester.widget<Icon>(
        find.descendant(of: row, matching: find.byType(Icon)).first,
      );
      expect(icon.color, CinemaColors.error);
    });

    testWidgets('sync times are shown under the attention list', (
      tester,
    ) async {
      await pumpDashboard(
        tester,
        summary: _summaryJson(sync: _job(), nextSyncAt: '2026-10-02T03:30:00Z'),
      );
      expect(find.textContaining('上次同步 '), findsOneWidget);
      expect(find.textContaining('下次同步 '), findsOneWidget);
    });

    testWidgets('the menu marks the entries that need attention', (
      tester,
    ) async {
      await _pump(
        tester,
        setup: (api) {
          api
            ..on(
              _summary,
              _summaryJson(pending: 1, sources: [_source(state: 'failed')]),
            )
            ..on(_alerts, [
              {
                'id': 1,
                'kind': 'sync',
                'level': 'warning',
                'message': '同步失败',
                'created_at': '2026-10-01T09:00:00Z',
              },
            ]);
        },
      );
      for (final title in ['资源来源', '待处理', '警报']) {
        final tile = find.widgetWithText(ListTile, title);
        expect(tile, findsOneWidget, reason: title);
        final dot = find.descendant(
          of: tile,
          matching: find.byWidgetPredicate(
            (widget) =>
                widget is Container &&
                widget.decoration is BoxDecoration &&
                (widget.decoration! as BoxDecoration).color != null,
          ),
        );
        expect(dot, findsOneWidget, reason: title);
      }
    });
  });

  group('dashboard', () {
    testWidgets('shows attention, now playing and per library stats', (
      tester,
    ) async {
      final now = DateTime.now().toUtc().toIso8601String();
      await pumpAdmin(tester, const DashboardPage(), (api) {
        api
          ..on(_summary, _summaryJson(pending: 3))
          ..on(_alerts, <Object>[])
          ..on(_activity, {
            'now_playing': [
              {
                'username': 'alice',
                'item_id': 7,
                'title': '三体',
                'season_number': 1,
                'episode_number': 2,
                'episode_name': null,
                'position': 900,
                'duration': 2700,
                'at': now,
              },
            ],
            'recent': [
              {
                'username': 'admin',
                'item_id': 3,
                'title': '流浪地球',
                'season_number': null,
                'episode_number': null,
                'episode_name': null,
                'client': 'android',
                'at': now,
              },
            ],
          })
          ..on('GET /api/admin/libraries', [
            {
              'id': 1,
              'name': '电影',
              'kind': 'movie',
              'folders': <Object>[],
              'video_count': 8,
              'missing_count': 1,
              'metadata_provider': 'builtin.tmdb',
              'cover': <Object>[],
              'cover_custom': false,
            },
          ])
          ..on('GET /api/admin/libraries/1/stats', {
            'items': 12,
            'movies': 8,
            'series': 4,
            'episodes': 40,
            'unmatched': 2,
            'missing': 1,
          });
      });
      await _settle(tester);

      expect(find.text('待处理事项'), findsOneWidget);
      expect(find.text('3 项待处理'), findsOneWidget);
      expect(find.text('一切正常'), findsNothing);
      expect(find.text('正在播放'), findsOneWidget);
      expect(find.text('三体 · 第 1 季 第 2 集'), findsOneWidget);
      expect(find.text('alice · 15:00 / 45:00'), findsOneWidget);
      expect(find.byType(LinearProgressIndicator), findsOneWidget);
      expect(find.text('最近播放'), findsOneWidget);
      expect(find.text('流浪地球'), findsOneWidget);
      expect(find.text('admin · Android · 刚刚'), findsOneWidget);
      expect(find.text('资料库统计'), findsOneWidget);
      expect(find.text('电影'), findsOneWidget);
      expect(
        find.text('条目 12 · 电影 8 · 剧集 4 · 单集 40 · 未匹配 2 · 失效 1'),
        findsOneWidget,
      );
    });

    testWidgets('healthy state says everything is fine', (tester) async {
      await pumpAdmin(tester, const DashboardPage(), (api) {
        api
          ..on(_summary, _summaryJson())
          ..on(_alerts, <Object>[])
          ..on(_activity, {'now_playing': <Object>[], 'recent': <Object>[]})
          ..on('GET /api/admin/libraries', <Object>[]);
      });
      await _settle(tester);

      expect(find.text('一切正常'), findsOneWidget);
      expect(find.text('现在没有正在播放的内容'), findsOneWidget);
      expect(find.text('还没有播放记录'), findsOneWidget);
      expect(find.text('还没有资料库'), findsOneWidget);
      expect(find.textContaining('上次同步'), findsNothing);
    });

    testWidgets('each failing block explains itself and offers a retry', (
      tester,
    ) async {
      await pumpAdmin(tester, const DashboardPage(), (_) {});
      await _settle(tester);

      expect(find.text('概况加载失败'), findsOneWidget);
      expect(find.text('正在播放加载失败'), findsOneWidget);
      expect(find.text('最近播放加载失败'), findsOneWidget);
      expect(find.text('资料库统计加载失败'), findsOneWidget);
      expect(find.text('重试'), findsNWidgets(4));
    });
  });
}

void _stubHealthy(FakeAdminApi api) {
  api
    ..on(_summary, _summaryJson())
    ..on(_alerts, <Object>[]);
}
