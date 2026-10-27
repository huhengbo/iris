import 'package:dio/dio.dart';
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/app/tv.dart';
import 'package:iris/core/providers.dart';
import 'package:iris/features/auth/auth_controller.dart';
import 'package:iris/features/detail/detail_page.dart';
import 'package:iris/features/library/browse_api.dart';
import 'package:iris/features/library/poster_card.dart';

import 'support/fake_api.dart';

class _User extends AuthController {
  _User(this.role);
  final String role;

  @override
  Future<AppUser?> build() async => AppUser(
    id: 1,
    username: 'u',
    role: role,
    disabled: false,
    allowSubtitleDownload: true,
  );
}

const _longTitle = '这是一个非常非常非常非常非常非常非常非常非常非常长的片名 The Extremely Long Title';
const _subtitle = '一行同样很长很长很长很长很长很长很长很长的补充信息';

/// 海报卡「⋯」和长按都要先取详情：收藏、稍后观看的状态和可播放版本都在里面。
Map<String, Object?> _detail({bool watchlisted = true}) => {
  'id': 1,
  'library_id': 1,
  'kind': 'movie',
  'title': _longTitle,
  'original_title': null,
  'year': 2024,
  'rating': 8.0,
  'genres': <String>[],
  'runtime': 120,
  'poster': <Object>[],
  'backdrop': <Object>[],
  'watched': false,
  'favorite': false,
  'watchlisted': watchlisted,
  'en_title': null,
  'release_date': null,
  'overview': null,
  'content_rating': null,
  'studios': <Object>[],
  'countries': <Object>[],
  'ratings': <Object>[],
  'thumb': <Object>[],
  'logo': <Object>[],
  'directors': <Object>[],
  'writers': <Object>[],
  'cast': <Object>[],
  'versions': [
    {
      'id': 1,
      'label': '4K · 60 GB',
      'size': null,
      'subtitles': <Object>[],
      'media': null,
    },
  ],
  'seasons': <Object>[],
  'next_episode': null,
  'progress': null,
};

/// 把海报卡挂在首页，详情和条目编辑页各留一条路由，点卡片能真的进详情。
Future<(FakeAdminApi, GoRouter)> _pumpCard(
  WidgetTester tester, {
  String role = 'admin',
  bool tv = false,
  VoidCallback? onSelect,
  double width = 160,
  double height = 292,
  int detailStatus = 200,
}) async {
  final api = FakeAdminApi()
    ..on('GET /api/items/1', _detail(), status: detailStatus)
    ..on('GET /api/items/1/related?limit=20', <Object>[])
    ..on('GET /api/me/preferences', {'subtitle_language': 'zh-CN'})
    ..on('GET /api/subtitles/providers?version_id=1', {
      'allowed': true,
      'providers': <Object>[],
    })
    ..on('PUT /api/items/1/watched', null, status: 204);
  final dio = Dio(BaseOptions(baseUrl: 'http://test'))..httpClientAdapter = api;
  final router = GoRouter(
    initialLocation: '/',
    routes: [
      GoRoute(
        path: '/',
        builder: (_, _) => Scaffold(
          body: Align(
            alignment: Alignment.topLeft,
            child: SizedBox(
              width: width,
              height: height,
              child: PosterCard(
                media: const MediaCard(
                  id: 1,
                  libraryId: 1,
                  kind: MediaKind.movie,
                  title: _longTitle,
                ),
                heroTag: 't',
                subtitle: _subtitle,
                onSelect: onSelect,
              ),
            ),
          ),
        ),
      ),
      GoRoute(
        path: '/item/:id',
        builder: (_, state) =>
            DetailPage(id: int.parse(state.pathParameters['id']!)),
      ),
      GoRoute(
        path: '/settings/manage/items/:id',
        builder: (_, _) => const Scaffold(body: Text('条目编辑页')),
      ),
    ],
  );
  addTearDown(router.dispose);
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        apiClientProvider.overrideWithValue(dio),
        apiBaseUrlProvider.overrideWithValue('http://test'),
        authControllerProvider.overrideWith(() => _User(role)),
      ],
      child: MaterialApp.router(
        theme: buildTheme(),
        builder: tv
            ? (context, child) => MediaQuery(
                data: televisionMediaQuery(MediaQuery.of(context)),
                child: child!,
              )
            : null,
        routerConfig: router,
      ),
    ),
  );
  await tester.pumpAndSettle();
  return (api, router);
}

/// 当前焦点是否落在指定的操作按钮上
bool _focused(Key key) {
  final context = FocusManager.instance.primaryFocus?.context;
  if (context == null) return false;
  return context.findAncestorWidgetOfExactType<IconButton>()?.key == key;
}

/// 用键盘焦点移动到「⋯」，几步内必须到达
Future<void> _focusMenu(WidgetTester tester) async {
  const key = ValueKey('poster-menu');
  for (var i = 0; i < 8 && !_focused(key); i++) {
    await tester.sendKeyEvent(LogicalKeyboardKey.tab);
    await tester.pumpAndSettle();
  }
  expect(_focused(key), isTrue);
}

double _opacity(WidgetTester tester, Key key) => tester
    .widget<AnimatedOpacity>(
      find.ancestor(
        of: find.byKey(key),
        matching: find.byType(AnimatedOpacity),
      ),
    )
    .opacity;

/// 鼠标移到海报上，返回手势方便再移开
Future<TestGesture> _hover(WidgetTester tester) async {
  final mouse = await tester.createGesture(kind: PointerDeviceKind.mouse);
  addTearDown(mouse.removePointer);
  await mouse.addPointer(location: tester.getCenter(find.byType(PosterCard)));
  await tester.pumpAndSettle();
  return mouse;
}

void main() {
  const menu = ValueKey('poster-menu');
  const play = ValueKey('poster-play');
  const edit = ValueKey('poster-edit');
  const watched = ValueKey('poster-watched');

  testWidgets('everyone gets play and ⋯ on hover, regular users no pencil', (
    tester,
  ) async {
    await _pumpCard(tester, role: 'user');
    // 操作层第一次悬停或聚焦时才构建，海报墙上几十张卡不常驻 4 个按钮
    expect(find.byKey(menu), findsNothing);

    await _hover(tester);
    expect(_opacity(tester, menu), 1);
    expect(find.byKey(play), findsOneWidget);
    expect(find.byKey(watched), findsOneWidget);
    expect(find.byKey(edit), findsNothing);
  });

  testWidgets('admin gets the edit pencil too', (tester) async {
    await _pumpCard(tester);
    await _hover(tester);
    expect(find.byKey(edit), findsOneWidget);
  });

  testWidgets('the layer hides again when the pointer leaves', (tester) async {
    await _pumpCard(tester);
    final mouse = await _hover(tester);
    expect(_opacity(tester, menu), 1);

    await mouse.moveTo(const Offset(700, 550));
    await tester.pumpAndSettle();
    expect(_opacity(tester, menu), 0);
    expect(find.byKey(menu).hitTestable(), findsNothing);
  });

  testWidgets('touch: hidden, long press opens the same menu', (tester) async {
    await _pumpCard(tester, role: 'user');
    expect(find.byKey(menu), findsNothing);

    await tester.longPress(find.text(_longTitle));
    await tester.pumpAndSettle();
    // 普通用户也有菜单：收藏、稍后观看、加入播放列表、获取信息
    expect(find.text('收藏'), findsOneWidget);
    expect(find.text('移出稍后观看'), findsOneWidget);
    expect(find.text('加入播放列表'), findsOneWidget);
    expect(find.text('获取信息'), findsOneWidget);
    expect(find.text('修正匹配'), findsNothing);
  });

  testWidgets('long press menu is filtered for admins', (tester) async {
    await _pumpCard(tester);
    await tester.longPress(find.text(_longTitle));
    await tester.pumpAndSettle();

    expect(find.text('修正匹配'), findsOneWidget);
    expect(find.text('删除'), findsOneWidget);
    expect(find.text('重新匹配'), findsNothing);
  });

  testWidgets('the card still opens the detail page on tap', (tester) async {
    final (_, router) = await _pumpCard(tester, role: 'user');
    await tester.tap(find.text(_longTitle));
    await tester.pumpAndSettle();

    expect(router.state.uri.toString(), '/item/1');
    expect(find.text('播放'), findsOneWidget);
  });

  testWidgets('hovering then tapping the poster still opens the detail page', (
    tester,
  ) async {
    final (_, router) = await _pumpCard(tester, role: 'user');
    await _hover(tester);
    expect(_opacity(tester, menu), 1);

    // 点在按钮以外的地方，遮罩不拦点击
    await tester.tapAt(
      tester.getTopLeft(find.byType(PosterCard)) + const Offset(8, 8),
    );
    await tester.pumpAndSettle();
    expect(router.state.uri.toString(), '/item/1');
  });

  testWidgets('keyboard: focus reveals the layer and the menu opens', (
    tester,
  ) async {
    await _pumpCard(tester);
    await _focusMenu(tester);
    expect(_opacity(tester, menu), 1);

    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.pumpAndSettle();
    expect(find.text('收藏'), findsOneWidget);

    await tester.sendKeyEvent(LogicalKeyboardKey.escape);
    await tester.pumpAndSettle();
    expect(find.text('收藏'), findsNothing);
    // 菜单关闭后焦点回到「⋯」，遥控器不用从头再找
    expect(_focused(menu), isTrue);
  });

  testWidgets('play falls back to the detail page without playable info', (
    tester,
  ) async {
    // 详情接口拿不到，就不知道播哪一版、哪一集，点播放改成进详情
    final (_, router) = await _pumpCard(
      tester,
      role: 'user',
      detailStatus: 404,
    );
    await _hover(tester);

    await tester.tap(find.byKey(play));
    await tester.pumpAndSettle();
    expect(router.state.uri.toString(), '/item/1');
  });

  testWidgets('watched toggle goes through the API', (tester) async {
    final (api, _) = await _pumpCard(tester, role: 'user');
    await _hover(tester);

    await tester.tap(find.byKey(watched));
    await tester.pumpAndSettle();
    expect(api.requests, contains('PUT /api/items/1/watched'));
    // 刷详情，让按钮和详情页看到同一份已看状态
    expect(api.requests.where((r) => r == 'GET /api/items/1'), isNotEmpty);
  });

  testWidgets('TV has no hover layer: no play, no ⋯', (tester) async {
    tester.view.physicalSize = const Size(1920, 1080);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await _pumpCard(tester, tv: true);
    expect(tester.takeException(), isNull);

    expect(find.byKey(play), findsNothing);
    expect(find.byKey(menu), findsNothing);
    expect(find.text(_longTitle), findsOneWidget);
  });

  testWidgets('selection mode keeps the plain card', (tester) async {
    var selected = 0;
    await _pumpCard(tester, onSelect: () => selected++);
    await _hover(tester);

    expect(find.byKey(play), findsNothing);
    expect(find.byKey(menu), findsNothing);
    await tester.tap(find.text(_longTitle));
    expect(selected, 1);
  });

  testWidgets('TV 1920x1080: long title and subtitle do not overflow', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1920, 1080);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await _pumpCard(tester, tv: true);
    expect(tester.takeException(), isNull);
    expect(find.text(_longTitle), findsOneWidget);
    expect(find.text(_subtitle), findsOneWidget);
  });
}
