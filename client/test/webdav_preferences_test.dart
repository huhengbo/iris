import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/features/me/profile_page.dart';

import 'support/fake_api.dart';

const _webdav = 'https://m.example.test/webdav';

Map<String, Object?> _info({
  required bool enabled,
  String? url = _webdav,
  String username = 'alice',
}) => {'enabled': enabled, 'username': username, 'url': url};

Future<FakeAdminApi> _pump(
  WidgetTester tester,
  void Function(FakeAdminApi api) setup,
) => pumpAdmin(tester, const AccountProfilePage(), setup);

/// 设置页里新增了「首页显示我的收藏」开关，WebDAV 的开关要按所在行来找
Finder _webdavSwitch() => find.descendant(
  of: find.widgetWithText(ListTile, 'WebDAV'),
  matching: find.byType(Switch),
);

bool _switchValue(WidgetTester tester) =>
    tester.widget<Switch>(_webdavSwitch()).value;

void main() {
  group('WebDAV settings', () {
    testWidgets('enabled shows address and username with copy buttons', (
      tester,
    ) async {
      await _pump(tester, (api) {
        api.on('GET /api/me/webdav', _info(enabled: true));
      });
      expect(find.text(_webdav), findsOneWidget);
      expect(find.text('alice'), findsOneWidget);
      expect(find.byTooltip('复制地址'), findsOneWidget);
      expect(find.byTooltip('复制用户名'), findsOneWidget);
      expect(find.textContaining('密码就是登录密码'), findsOneWidget);
      expect(_switchValue(tester), isTrue);
    });

    testWidgets('disabled hides the connection details', (tester) async {
      await _pump(tester, (api) {
        api.on('GET /api/me/webdav', _info(enabled: false));
      });
      expect(_switchValue(tester), isFalse);
      expect(find.text(_webdav), findsNothing);
      expect(find.byTooltip('复制地址'), findsNothing);
      expect(find.byTooltip('复制用户名'), findsNothing);
    });

    testWidgets('turning it on sends enabled=true and reveals the details', (
      tester,
    ) async {
      final api = await _pump(tester, (api) {
        api
          ..on('GET /api/me/webdav', _info(enabled: false))
          ..on('PATCH /api/me/webdav', _info(enabled: true));
      });
      api.on('GET /api/me/webdav', _info(enabled: true));
      await tester.tap(_webdavSwitch());
      await tester.pumpAndSettle();

      expect(api.bodies['PATCH /api/me/webdav'], {'enabled': true});
      expect(_switchValue(tester), isTrue);
      expect(find.text(_webdav), findsOneWidget);
      expect(find.text('alice'), findsOneWidget);
    });

    testWidgets('missing public url warns instead of showing an address', (
      tester,
    ) async {
      await _pump(tester, (api) {
        api.on('GET /api/me/webdav', _info(enabled: true, url: null));
      });
      expect(find.textContaining('管理员还没有配置对外访问地址'), findsOneWidget);
      expect(find.byTooltip('复制地址'), findsNothing);
      expect(find.byTooltip('复制用户名'), findsOneWidget);
    });

    testWidgets('a failed save shows the server message and keeps the state', (
      tester,
    ) async {
      final api = await _pump(tester, (api) {
        api
          ..on('GET /api/me/webdav', _info(enabled: true))
          ..on('PATCH /api/me/webdav', {
            'error': {'code': 'internal_error', 'message': '保存失败，请稍后再试'},
          }, status: 500);
      });
      await tester.tap(_webdavSwitch());
      await tester.pumpAndSettle();

      expect(api.bodies['PATCH /api/me/webdav'], {'enabled': false});
      expect(find.text('保存失败，请稍后再试'), findsOneWidget);
      expect(_switchValue(tester), isTrue, reason: '失败后开关仍反映服务端状态');
      expect(find.text(_webdav), findsOneWidget);
      final toggle = tester.widget<Switch>(_webdavSwitch());
      expect(toggle.onChanged, isNotNull, reason: '失败后可以再次操作');
    });

    testWidgets('a failed load shows a retry button that reloads', (
      tester,
    ) async {
      final api = await _pump(tester, (api) {
        api.on('GET /api/me/webdav', {
          'error': {'code': 'internal_error', 'message': '服务暂时不可用'},
        }, status: 500);
      });
      expect(find.text('暂时无法读取状态'), findsOneWidget);
      expect(_webdavSwitch(), findsNothing);

      api.on('GET /api/me/webdav', _info(enabled: true));
      await tester.tap(find.byTooltip('重试'));
      await tester.pumpAndSettle();
      expect(find.text('暂时无法读取状态'), findsNothing);
      expect(_switchValue(tester), isTrue);
      expect(find.text(_webdav), findsOneWidget);
    });
  });
}
