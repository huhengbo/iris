import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:iris/app/shell.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/app/wide_sidebar.dart';
import 'package:iris/features/auth/account_switcher.dart';

import 'support/fake_api.dart';
import 'support/nav_env.dart';

void main() {
  late GoRouter router;

  Future<FakeAdminApi> pumpShell(
    WidgetTester tester, {
    bool admin = true,
    Size size = const Size(1440, 900),
    void Function(FakeAdminApi api)? setup,
  }) async {
    final env = navEnv(admin: admin, setup: setup);
    router = GoRouter(
      routes: [
        GoRoute(
          path: '/settings/status/dashboard',
          builder: (_, _) => const Scaffold(body: Text('仪表盘')),
        ),
        StatefulShellRoute.indexedStack(
          builder: (_, _, shell) => AppShell(navigationShell: shell),
          branches: [
            StatefulShellBranch(
              routes: [
                GoRoute(
                  path: '/',
                  builder: (_, _) => const Scaffold(body: Text('首页内容')),
                  routes: [
                    GoRoute(
                      path: 'library/:id',
                      builder: (_, state) => Scaffold(
                        body: Text('电影资料库 ${state.pathParameters['id']}'),
                      ),
                    ),
                  ],
                ),
              ],
            ),
            StatefulShellBranch(
              routes: [
                GoRoute(
                  path: '/libraries',
                  builder: (_, _) => const Scaffold(body: Text('资料库列表页')),
                  routes: [
                    GoRoute(
                      path: ':id',
                      builder: (_, state) => Scaffold(
                        body: Text('电影资料库 ${state.pathParameters['id']}'),
                      ),
                    ),
                  ],
                ),
              ],
            ),
            StatefulShellBranch(
              routes: [
                GoRoute(
                  path: '/search',
                  builder: (_, state) => Scaffold(
                    body: Text('搜索页 ${state.uri.queryParameters['q'] ?? ''}'),
                  ),
                ),
              ],
            ),
            StatefulShellBranch(
              routes: [
                GoRoute(
                  path: '/me',
                  builder: (_, _) => const Scaffold(body: Text('我的页')),
                ),
              ],
            ),
          ],
        ),
      ],
    );
    addTearDown(router.dispose);
    tester.view.physicalSize = size;
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(
      ProviderScope(
        overrides: env.overrides.cast(),
        child: MaterialApp.router(theme: buildTheme(), routerConfig: router),
      ),
    );
    await tester.pumpAndSettle();
    return env.api;
  }

  /// 假的管理接口：一条正在播放、一个运行中的后台任务。
  void stubActivity(FakeAdminApi api) => api
    ..on('GET /api/admin/activity', {
      'now_playing': [
        {
          'username': 'alice',
          'item_id': 7,
          'title': '三体',
          'season_number': 1,
          'episode_number': 2,
          'position': 600,
          'duration': 2700,
          'at': '2026-10-24T20:00:00Z',
        },
      ],
      'recent': [],
    })
    ..on('GET /api/admin/tasks', [
      {
        'id': 11,
        'kind': 'refresh_library_metadata',
        'phase': 'working',
        'status': 'running',
        'progress_done': 3,
        'progress_total': 10,
        'cancel_requested': false,
        'error': null,
        'started_at': '2026-10-24T20:00:00.000Z',
        'finished_at': null,
      },
    ]);

  testWidgets('wide screens use the sidebar and top bar instead of the rail', (
    tester,
  ) async {
    await pumpShell(tester);

    expect(find.byType(WideSidebar), findsOneWidget);
    expect(find.byType(NavigationRail), findsNothing);
    expect(find.byType(NavigationBar), findsNothing);
    // 首页、稍后观看、服务器名和服务器名下的资料库
    for (final label in ['首页', '稍后观看', '光圈', '电影', '剧集']) {
      expect(find.text(label), findsOneWidget, reason: label);
    }
    // 侧栏不再有「资料库列表」「搜索」「我的」
    expect(find.text('资料库列表'), findsNothing);
    expect(find.text('搜索'), findsNothing);
    expect(find.text('我的'), findsNothing);
  });

  testWidgets('a library list that fails to load shows one retry row', (
    tester,
  ) async {
    final api = await pumpShell(
      tester,
      setup: (api) => api.on('GET /api/libraries', {
        'error': {'code': 'internal', 'message': '服务器出错了'},
      }, status: 500),
    );
    expect(find.text('服务器出错了'), findsOneWidget);
    expect(find.text('重试'), findsOneWidget);
    expect(find.text('电影'), findsNothing);

    // 服务恢复后点「重试」重新取一遍
    api.on('GET /api/libraries', [
      {
        'id': 1,
        'name': '电影',
        'kind': 'movie',
        'item_count': 12,
        'cover': <Object>[],
      },
    ]);
    await tester.tap(find.text('重试'));
    await tester.pumpAndSettle();
    expect(find.text('电影'), findsOneWidget);
    expect(find.text('重试'), findsNothing);
  });

  testWidgets('admins get a more menu with the library actions on every row', (
    tester,
  ) async {
    await pumpShell(tester);

    expect(find.byTooltip('管理 电影'), findsOneWidget);
    expect(find.byTooltip('管理 剧集'), findsOneWidget);

    await tester.tap(find.byTooltip('管理 电影'));
    await tester.pumpAndSettle();
    for (final item in ['同步此资料库', '刷新元数据', '编辑资料库', '管理推荐', '删除']) {
      expect(find.text(item), findsOneWidget, reason: item);
    }
  });

  testWidgets('viewers get neither the library menu nor the activity icon', (
    tester,
  ) async {
    await pumpShell(tester, admin: false);

    expect(find.text('电影'), findsOneWidget);
    expect(find.byTooltip('管理 电影'), findsNothing);
    expect(find.byTooltip('活动'), findsNothing);
  });

  testWidgets('the activity menu lists what is playing and running', (
    tester,
  ) async {
    await pumpShell(tester, setup: stubActivity);

    await tester.tap(find.byTooltip('活动'));
    await tester.pumpAndSettle();
    expect(find.text('正在播放'), findsOneWidget);
    expect(find.text('三体 · 第 1 季 第 2 集'), findsOneWidget);
    expect(find.text('后台任务'), findsOneWidget);
    expect(find.text('刷新资料库元数据'), findsOneWidget);
    expect(find.text('查看仪表盘'), findsOneWidget);

    await tester.tap(find.text('查看仪表盘'));
    await tester.pumpAndSettle();
    expect(router.state.uri.path, '/settings/status/dashboard');
    expect(find.text('仪表盘'), findsOneWidget);
  });

  testWidgets('the sidebar and top bar fit at the wide breakpoint', (
    tester,
  ) async {
    await pumpShell(tester, size: const Size(840, 700));

    expect(find.byType(WideSidebar), findsOneWidget);
    expect(find.byType(TextField), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('narrow screens keep the four bottom destinations', (
    tester,
  ) async {
    await pumpShell(tester, size: const Size(390, 844));

    expect(find.byType(NavigationBar), findsOneWidget);
    expect(find.byType(WideSidebar), findsNothing);
    for (final label in ['首页', '资料库', '搜索', '我的']) {
      expect(find.text(label), findsOneWidget, reason: label);
    }
  });

  testWidgets('the sidebar opens a library and highlights it', (tester) async {
    await pumpShell(tester);
    expect(
      tester.widget<Text>(find.text('电影')).style?.color,
      CinemaColors.text1,
    );

    await tester.tap(find.text('电影'));
    await tester.pumpAndSettle();

    expect(router.state.uri.path, '/libraries/1');
    expect(find.text('电影资料库 1'), findsOneWidget);
    expect(
      tester.widget<Text>(find.text('电影')).style?.color,
      CinemaColors.amber,
    );
  });

  testWidgets('the search box takes typing in place', (tester) async {
    await pumpShell(tester);
    final before = router.state.uri.path;

    await tester.tap(find.byType(TextField));
    await tester.pumpAndSettle();
    expect(router.state.uri.path, before);
  });

  testWidgets('submitting the search box carries the keyword', (tester) async {
    await pumpShell(tester);

    await tester.enterText(find.byType(TextField), '三体');
    await tester.testTextInput.receiveAction(TextInputAction.search);
    await tester.pumpAndSettle();

    expect(router.state.uri.path, '/search');
    expect(router.state.uri.queryParameters['q'], '三体');
  });

  testWidgets('the avatar menu has the same entries as the phone me page', (
    tester,
  ) async {
    await pumpShell(tester);

    await tester.tap(find.byType(AccountAvatar));
    await tester.pumpAndSettle();
    for (final entry in ['设置', '播放列表', '我的收藏', '我的设备', '切换用户', '退出登录']) {
      expect(find.text(entry), findsOneWidget, reason: entry);
    }
    // 管理后台和关于并进设置，头像菜单里不再单列
    for (final merged in ['管理后台', '关于']) {
      expect(find.text(merged), findsNothing, reason: merged);
    }
  });
}
