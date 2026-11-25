import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/app/tv.dart';
import 'package:iris/core/providers.dart';
import 'package:iris/core/widgets/skeleton.dart';
import 'package:iris/features/library/browse_api.dart';
import 'package:iris/features/tv/tv_home_page.dart';
import 'package:iris/features/tv/tv_widgets.dart';
import 'package:iris/main.dart';

import 'support/fake_api.dart';

/// 电视首页：行顺序与手机一致，精选轮播不再出现，默认焦点在第一行第一张卡。

Map<String, Object?> library() => {
  'id': 1,
  'name': '电影',
  'kind': 'movie',
  'item_count': 4,
  'cover': <Object>[],
};

Map<String, Object?> card(int id, String title, {String kind = 'movie'}) => {
  'id': id,
  'library_id': 1,
  'kind': kind,
  'title': title,
  'original_title': null,
  'year': 2019,
  'rating': 8.0,
  'genres': <String>['科幻'],
  'runtime': kind == 'movie' ? 125 : null,
  'poster': <Object>[],
  'backdrop': <Object>[],
  'watched': false,
};

Map<String, Object?> play(int itemId, String title) => {
  'item': card(itemId, title, kind: 'series'),
  'episode': {
    'id': itemId * 10,
    'season_id': 21,
    'season_number': 1,
    'number': 2,
    'name': '疑云',
  },
  'still': <Object>[],
  'progress': {'position': 600, 'duration': 1200},
};

Map<String, Object?> homeFeed({
  List<Map<String, Object?>> recent = const [],
  List<Map<String, Object?>> watchlist = const [],
  List<Map<String, Object?>> shelves = const [],
}) => {
  'libraries': [library()],
  'recent': recent,
  'favorites': <Object>[],
  'watchlist': watchlist,
  'shelves': shelves,
};

Map<String, Object?> hubShelf(String title, List<Map<String, Object?>> items) =>
    {
      'library': library(),
      'hub': {
        'kind': 'latest',
        'arg': null,
        'title': title,
        'items': items,
        'recent': <Object>[],
      },
    };

Future<void> pumpHome(WidgetTester tester, Map<String, Object?> json) async {
  // 竖向放大到能同时容纳三排，省去测试里的滚动
  tester.view.physicalSize = const Size(1920, 1400);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        homeFeedProvider.overrideWith(
          (ref) async => HomeFeed.fromJson(json, 'http://test'),
        ),
        // 焦点停稳 400ms 后会取简介；不拦网络的话 Riverpod 会对失败反复重试，
        // pumpAndSettle 永远等不到静止。
        mediaDetailProvider.overrideWith(
          (ref, id) async => MediaDetail.fromJson({
            ...card(id, '详情'),
            'overview': '简介',
            'content_rating': null,
            'logo': <Object>[],
            'studios': <Object>[],
            'countries': <Object>[],
            'ratings': <Object>[],
            'favorite': false,
            'directors': <Object>[],
            'writers': <Object>[],
            'cast': <Object>[],
            'versions': <Object>[],
            'seasons': <Object>[],
            'next_episode': null,
            'progress': null,
            'watchlisted': false,
          }, 'http://test'),
        ),
      ],
      child: MaterialApp(
        theme: buildTheme(),
        shortcuts: appShortcuts,
        builder: televisionBuilder,
        home: const TvHomePage(),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

bool focusedOn(WidgetTester tester, String label) {
  final focused = FocusManager.instance.primaryFocus?.context;
  if (focused == null) return false;
  return find
      .descendant(
        of: find.byElementPredicate((element) => element == focused),
        matching: find.text(label),
      )
      .evaluate()
      .isNotEmpty;
}

void main() {
  testWidgets('rows are continue watching, watchlist and library hubs', (
    tester,
  ) async {
    await pumpHome(
      tester,
      homeFeed(
        recent: [play(1, '三体')],
        watchlist: [card(2, '沙丘')],
        shelves: [
          hubShelf('最近添加的电影', [card(3, '银翼杀手'), card(4, '降临')]),
        ],
      ),
    );

    double top(String label) => tester.getTopLeft(find.text(label)).dy;
    expect(top('继续观看'), lessThan(top('稍后观看')));
    expect(top('稍后观看'), lessThan(top('最近添加的电影')));
  });

  testWidgets('library continue hubs use still cards and keep the server '
      'order', (tester) async {
    await pumpHome(
      tester,
      homeFeed(
        shelves: [
          {
            'library': library(),
            'hub': {
              'kind': 'continue',
              'arg': null,
              'title': '继续观看 · 电影',
              'items': [card(1, '三体', kind: 'series')],
              'recent': [play(1, '三体')],
            },
          },
          hubShelf('最近添加的电影', [card(3, '银翼杀手')]),
        ],
      ),
    );

    expect(find.text('继续观看 · 电影'), findsOneWidget);
    double top(String label) => tester.getTopLeft(find.text(label)).dy;
    expect(top('继续观看 · 电影'), lessThan(top('最近添加的电影')));
    // 继续观看行的片名下面写集数和剩余时间
    expect(find.textContaining('第 1 季 第 2 集'), findsWidgets);
  });

  testWidgets('default focus is the first card of the first row', (
    tester,
  ) async {
    await pumpHome(
      tester,
      homeFeed(
        recent: [play(1, '三体')],
        watchlist: [card(2, '沙丘')],
        shelves: [
          hubShelf('最近添加的电影', [card(3, '银翼杀手')]),
        ],
      ),
    );
    expect(focusedOn(tester, '三体'), isTrue);
  });

  testWidgets('an empty watchlist does not add a row', (tester) async {
    await pumpHome(
      tester,
      homeFeed(
        recent: [play(1, '三体')],
        shelves: [
          hubShelf('最近添加的电影', [card(3, '银翼杀手')]),
        ],
      ),
    );
    expect(find.text('稍后观看'), findsNothing);
    expect(find.text('继续观看'), findsOneWidget);
    expect(find.text('最近添加的电影'), findsOneWidget);
  });

  testWidgets('spotlight keeps title steady when overview loads and gives '
      'cards unique hero tags', (tester) async {
    await pumpHome(
      tester,
      homeFeed(
        recent: [play(1, '三体')],
        watchlist: [card(1, '三体', kind: 'series')],
      ),
    );

    final spotTitle = find.byWidgetPredicate(
      (w) =>
          w is Text && w.data == '三体' && w.style?.fontWeight == FontWeight.w700,
    );
    final dyBefore = tester.getTopLeft(spotTitle).dy;

    // 焦点停稳 400ms 后加载简介，片名位置保持不变
    await tester.pump(const Duration(milliseconds: 500));
    await tester.pumpAndSettle();

    expect(find.text('简介'), findsOneWidget);
    expect(tester.getTopLeft(spotTitle).dy, dyBefore);

    // 同一条目出现在多个片架时 Hero tag 互不重复，且首张卡片左侧留白足以容纳焦点环
    final heroTags = [
      for (final hero in tester.widgetList<Hero>(find.byType(Hero))) hero.tag,
    ];
    expect(heroTags.length, greaterThanOrEqualTo(2));
    expect(heroTags.toSet().length, heroTags.length);

    final shelfLeft = tester.getTopLeft(find.byType(TvShelf).first).dx;
    final cardLeft = tester.getTopLeft(find.byType(TvStill).first).dx;
    expect(cardLeft - shelfLeft, greaterThanOrEqualTo(14));
  });

  testWidgets('shows structured skeleton respecting overscan while loading', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1920, 1080);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final pending = Completer<HomeFeed>();

    await tester.pumpWidget(
      ProviderScope(
        overrides: [homeFeedProvider.overrideWith((ref) => pending.future)],
        child: MaterialApp(
          theme: buildTheme(),
          shortcuts: appShortcuts,
          builder: televisionBuilder,
          home: const TvHomePage(),
        ),
      ),
    );
    await tester.pump();

    expect(find.byType(CircularProgressIndicator), findsNothing);
    expect(find.bySemanticsLabel('正在加载'), findsOneWidget);
    expect(find.byType(Skeleton), findsWidgets);
    // 过扫描安全区（48dp）被尊重
    final firstSkeleton = tester.getTopLeft(find.byType(Skeleton).first);
    expect(firstSkeleton.dx, greaterThanOrEqualTo(TvLayout.overscan.left));
  });

  testWidgets('quick action sheet works via menu/info keys and long press', (
    tester,
  ) async {
    final api = FakeAdminApi()
      ..on('PUT /api/items/1/watched', null, status: 204)
      ..on('PUT /api/items/2/watchlist', null, status: 204)
      ..on('DELETE /api/items/1/resume', null, status: 204);
    final dio = Dio(BaseOptions(baseUrl: 'http://test'))
      ..httpClientAdapter = api;

    tester.view.physicalSize = const Size(1920, 1400);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);

    final router = GoRouter(
      initialLocation: '/',
      routes: [
        GoRoute(
          path: '/',
          builder: (_, _) => const Scaffold(body: TvHomePage()),
        ),
        GoRoute(
          path: '/item/:id',
          builder: (_, state) =>
              Scaffold(body: Text('详情页 ${state.pathParameters['id']}')),
        ),
      ],
    );

    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          apiClientProvider.overrideWithValue(dio),
          apiBaseUrlProvider.overrideWithValue('http://test'),
          homeFeedProvider.overrideWith(
            (ref) async => HomeFeed.fromJson(
              homeFeed(recent: [play(1, '三体')], watchlist: [card(2, '沙丘')]),
              'http://test',
            ),
          ),
          mediaDetailProvider.overrideWith(
            (ref, id) async => MediaDetail.fromJson({
              ...card(id, id == 1 ? '三体' : '沙丘'),
              'overview': '简介',
              'content_rating': null,
              'logo': <Object>[],
              'studios': <Object>[],
              'countries': <Object>[],
              'ratings': <Object>[],
              'favorite': false,
              'directors': <Object>[],
              'writers': <Object>[],
              'cast': <Object>[],
              'versions': <Object>[],
              'seasons': <Object>[],
              'next_episode': null,
              'progress': null,
              'watchlisted': false,
            }, 'http://test'),
          ),
        ],
        child: MaterialApp.router(
          theme: buildTheme(),
          shortcuts: appShortcuts,
          builder: televisionBuilder,
          routerConfig: router,
        ),
      ),
    );
    await tester.pumpAndSettle();

    // 默认焦点在 TvStill（三体），按菜单键弹出快捷操作弹层并包含「从继续观看移除」
    await tester.sendKeyEvent(LogicalKeyboardKey.contextMenu);
    await tester.pumpAndSettle();
    expect(find.text('标记已看'), findsOneWidget);
    expect(find.text('加入稍后观看'), findsOneWidget);
    expect(find.text('从继续观看移除'), findsOneWidget);
    expect(find.text('查看详情'), findsOneWidget);

    await tester.tap(find.text('从继续观看移除'));
    await tester.pumpAndSettle();
    expect(api.requests, contains('DELETE /api/items/1/resume'));
    expect(find.text('已从继续观看移除'), findsOneWidget);

    // 长按 TvStill 打开弹层并执行标记已看
    await tester.longPress(find.byType(TvStill).first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('标记已看'));
    await tester.pumpAndSettle();
    expect(api.requests, contains('PUT /api/items/1/watched'));
    expect(find.text('已标记为已看'), findsOneWidget);

    // 按下键移到 TvPoster（沙丘），按信息键打开弹层，不含「从继续观看移除」
    await tester.sendKeyEvent(LogicalKeyboardKey.arrowDown);
    await tester.pumpAndSettle();
    expect(focusedOn(tester, '沙丘'), isTrue);

    await tester.sendKeyEvent(LogicalKeyboardKey.info);
    await tester.pumpAndSettle();
    expect(find.text('标记已看'), findsOneWidget);
    expect(find.text('加入稍后观看'), findsOneWidget);
    expect(find.text('从继续观看移除'), findsNothing);
    expect(find.text('查看详情'), findsOneWidget);

    await tester.tap(find.text('加入稍后观看'));
    await tester.pumpAndSettle();
    expect(api.requests, contains('PUT /api/items/2/watchlist'));
    expect(find.text('已加入稍后观看'), findsOneWidget);

    await tester.sendKeyEvent(LogicalKeyboardKey.info);
    await tester.pumpAndSettle();
    await tester.tap(find.text('查看详情'));
    await tester.pumpAndSettle();
    expect(find.text('详情页 2'), findsOneWidget);
  });
}
