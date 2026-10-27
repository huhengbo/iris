import 'package:flutter/material.dart';
import 'package:iris/features/admin/setup_page.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/fake_api.dart';

void main() {
  testWidgets('shows ordered setup steps and actionable first step', (
    tester,
  ) async {
    final api = await pumpAdmin(tester, const SetupPage(), (api) {
      api.on('GET /api/admin/setup', {
        'complete': false,
        'current_key': 'source',
        'steps': [
          {
            'key': 'source',
            'title': '添加资源来源',
            'state': 'pending',
            'message': '添加资源来源后才能扫描媒体',
            'action_path': '/settings/manage/sources',
          },
          {
            'key': 'tmdb',
            'title': '配置 TMDB',
            'state': 'pending',
            'message': '配置后才能自动匹配和下载元数据',
            'action_path': '/settings/manage/plugins/builtin.tmdb',
          },
        ],
      });
    });

    expect(find.text('按顺序完成这些步骤即可开始同步媒体。'), findsOneWidget);
    expect(find.text('添加资源来源'), findsOneWidget);
    expect(find.text('配置 TMDB'), findsOneWidget);
    expect(api.requests, contains('GET /api/admin/setup'));
  });

  testWidgets('failed step shows reason, retries and can be snoozed', (
    tester,
  ) async {
    final api = await pumpAdmin(tester, const SetupPage(), (api) {
      api.on('GET /api/admin/setup', {
        'complete': false,
        'current_key': 'source',
        'steps': [
          {
            'key': 'source',
            'title': '添加资源来源',
            'state': 'failed',
            'message': '来源凭据已失效，需要重新登录',
            'action_path': '/settings/manage/sources',
          },
          {
            'key': 'tmdb',
            'title': '配置 TMDB',
            'state': 'complete',
            'message': 'TMDB 已配置',
            'action_path': null,
          },
        ],
      });
    });

    expect(find.text('需要处理：来源凭据已失效，需要重新登录'), findsOneWidget);
    expect(find.byIcon(Icons.error_outline), findsOneWidget);
    expect(find.text('重试'), findsOneWidget);
    expect(find.text('稍后处理'), findsOneWidget);

    final before = api.requests.where((r) => r == 'GET /api/admin/setup');
    final count = before.length;
    await tester.tap(find.text('重试'));
    await tester.pumpAndSettle();
    expect(
      api.requests.where((r) => r == 'GET /api/admin/setup').length,
      count + 1,
    );

    await tester.tap(find.text('稍后处理'));
    await tester.pumpAndSettle();
    expect(find.text('重试'), findsNothing);
    expect(find.text('稍后处理'), findsNothing);
    // 只收起操作，失败原因仍然可见
    expect(find.text('需要处理：来源凭据已失效，需要重新登录'), findsOneWidget);
  });

  testWidgets('complete and pending steps show no retry actions', (
    tester,
  ) async {
    await pumpAdmin(tester, const SetupPage(), (api) {
      api.on('GET /api/admin/setup', {
        'complete': false,
        'current_key': 'tmdb',
        'steps': [
          {
            'key': 'source',
            'title': '添加资源来源',
            'state': 'complete',
            'message': '已有可用来源',
            'action_path': null,
          },
          {
            'key': 'tmdb',
            'title': '配置 TMDB',
            'state': 'pending',
            'message': '配置后才能自动匹配和下载元数据',
            'action_path': '/settings/manage/plugins/builtin.tmdb',
          },
        ],
      });
    });

    expect(find.byIcon(Icons.check), findsOneWidget);
    expect(find.text('重试'), findsNothing);
    expect(find.text('稍后处理'), findsNothing);
  });
}
