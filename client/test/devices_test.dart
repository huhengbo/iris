import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/features/me/devices_page.dart';

import 'support/fake_api.dart';

List<Map<String, Object?>> sessions() => [
  {
    'id': 1,
    'device_name': '客厅电视',
    'platform': 'android',
    'client_version': '0.1.0',
    'ip': '192.168.1.20',
    'kind': 'app',
    'client_name': null,
    'created_at': '2026-10-01T08:00:00Z',
    'expires_at': '2026-10-08T08:00:00Z',
    'last_seen_at': DateTime.now()
        .toUtc()
        .subtract(const Duration(minutes: 5))
        .toIso8601String(),
  },
  {
    'id': 2,
    'device_name': 'iPad',
    'platform': 'ios',
    'client_version': '8.2',
    'ip': '192.168.1.21',
    'kind': 'emby',
    'client_name': 'Infuse',
    'created_at': '2026-10-02T08:00:00Z',
    'expires_at': '9999-12-31T00:00:00Z',
    'last_seen_at': DateTime.now()
        .toUtc()
        .subtract(const Duration(minutes: 1))
        .toIso8601String(),
  },
];

void main() {
  testWidgets('lists app and third-party devices', (tester) async {
    await pumpAdmin(tester, const DevicesPage(), (api) {
      api.on('GET /api/me/sessions', sessions());
    });
    expect(find.text('客厅电视'), findsOneWidget);
    expect(find.text('iPad'), findsOneWidget);
    expect(find.textContaining('第三方客户端 · Infuse'), findsOneWidget);
    expect(find.textContaining('最近活动'), findsNWidgets(2));
  });

  testWidgets('revoking a device asks first and removes it', (tester) async {
    var current = sessions();
    final api = await pumpAdmin(tester, const DevicesPage(), (api) {
      api.routes['GET /api/me/sessions'] = (_) => (200, current);
      api.routes['DELETE /api/me/sessions/2'] = (_) {
        current = current.where((item) => item['id'] != 2).toList();
        return (204, null);
      };
    });

    await tester.tap(find.byTooltip('撤销登录').last);
    await tester.pumpAndSettle();
    expect(find.text('撤销「iPad」的登录？'), findsOneWidget);
    // 第三方客户端的撤销说明和普通设备不同
    expect(find.textContaining('重新添加服务器并登录'), findsOneWidget);

    await tester.tap(find.widgetWithText(FilledButton, '撤销'));
    await tester.pumpAndSettle();
    expect(api.requests, contains('DELETE /api/me/sessions/2'));
    expect(find.text('iPad'), findsNothing);
    expect(find.text('已撤销「iPad」的登录'), findsOneWidget);
  });

  testWidgets('canceling a revoke changes nothing', (tester) async {
    final api = await pumpAdmin(tester, const DevicesPage(), (api) {
      api.on('GET /api/me/sessions', sessions());
    });
    await tester.tap(find.byTooltip('撤销登录').last);
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(TextButton, '取消'));
    await tester.pumpAndSettle();
    expect(api.requests.where((r) => r.startsWith('DELETE ')), isEmpty);
    expect(find.text('iPad'), findsOneWidget);
  });

  testWidgets('an empty list says there is nothing logged in', (tester) async {
    await pumpAdmin(tester, const DevicesPage(), (api) {
      api.on('GET /api/me/sessions', <Object>[]);
    });
    expect(find.text('没有登录中的设备'), findsOneWidget);
  });

  testWidgets('a failed load can be retried', (tester) async {
    final api = await pumpAdmin(tester, const DevicesPage(), (_) {});
    expect(find.text('重试'), findsOneWidget);
    api.on('GET /api/me/sessions', sessions());
    await tester.tap(find.text('重试'));
    await tester.pumpAndSettle();
    expect(find.text('iPad'), findsOneWidget);
  });
}
