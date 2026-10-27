import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:iris/features/admin/backup_page.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/fake_api.dart';

void main() {
  testWidgets('lists backups and creates a new database backup', (
    tester,
  ) async {
    final api = await pumpAdmin(tester, const BackupPage(), (api) {
      api.on('GET /api/admin/backups', [
        {
          'name': 'iris-20261002T100000Z.sqlite3',
          'size': 2048,
          'created_at': '2026-10-02T10:00:00Z',
          'schema_version': 9,
        },
      ]);
      api.on('POST /api/admin/backups', {
        'name': 'iris-20261002T110000Z.sqlite3',
        'size': 4096,
        'created_at': '2026-10-02T11:00:00Z',
        'schema_version': 9,
      }, status: 201);
    });

    expect(find.text('iris-20261002T100000Z.sqlite3'), findsOneWidget);
    expect(find.textContaining('2.0 KB'), findsOneWidget);

    await tester.tap(find.byTooltip('立即备份'));
    await tester.pumpAndSettle();

    expect(api.requests, contains('POST /api/admin/backups'));
    expect(
      find.textContaining('已创建备份 iris-20261002T110000Z.sqlite3'),
      findsOneWidget,
    );
  });

  testWidgets('exported config is shown and can be copied', (tester) async {
    String? copied;
    tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
      SystemChannels.platform,
      (call) async {
        if (call.method == 'Clipboard.setData') {
          copied = (call.arguments as Map)['text'] as String;
        }
        return null;
      },
    );
    addTearDown(
      () => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        SystemChannels.platform,
        null,
      ),
    );
    await pumpAdmin(tester, const BackupPage(), (api) {
      api.on('GET /api/admin/backups', []);
      api.on('GET /api/admin/backups/config/export', {
        'format_version': 1,
        'server': {'name': '光圈', 'public_url': null},
        'secrets_excluded': true,
      });
    });

    await tester.tap(find.byTooltip('导出安全配置'));
    await tester.pumpAndSettle();
    expect(find.text('安全配置'), findsOneWidget);
    expect(find.textContaining('"name": "光圈"'), findsOneWidget);

    await tester.tap(find.widgetWithText(FilledButton, '复制'));
    await tester.pumpAndSettle();
    expect(copied, contains('"format_version": 1'));
    expect(find.text('已复制配置'), findsOneWidget);
  });

  testWidgets('restore disables other restores while running', (tester) async {
    final api = await pumpAdmin(tester, const BackupPage(), (api) {
      api.on('GET /api/admin/backups', [
        for (final hour in ['10', '11'])
          {
            'name': 'iris-20261002T${hour}0000Z.sqlite3',
            'size': 2048,
            'created_at': '2026-10-02T$hour:00:00Z',
            'schema_version': 9,
          },
      ]);
    });

    await tester.tap(find.text('恢复').first);
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, '恢复'));
    await tester.pump();
    // 恢复请求还没返回：这一行转圈，另一行的恢复不可点
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    final other = tester.widget<TextButton>(
      find.widgetWithText(TextButton, '恢复'),
    );
    expect(other.onPressed, isNull);
    await tester.pumpAndSettle();
    expect(
      api.requests,
      contains(
        'POST /api/admin/backups/iris-20261002T100000Z.sqlite3/restore?confirm=true',
      ),
    );
  });
}
