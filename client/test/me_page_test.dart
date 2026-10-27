import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/core/api/api_error.dart';
import 'package:iris/core/api/client_device.dart';
import 'package:iris/core/api/token_store.dart';
import 'package:iris/core/providers.dart';
import 'package:iris/features/auth/auth_controller.dart';
import 'package:iris/features/auth/login_page.dart';
import 'package:iris/features/me/me_page.dart';
import 'package:iris/features/me/profile_page.dart';
import 'package:iris/features/me/settings_pages.dart';

import 'support/fake_api.dart';

const _admin = {
  'id': 1,
  'username': 'admin',
  'role': 'admin',
  'disabled': false,
  'allow_subtitle_download': true,
};
const _alice = {
  'id': 2,
  'username': 'alice',
  'role': 'user',
  'disabled': false,
  'allow_subtitle_download': true,
};

SavedAccount _saved(String access, {String refresh = 'alice-refresh'}) =>
    SavedAccount(
      id: 2,
      username: 'alice',
      role: 'user',
      tokens: TokenPair(accessToken: access, refreshToken: refresh),
    );

/// 按令牌认人的假服务端：`GET /api/auth/me` 看 Authorization 头，刷新看请求体。
class _AuthServer implements HttpClientAdapter {
  /// 有效的访问令牌 → 用户
  final Map<String, Map<String, Object>> users = {
    'admin-access': _admin,
    'alice-access': _alice,
  };

  /// 有效的刷新令牌 → 换出来的访问令牌
  final Map<String, String> refreshable = {};
  final List<String> requests = [];
  final Map<String, Map<String, dynamic>?> bodies = {};

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    final route = '${options.method} ${options.path}';
    requests.add(route);
    final body = requestStream == null
        ? null
        : jsonDecode(utf8.decode(await requestStream.expand((c) => c).toList()))
              as Map<String, dynamic>;
    bodies[route] = body;
    switch (route) {
      case 'GET /api/auth/me':
        final token = (options.headers['Authorization'] as String?)
            ?.replaceFirst('Bearer ', '');
        final user = users[token];
        return user == null ? _json(401, _error) : _json(200, user);
      case 'POST /api/auth/refresh':
        final access = refreshable[body!['refresh_token']];
        if (access == null) return _json(401, _error);
        return _json(200, {
          'access_token': access,
          'refresh_token': '$access-refresh',
        });
      case 'POST /api/auth/login':
        return _json(200, {
          'access_token': 'admin-access',
          'refresh_token': 'admin-refresh',
          'user': _admin,
        });
      case 'POST /api/auth/logout':
        return ResponseBody.fromString('', 204);
      case 'GET /api/system/info':
        return _json(200, {
          'product': 'iris',
          'server_id': 'server-1',
          'server_name': '光圈',
          'api_version': 1,
        });
    }
    return _json(404, _error);
  }

  static const _error = {
    'error': {'code': 'unauthorized', 'message': '请重新登录'},
  };

  ResponseBody _json(int status, Object body) => ResponseBody.fromString(
    jsonEncode(body),
    status,
    headers: {
      Headers.contentTypeHeader: [Headers.jsonContentType],
    },
  );

  @override
  void close({bool force = false}) {}
}

/// 管理员已登录，本机还记住了 alice。
({
  _AuthServer server,
  MemoryTokenStore tokens,
  MemoryAccountStore accounts,
  List<Object> overrides,
})
_signedIn({List<SavedAccount>? saved}) {
  final server = _AuthServer();
  final tokens = MemoryTokenStore(
    const TokenPair(accessToken: 'admin-access', refreshToken: 'admin-refresh'),
  );
  final accounts = MemoryAccountStore(saved ?? [_saved('alice-access')]);
  // 和真实客户端一样带上当前令牌；切换时的验证请求自带令牌、跳过这一步
  final dio = Dio(BaseOptions(baseUrl: 'http://test'))
    ..httpClientAdapter = server
    ..interceptors.add(
      InterceptorsWrapper(
        onRequest: (options, handler) async {
          final current = await tokens.read();
          if (options.extra['skipAuth'] != true && current != null) {
            options.headers['Authorization'] = 'Bearer ${current.accessToken}';
          }
          handler.next(options);
        },
      ),
    );
  return (
    server: server,
    tokens: tokens,
    accounts: accounts,
    overrides: [
      apiClientProvider.overrideWithValue(dio),
      apiBaseUrlProvider.overrideWithValue('http://test'),
      tokenStoreProvider.overrideWithValue(tokens),
      accountStoreProvider.overrideWithValue(accounts),
      clientDeviceProvider.overrideWith(
        (ref) async => const ClientDevice(
          id: 'device-1',
          name: 'Chrome · macOS',
          platform: 'Web',
          version: '0.1.0 (3)',
        ),
      ),
    ],
  );
}

void main() {
  group('switching accounts', () {
    late ProviderContainer container;
    late ({
      _AuthServer server,
      MemoryTokenStore tokens,
      MemoryAccountStore accounts,
      List<Object> overrides,
    })
    env;

    Future<void> start({List<SavedAccount>? saved}) async {
      env = _signedIn(saved: saved);
      container = ProviderContainer(overrides: env.overrides.cast());
      addTearDown(container.dispose);
      expect(
        (await container.read(authControllerProvider.future))?.username,
        'admin',
      );
    }

    AuthController controller() =>
        container.read(authControllerProvider.notifier);

    test(
      'switches without a password and remembers the previous account',
      () async {
        await start();
        final epoch = container.read(sessionEpochProvider);

        await controller().switchTo(_saved('alice-access'));

        expect(container.read(authControllerProvider).value?.username, 'alice');
        expect((await env.tokens.read())?.accessToken, 'alice-access');
        final saved = await env.accounts.read();
        expect(saved.map((a) => a.username), ['admin']);
        expect(saved.single.tokens.refreshToken, 'admin-refresh');
        // 换了账号，API 客户端和上面的数据都要重建
        expect(container.read(sessionEpochProvider), greaterThan(epoch));
      },
    );

    test('an expired access token is refreshed before switching', () async {
      await start(saved: [_saved('stale')]);
      env.server.refreshable['alice-refresh'] = 'alice-new';
      env.server.users['alice-new'] = _alice;

      await controller().switchTo(_saved('stale'));

      expect(container.read(authControllerProvider).value?.username, 'alice');
      expect((await env.tokens.read())?.accessToken, 'alice-new');
    });

    test('a rejected account is forgotten and the current one stays', () async {
      await start(saved: [_saved('stale')]);

      await expectLater(
        controller().switchTo(_saved('stale')),
        throwsA(
          isA<ApiError>().having((e) => e.message, 'message', contains('已失效')),
        ),
      );

      expect(container.read(authControllerProvider).value?.username, 'admin');
      expect((await env.tokens.read())?.accessToken, 'admin-access');
      expect(await env.accounts.read(), isEmpty);
    });

    test('adding an account keeps the current one for later', () async {
      await start(saved: []);

      await controller().addAccount();
      expect(container.read(authControllerProvider).value, isNull);
      expect(await env.tokens.read(), isNull);
      expect((await env.accounts.read()).map((a) => a.username), ['admin']);

      // 又用密码登回同一个账号时，以新令牌为准，不再重复记住，旧会话也在服务端注销
      await controller().login('admin', 'secret');
      expect(await env.accounts.read(), isEmpty);
      expect(env.server.bodies['POST /api/auth/logout'], {
        'refresh_token': 'admin-refresh',
      });
    });

    test('logging in reports this device to the server', () async {
      await start(saved: []);
      await controller().logout();

      await controller().login('admin', 'secret');

      expect(env.server.bodies['POST /api/auth/login'], {
        'username': 'admin',
        'password': 'secret',
        'device_id': 'device-1',
        'device_name': 'Chrome · macOS',
        'platform': 'Web',
        'client_version': '0.1.0 (3)',
      });
    });

    test('forgetting an account also signs it out on the server', () async {
      await start();
      await controller().forget(_saved('alice-access'));
      expect(await env.accounts.read(), isEmpty);
      expect(env.server.requests, contains('POST /api/auth/logout'));
    });
  });

  group('me page', () {
    Future<GoRouter> pumpMe(WidgetTester tester, List<Object> overrides) async {
      final router = GoRouter(
        initialLocation: '/me',
        routes: [
          GoRoute(
            path: '/',
            builder: (_, _) => const Scaffold(body: Text('首页')),
          ),
          GoRoute(path: '/me', builder: (_, _) => const MePage()),
          GoRoute(path: '/settings', builder: (_, _) => const Text('设置页')),
        ],
      );
      addTearDown(router.dispose);
      tester.view.physicalSize = const Size(390 * 3, 1600 * 3);
      tester.view.devicePixelRatio = 3;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(
        ProviderScope(
          overrides: overrides.cast(),
          child: MaterialApp.router(theme: buildTheme(), routerConfig: router),
        ),
      );
      await tester.pumpAndSettle();
      return router;
    }

    testWidgets(
      'the first level only has entries, details live one level down',
      (tester) async {
        final router = await pumpMe(tester, _signedIn().overrides);
        expect(find.text('admin'), findsOneWidget);
        // 头像下面写当前服务器名，「服务器」这一行也带服务器名
        expect(find.text('光圈'), findsNWidgets(2));
        for (final entry in [
          '设置',
          '播放列表',
          '我的收藏',
          '我的设备',
          '服务器',
          '切换用户',
          '退出登录',
        ]) {
          expect(find.text(entry), findsOneWidget, reason: entry);
        }
        // 管理后台和关于已经并进设置，一级页面不再单列
        for (final merged in ['管理后台', '关于', '版本 0.1.0 (3)']) {
          expect(find.text(merged), findsNothing, reason: merged);
        }
        // 顺序照 Plex 头像菜单
        expect(
          [
            for (final tile in tester.widgetList<ListTile>(
              find.byType(ListTile),
            ))
              (tile.title! as Text).data,
          ],
          ['设置', '播放列表', '我的收藏', '我的设备', '服务器', '切换用户', '退出登录'],
        );
        for (final moved in ['WebDAV', '修改密码', '默认字幕语言']) {
          expect(find.text(moved), findsNothing);
        }

        await tester.tap(find.text('设置'));
        await tester.pumpAndSettle();
        expect(router.state.uri.path, '/settings');
      },
    );

    testWidgets('switching user from 我的 goes home as the new account', (
      tester,
    ) async {
      final env = _signedIn();
      final router = await pumpMe(tester, env.overrides);

      await tester.tap(find.text('切换用户'));
      await tester.pumpAndSettle();
      expect(find.text('alice'), findsOneWidget);
      expect(find.text('添加账号'), findsOneWidget);

      await tester.tap(find.text('alice'));
      await tester.pumpAndSettle();
      expect(router.state.uri.path, '/');
      expect(find.text('已切换到 alice'), findsOneWidget);
      expect((await env.tokens.read())?.accessToken, 'alice-access');
    });

    testWidgets('an expired account says so inside the switcher', (
      tester,
    ) async {
      await pumpMe(tester, _signedIn(saved: [_saved('stale')]).overrides);
      await tester.tap(find.text('切换用户'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('alice'));
      await tester.pumpAndSettle();
      expect(find.text('alice 的登录已失效，请添加账号重新登录'), findsOneWidget);
      // 失效的账号已经忘掉，当前账号不受影响
      expect(find.text('alice'), findsNothing);
      expect(find.text('admin'), findsWidgets);
    });
  });

  testWidgets('the login page offers remembered accounts', (tester) async {
    final env = _signedIn();
    await env.tokens.clear();
    await tester.pumpWidget(
      ProviderScope(
        overrides: env.overrides.cast(),
        child: MaterialApp(theme: buildTheme(), home: const LoginPage()),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.text('切换到已登录的账号'));
    await tester.pumpAndSettle();
    // 登录页本身就是在添加账号
    expect(find.text('添加账号'), findsNothing);
    await tester.tap(find.text('alice'));
    await tester.pumpAndSettle();
    expect((await env.tokens.read())?.accessToken, 'alice-access');
    expect(await env.accounts.read(), isEmpty);
  });

  group('settings', () {
    testWidgets('closing the subtitle dialog keeps the language', (
      tester,
    ) async {
      final api = await pumpAdmin(tester, const SubtitleSettingsPage(), (api) {
        api
          ..on('GET /api/me/preferences', {'subtitle_language': 'ja'})
          ..on('GET /api/me/webdav', {
            'enabled': true,
            'username': 'admin',
            'url': null,
          });
      });
      expect(find.text('日语'), findsOneWidget);
      await tester.tap(find.text('默认字幕语言'));
      await tester.pumpAndSettle();
      await tester.tapAt(const Offset(5, 5));
      await tester.pumpAndSettle();
      expect(
        api.requests.where((r) => r.startsWith('PATCH /api/me/preferences')),
        isEmpty,
        reason: '点对话框外面关掉不能被当成选了「跟随服务器默认」',
      );
      expect(find.text('日语'), findsOneWidget);
    });

    testWidgets('WebDAV is on by default and lists what to copy', (
      tester,
    ) async {
      final api = await pumpAdmin(tester, const AccountProfilePage(), (api) {
        api
          ..on('GET /api/me/webdav', {
            'enabled': true,
            'username': 'admin',
            'url': 'https://m.example.test/webdav',
          })
          ..on('PATCH /api/me/webdav', {
            'enabled': false,
            'username': 'admin',
            'url': 'https://m.example.test/webdav',
          });
      });
      // 设置页还有别的开关（首页显示我的收藏），只看 WebDAV 这一行的
      final webdavSwitch = find.descendant(
        of: find.widgetWithText(ListTile, 'WebDAV'),
        matching: find.byType(Switch),
      );
      expect(find.text('https://m.example.test/webdav'), findsOneWidget);
      expect(find.byTooltip('复制地址'), findsOneWidget);
      expect(find.byTooltip('复制用户名'), findsOneWidget);
      expect(tester.widget<Switch>(webdavSwitch).value, isTrue);

      api.on('GET /api/me/webdav', {
        'enabled': false,
        'username': 'admin',
        'url': 'https://m.example.test/webdav',
      });
      await tester.tap(find.text('WebDAV'));
      await tester.pumpAndSettle();
      expect(api.bodies['PATCH /api/me/webdav'], {'enabled': false});
      expect(find.byTooltip('复制地址'), findsNothing);
      expect(tester.widget<Switch>(webdavSwitch).value, isFalse);
    });
  });
}
