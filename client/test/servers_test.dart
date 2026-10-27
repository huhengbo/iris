import 'package:iris/app/theme.dart';
import 'package:iris/app/tv.dart';
import 'package:iris/core/api/api_error.dart';
import 'package:iris/core/server.dart';
import 'package:iris/core/servers.dart';
import 'package:iris/features/auth/auth_controller.dart';
import 'package:iris/features/servers/servers_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

const _living = SavedServer(
  id: 's1',
  name: '客厅',
  url: 'http://192.168.1.10:8080',
);
const _office = SavedServer(
  id: 's2',
  name: '公司',
  url: 'https://media.example.com',
);

class _SignedOut extends AuthController {
  @override
  Future<AppUser?> build() async => null;
}

class _Harness {
  _Harness(ServerList initial) : store = MemoryServerStore(initial);

  final MemoryServerStore store;
  final List<String> probed = [];
  final List<String> forgotten = [];
  Object? probeError;

  Future<ServerInfo> probe(String url) async {
    probed.add(url);
    if (probeError case final error?) throw error;
    return const ServerInfo(id: 's3', name: '书房', apiVersion: 1);
  }
}

Future<(_Harness, GoRouter)> pumpServers(
  WidgetTester tester, [
  ServerList initial = const ServerList(),
  bool television = false,
]) async {
  final harness = _Harness(initial);
  final router = GoRouter(
    initialLocation: '/servers',
    routes: [
      GoRoute(path: '/', builder: (_, _) => const Text('首页')),
      GoRoute(path: '/servers', builder: (_, _) => const ServersPage()),
    ],
  );
  addTearDown(router.dispose);
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        manageServersProvider.overrideWithValue(true),
        serverStoreProvider.overrideWithValue(harness.store),
        serverProbeProvider.overrideWithValue(harness.probe),
        forgetServerTokensProvider.overrideWithValue(
          (id) async => harness.forgotten.add(id),
        ),
        authControllerProvider.overrideWith(_SignedOut.new),
      ],
      child: MaterialApp.router(
        theme: buildTheme(),
        builder: television ? televisionBuilder : null,
        routerConfig: router,
      ),
    ),
  );
  await tester.pumpAndSettle();
  return (harness, router);
}

String location(GoRouter router) => router.state.uri.toString();

void main() {
  test('server addresses are normalized, junk is rejected', () {
    expect(
      normalizeServerUrl(' 192.168.1.10:8080/ '),
      'http://192.168.1.10:8080',
    );
    expect(
      normalizeServerUrl('https://media.example.com/cinema//'),
      'https://media.example.com/cinema',
    );
    for (final bad in [
      '',
      '   ',
      'ftp://x.example',
      'http://',
      'http://a.example/?x=1',
      'http://a.example/#x',
      'http://user:pw@a.example',
    ]) {
      expect(normalizeServerUrl(bad), isNull, reason: bad);
    }
  });

  testWidgets('first run: add a server and enter it', (tester) async {
    final (harness, router) = await pumpServers(tester);
    expect(find.text('连接你的光圈服务器'), findsOneWidget);

    await tester.tap(find.text('添加服务器'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), 'not a url://');
    await tester.tap(find.text('连接'));
    await tester.pumpAndSettle();
    expect(find.textContaining('请输入服务器地址'), findsOneWidget);
    expect(harness.probed, isEmpty);

    await tester.enterText(find.byType(TextField), '192.168.1.20:8080/');
    await tester.tap(find.text('连接'));
    await tester.pumpAndSettle();
    expect(harness.probed, ['http://192.168.1.20:8080']);
    expect(location(router), '/');

    final saved = await harness.store.read();
    expect(saved.current?.id, 's3');
    expect(saved.current?.name, '书房');
    expect(saved.current?.url, 'http://192.168.1.20:8080');
  });

  testWidgets('an address that is not an Iris server stays in the dialog', (
    tester,
  ) async {
    final (harness, router) = await pumpServers(tester);
    harness.probeError = const ApiError(
      code: 'not_iris',
      message: '这个地址不是光圈服务器',
    );
    await tester.tap(find.text('添加服务器'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), 'nas.local');
    await tester.tap(find.text('连接'));
    await tester.pumpAndSettle();
    expect(find.text('这个地址不是光圈服务器'), findsOneWidget);
    expect(location(router), '/servers');
    expect((await harness.store.read()).servers, isEmpty);
  });

  testWidgets('on TV the address field stays visible above the keyboard', (
    tester,
  ) async {
    // 1080p 电视，密度 2，即 960×540dp；输入法占去下面 260dp
    tester.view.physicalSize = const Size(1920, 1080);
    tester.view.devicePixelRatio = 2;
    // 带状态栏的盒子，空间更紧
    tester.view.padding = const FakeViewPadding(top: 48);
    addTearDown(tester.view.reset);
    await pumpServers(tester, const ServerList(), true);
    await tester.tap(find.text('添加服务器'));
    await tester.pumpAndSettle();
    await tester.showKeyboard(find.byType(TextField));
    tester.view.viewInsets = const FakeViewPadding(bottom: 520);
    await tester.pumpAndSettle();
    // 真机上输入法弹出后会自动滚到光标处，测试环境不触发，手动滚过去
    await tester.ensureVisible(find.byType(TextField));
    await tester.pumpAndSettle();

    final field = tester.getRect(find.byType(TextField));
    // AlertDialog 自己铺满屏幕，对话框的面板是里面的 Material
    final dialog = tester.getRect(
      find
          .descendant(
            of: find.byType(AlertDialog),
            matching: find.byType(Material),
          )
          .first,
    );
    // 输入框没有被挤扁，整个露在按钮上方
    expect(field.height, greaterThanOrEqualTo(48));
    expect(field.top, greaterThanOrEqualTo(dialog.top));
    expect(
      field.bottom,
      lessThanOrEqualTo(tester.getTopLeft(find.text('取消')).dy),
    );
    expect(dialog.bottom, lessThanOrEqualTo(540 - 260));
  });

  testWidgets('switch to another server and remove one', (tester) async {
    final (harness, router) = await pumpServers(
      tester,
      const ServerList(servers: [_living, _office], currentId: 's1'),
    );
    expect(find.text('当前'), findsOneWidget);

    // 删除要确认，同时清掉本机为它存的登录信息
    await tester.tap(find.byTooltip('更多').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('删除'));
    await tester.pumpAndSettle();
    expect(find.text('删除「公司」？'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, '删除'));
    await tester.pumpAndSettle();
    expect(harness.forgotten, ['s2']);
    expect(find.text('公司'), findsNothing);
    expect((await harness.store.read()).currentId, 's1');

    await tester.tap(find.text('添加服务器'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), 'http://10.0.0.5');
    await tester.tap(find.text('连接'));
    await tester.pumpAndSettle();
    expect(location(router), '/');
    expect((await harness.store.read()).servers.map((s) => s.id), ['s1', 's3']);

    router.go('/servers');
    await tester.pumpAndSettle();
    await tester.tap(find.text('客厅'));
    await tester.pumpAndSettle();
    expect((await harness.store.read()).currentId, 's1');
    expect(location(router), '/');
  });
}
