import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/core/providers.dart';
import 'package:iris/features/admin/health_page.dart';

import 'support/fake_api.dart';

/// 健康页加一个插件详情占位路由，用来核对来源检查的跳转地址。
Future<({GoRouter router, FakeAdminApi api})> _pumpHealth(
  WidgetTester tester,
  void Function(FakeAdminApi api) setup,
) async {
  final api = FakeAdminApi();
  setup(api);
  final dio = Dio(BaseOptions(baseUrl: 'http://test'))..httpClientAdapter = api;
  final router = GoRouter(
    initialLocation: '/settings/manage/troubleshooting/health',
    routes: [
      GoRoute(
        path: '/settings/manage/troubleshooting/health',
        builder: (_, _) => const HealthPage(),
      ),
      GoRoute(
        path: '/settings/manage/plugins/:id',
        builder: (_, state) => Scaffold(body: Text('插件页 ${state.uri}')),
      ),
    ],
  );
  addTearDown(router.dispose);
  tester.view.physicalSize = const Size(390 * 3, 1600 * 3);
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        apiClientProvider.overrideWithValue(dio),
        apiBaseUrlProvider.overrideWithValue('http://test'),
      ],
      child: MaterialApp.router(theme: buildTheme(), routerConfig: router),
    ),
  );
  await tester.pumpAndSettle();
  return (router: router, api: api);
}

void main() {
  testWidgets('renders every health check and follows its action path', (
    tester,
  ) async {
    final (:router, :api) = await _pumpHealth(tester, (api) {
      api.on('GET /api/admin/health', {
        'checked_at': '2026-10-02T10:00:00Z',
        'schema_version': 9,
        'checks': [
          {
            'key': 'source:1',
            'title': '网盘',
            'state': 'error',
            'message': '凭据已失效，需要重新登录',
            'action_path': '/settings/manage/plugins/source.115?page=login',
          },
          {
            'key': 'database',
            'title': '数据库',
            'state': 'ok',
            'message': '完整性检查通过',
            'action_path': null,
          },
        ],
      });
    });

    expect(find.text('网盘'), findsOneWidget);
    expect(find.text('凭据已失效，需要重新登录'), findsOneWidget);
    expect(find.text('数据库'), findsOneWidget);
    expect(find.textContaining('数据库结构 9'), findsOneWidget);
    // 来源就是健康检查本身，不再另取来源状态
    expect(find.text('资源来源'), findsNothing);
    expect(api.requests, contains('GET /api/admin/health'));
    expect(api.requests, isNot(contains('GET /api/admin/resources/status')));

    await tester.tap(find.text('网盘'));
    await tester.pumpAndSettle();
    expect(
      router.state.uri.toString(),
      '/settings/manage/plugins/source.115?page=login',
    );
  });

  testWidgets('shows new health items with their recovery links', (
    tester,
  ) async {
    await pumpAdmin(tester, const HealthPage(), (api) {
      api.on('GET /api/admin/health', {
        'checked_at': '2026-10-02T10:00:00Z',
        'schema_version': 9,
        'checks': [
          {
            'key': 'last_sync_success',
            'title': '最近成功同步',
            'state': 'ok',
            'message': '完成于 2026-10-01T08:00:00+00:00',
            'action_path': null,
          },
          {
            'key': 'failed_task',
            'title': '后台任务',
            'state': 'warning',
            'message': 'rematch 于 2026-10-02 失败：网络错误',
            'action_path': '/settings/status/tasks',
          },
          {
            'key': 'missing_files',
            'title': '失效文件',
            'state': 'warning',
            'message': '2 个视频文件在来源上已找不到',
            'action_path': '/settings/status/sync',
          },
          {
            'key': 'image_cache',
            'title': '图片缓存',
            'state': 'ok',
            'message': '已缓存 12 张图片',
            'action_path': null,
          },
        ],
      });
    });

    expect(find.text('最近成功同步'), findsOneWidget);
    expect(find.text('后台任务'), findsOneWidget);
    expect(find.textContaining('网络错误'), findsOneWidget);
    expect(find.text('2 个视频文件在来源上已找不到'), findsOneWidget);
    expect(find.text('已缓存 12 张图片'), findsOneWidget);
    expect(find.byIcon(Icons.chevron_right), findsNWidgets(2));
    expect(find.byKey(const ValueKey('health-stale')), findsNothing);
  });

  testWidgets('keeps the last report and marks it stale when refresh fails', (
    tester,
  ) async {
    final api = await pumpAdmin(tester, const HealthPage(), (api) {
      api.on('GET /api/admin/health', {
        'checked_at': '2026-10-02T10:00:00Z',
        'schema_version': 9,
        'checks': [
          {
            'key': 'database',
            'title': '数据库',
            'state': 'ok',
            'message': '完整性检查通过',
            'action_path': null,
          },
        ],
      });
    });
    expect(find.byKey(const ValueKey('health-stale')), findsNothing);

    api.on('GET /api/admin/health', {
      'error': {'code': 'internal_error', 'message': '服务暂时不可用'},
    }, status: 500);
    await tester.tap(find.byTooltip('刷新'));
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('health-stale')), findsOneWidget);
    expect(find.textContaining('刷新失败'), findsOneWidget);
    expect(find.text('完整性检查通过'), findsOneWidget);
    expect(find.text('重试'), findsOneWidget);

    api.on('GET /api/admin/health', {
      'checked_at': '2026-10-02T10:05:00Z',
      'schema_version': 9,
      'checks': [
        {
          'key': 'database',
          'title': '数据库',
          'state': 'ok',
          'message': '完整性检查通过',
          'action_path': null,
        },
      ],
    });
    await tester.tap(find.text('重试'));
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('health-stale')), findsNothing);
  });
}
