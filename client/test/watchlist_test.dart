import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:iris/app/router.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/core/providers.dart';
import 'package:iris/core/servers.dart';
import 'package:iris/features/auth/auth_controller.dart';
import 'package:iris/features/detail/detail_page.dart';
import 'package:iris/features/home/home_page.dart';
import 'package:iris/features/home/home_preferences.dart';
import 'package:iris/features/library/browse_api.dart';
import 'package:iris/features/me/settings_pages.dart';
import 'package:iris/features/watchlist/watchlist_api.dart';
import 'package:iris/features/watchlist/watchlist_page.dart';

import 'support/fake_api.dart';

// ---- 接口数据 ----

Map<String, Object?> card(
  int id,
  String title, {
  String kind = 'movie',
  bool watched = false,
}) => {
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
  'watched': watched,
};

Map<String, Object?> detail(
  Map<String, Object?> card, {
  bool watchlisted = false,
  List<Map<String, Object?>> seasons = const [],
}) => {
  ...card,
  'favorite': false,
  'watchlisted': watchlisted,
  'overview': '${card['title']}的简介',
  'content_rating': null,
  'directors': <Object>[],
  'writers': <Object>[],
  'cast': <Object>[],
  'studios': <Object>[],
  'countries': <Object>[],
  'ratings': <Object>[],
  'versions': <Object>[],
  'seasons': seasons,
  'next_episode': null,
  'progress': null,
};

Map<String, Object?> home({
  List<Map<String, Object?>> libraries = const [],
  List<Map<String, Object?>> recent = const [],
  List<Map<String, Object?>> favorites = const [],
  List<Map<String, Object?>> watchlist = const [],
  List<Map<String, Object?>> shelves = const [],
}) => {
  'libraries': libraries,
  'recent': recent,
  'favorites': favorites,
  'watchlist': watchlist,
  'shelves': shelves,
};

Map<String, Object?> latestShelf(List<Map<String, Object?>> items) => {
  'library': movies,
  'hub': {'kind': 'latest', 'arg': null, 'title': '最近添加的电影', 'items': items},
};

const movies = {
  'id': 1,
  'name': '电影',
  'kind': 'movie',
  'item_count': 3,
  'cover': <Object>[],
};

Map<String, Object?> recent(String title, int itemId) => {
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

String watchlistRoute({int offset = 0}) =>
    'GET /api/me/watchlist?limit=$watchlistPageSize&offset=$offset';

// ---- 测试环境 ----

class _SignedIn extends AuthController {
  @override
  Future<AppUser?> build() async => const AppUser(
    id: 2,
    username: 'viewer',
    role: 'user',
    disabled: false,
    allowSubtitleDownload: true,
  );
}

GoRouter testRouter(String location) => GoRouter(
  initialLocation: location,
  routes: [
    GoRoute(
      path: '/',
      builder: (_, _) => const HomePage(),
      routes: [
        GoRoute(path: 'watchlist', builder: (_, _) => const WatchlistPage()),
      ],
    ),
    GoRoute(
      path: '/item/:id',
      builder: (_, state) =>
          DetailPage(id: int.parse(state.pathParameters['id']!)),
    ),
    GoRoute(
      path: '/settings/local/general',
      builder: (_, _) => const LocalGeneralPage(),
    ),
  ],
);

Future<(FakeAdminApi, GoRouter)> pumpWatchlist(
  WidgetTester tester,
  String location,
  void Function(FakeAdminApi api) setup, {
  double width = 390,
  double height = 2400,
  HomePreferences? preferences,
  bool settle = true,
}) async {
  final api = FakeAdminApi();
  setup(api);
  final dio = Dio(BaseOptions(baseUrl: 'http://test'))..httpClientAdapter = api;
  final router = testRouter(location);
  addTearDown(router.dispose);
  tester.view.physicalSize = Size(width * 3, height * 3);
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        apiClientProvider.overrideWithValue(dio),
        apiBaseUrlProvider.overrideWithValue('http://test'),
        authControllerProvider.overrideWith(_SignedIn.new),
        homePreferencesProvider.overrideWithValue(
          preferences ?? MemoryHomePreferences(),
        ),
      ],
      child: MaterialApp.router(theme: buildTheme(), routerConfig: router),
    ),
  );
  if (settle) await tester.pumpAndSettle();
  return (api, router);
}

String location(GoRouter router) => router.state.uri.toString();

void main() {
  test('the app registers /watchlist inside the home shell branch', () {
    TestWidgetsFlutterBinding.ensureInitialized();
    final container = ProviderContainer(
      overrides: [
        authControllerProvider.overrideWith(_SignedIn.new),
        manageServersProvider.overrideWithValue(false),
      ],
    );
    addTearDown(container.dispose);
    final router = container.read(routerProvider);
    final match = router.configuration.findMatch(Uri.parse('/watchlist'));
    expect(match.isError, isFalse);
    expect(match.matches.last.matchedLocation, '/watchlist');
  });

  group('models', () {
    test('home parses the watchlist row and rejects a response without it', () {
      final feed = HomeFeed.fromJson(
        home(watchlist: [card(10, '流浪地球')]),
        'http://test',
      );
      expect(feed.watchlist.single.title, '流浪地球');

      final missing = home()..remove('watchlist');
      expect(
        () => HomeFeed.fromJson(missing, 'http://test'),
        throwsA(isA<TypeError>()),
      );
    });

    test('the detail exposes watchlisted', () {
      final watchlisted = MediaDetail.fromJson(
        detail(card(10, '流浪地球'), watchlisted: true),
        'http://test',
      );
      expect(watchlisted.watchlisted, isTrue);
    });
  });

  group('watchlist page', () {
    testWidgets('the empty state explains how to add and when it clears', (
      tester,
    ) async {
      await pumpWatchlist(
        tester,
        '/watchlist',
        (api) => api.on(watchlistRoute(), {'items': <Object>[], 'total': 0}),
      );
      expect(find.text('稍后观看是空的'), findsOneWidget);
      expect(find.textContaining('在电影或剧集详情页点书签图标加入'), findsOneWidget);
      expect(find.textContaining('看完会自动移出'), findsOneWidget);
    });

    testWidgets('lists the cards the server returns and opens the detail', (
      tester,
    ) async {
      final (_, router) = await pumpWatchlist(tester, '/watchlist', (api) {
        api
          ..on(watchlistRoute(), {
            'items': [card(11, '星际穿越'), card(10, '流浪地球')],
            'total': 2,
          })
          ..on('GET /api/items/10', detail(card(10, '流浪地球')));
      });
      expect(find.text('星际穿越'), findsOneWidget);
      expect(find.text('流浪地球'), findsOneWidget);
      expect(find.text('共 2 部'), findsOneWidget);

      await tester.tap(find.text('流浪地球'));
      await tester.pumpAndSettle();
      expect(location(router), '/item/10');
    });

    testWidgets('removing a card is optimistic and can be undone', (
      tester,
    ) async {
      var items = [card(10, '流浪地球')];
      final (api, _) = await pumpWatchlist(tester, '/watchlist', (api) {
        api.routes[watchlistRoute()] = (_) =>
            (200, {'items': items, 'total': items.length});
        api.routes['DELETE /api/items/10/watchlist'] = (_) {
          items = [];
          return (204, null);
        };
        api.routes['PUT /api/items/10/watchlist'] = (_) {
          items = [card(10, '流浪地球')];
          return (204, null);
        };
      });
      await tester.tap(find.byKey(const ValueKey('watchlist-remove-10')));
      await tester.pump();
      // 请求还没回来就从列表里消失了
      expect(find.text('流浪地球'), findsNothing);
      await tester.pumpAndSettle();
      expect(api.requests, contains('DELETE /api/items/10/watchlist'));
      expect(find.text('稍后观看是空的'), findsOneWidget);
      expect(find.text('已移出稍后观看'), findsOneWidget);

      await tester.tap(find.text('撤销'));
      await tester.pumpAndSettle();
      expect(api.requests, contains('PUT /api/items/10/watchlist'));
      expect(find.text('流浪地球'), findsOneWidget);
    });

    testWidgets('a failed removal puts the card back and shows the error', (
      tester,
    ) async {
      final (_, _) = await pumpWatchlist(tester, '/watchlist', (api) {
        api
          ..on(watchlistRoute(), {
            'items': [card(10, '流浪地球')],
            'total': 1,
          })
          ..on('DELETE /api/items/10/watchlist', {
            'error': {'code': 'internal_error', 'message': '移出失败，请稍后再试'},
          }, status: 500);
      });
      await tester.tap(find.byKey(const ValueKey('watchlist-remove-10')));
      await tester.pumpAndSettle();
      expect(find.text('流浪地球'), findsOneWidget);
      expect(find.text('移出失败，请稍后再试'), findsOneWidget);
      expect(find.byKey(const ValueKey('watchlist-remove-10')), findsOneWidget);
    });

    testWidgets('loading the next page keeps the server order', (tester) async {
      final firstPage = [for (var i = 0; i < 20; i++) card(100 + i, '影片$i')];
      final secondPage = [for (var i = 20; i < 25; i++) card(100 + i, '影片$i')];
      final (api, _) = await pumpWatchlist(tester, '/watchlist', (api) {
        api
          ..on(watchlistRoute(), {'items': firstPage, 'total': 25})
          ..on(watchlistRoute(offset: 20), {'items': secondPage, 'total': 25});
      }, height: 600);
      // 首屏之后自动取下一页
      await tester.drag(find.byType(CustomScrollView), const Offset(0, -1200));
      await tester.pumpAndSettle();
      expect(api.requests, contains(watchlistRoute(offset: 20)));
    });
  });

  group('detail bookmark button', () {
    testWidgets('toggles optimistically and sends PUT/DELETE', (tester) async {
      final (api, _) = await pumpWatchlist(tester, '/item/10', (api) {
        api
          ..on('GET /api/items/10', detail(card(10, '流浪地球')))
          ..on('PUT /api/items/10/watchlist', null, status: 204)
          ..on('DELETE /api/items/10/watchlist', null, status: 204);
      });
      expect(find.byTooltip('稍后观看'), findsOneWidget);

      await tester.tap(find.byTooltip('稍后观看'));
      await tester.pump();
      // 请求还没回来就已经变成实心书签
      expect(find.byTooltip('移出稍后观看'), findsOneWidget);
      await tester.pumpAndSettle();
      expect(api.requests, contains('PUT /api/items/10/watchlist'));

      await tester.tap(find.byTooltip('移出稍后观看'));
      await tester.pumpAndSettle();
      expect(api.requests, contains('DELETE /api/items/10/watchlist'));
      expect(find.byTooltip('稍后观看'), findsOneWidget);
    });

    testWidgets('rolls back and shows the error when the request fails', (
      tester,
    ) async {
      await pumpWatchlist(tester, '/item/10', (api) {
        api
          ..on('GET /api/items/10', detail(card(10, '流浪地球')))
          ..on('PUT /api/items/10/watchlist', {
            'error': {'code': 'internal_error', 'message': '服务器暂时不可用'},
          }, status: 500);
      });
      await tester.tap(find.byTooltip('稍后观看'));
      await tester.pumpAndSettle();
      expect(find.byTooltip('稍后观看'), findsOneWidget);
      expect(find.text('服务器暂时不可用'), findsOneWidget);
    });
  });

  group('home', () {
    testWidgets('order is continue watching, watchlist, then shelves', (
      tester,
    ) async {
      final (_, router) = await pumpWatchlist(tester, '/', (api) {
        api.on(
          'GET /api/home',
          home(
            libraries: [movies],
            recent: [recent('三体', 20)],
            watchlist: [card(11, '星际穿越')],
            shelves: [
              latestShelf([card(10, '流浪地球')]),
            ],
          ),
        );
        api.on(watchlistRoute(), {'items': <Object>[], 'total': 0});
      });
      final recentTop = tester.getTopLeft(find.text('继续观看')).dy;
      final watchlistTop = tester.getTopLeft(find.text('稍后观看')).dy;
      final latestTop = tester.getTopLeft(find.text('最近添加的电影')).dy;
      expect(recentTop, lessThan(watchlistTop));
      expect(watchlistTop, lessThan(latestTop));
      expect(find.text('星际穿越'), findsOneWidget);

      // 稍后观看行的「全部」进列表页
      await tester.tap(find.text('全部').first);
      await tester.pumpAndSettle();
      expect(location(router), '/watchlist');
    });

    testWidgets('the favorites row is off by default and follows watchlist '
        'when enabled', (tester) async {
      Map<String, Object?> feed() => home(
        libraries: [movies],
        watchlist: [card(11, '星际穿越')],
        favorites: [card(12, '疯狂的外星人')],
        shelves: [
          latestShelf([card(10, '流浪地球')]),
        ],
      );

      await pumpWatchlist(tester, '/', (api) {
        api.on('GET /api/home', feed());
      });
      expect(find.text('稍后观看'), findsOneWidget);
      expect(find.text('我的收藏'), findsNothing);
      expect(find.text('疯狂的外星人'), findsNothing);

      await pumpWatchlist(tester, '/', (api) {
        api.on('GET /api/home', feed());
      }, preferences: MemoryHomePreferences(showFavorites: true));
      final watchlistTop = tester.getTopLeft(find.text('稍后观看')).dy;
      final favoritesTop = tester.getTopLeft(find.text('我的收藏')).dy;
      final latestTop = tester.getTopLeft(find.text('最近添加的电影')).dy;
      expect(watchlistTop, lessThan(favoritesTop));
      expect(favoritesTop, lessThan(latestTop));
      expect(find.text('疯狂的外星人'), findsOneWidget);
    });

    testWidgets('the phone shows the watchlist icon and it opens the page', (
      tester,
    ) async {
      final (_, router) = await pumpWatchlist(tester, '/', (api) {
        api
          ..on('GET /api/home', home())
          ..on(watchlistRoute(), {'items': <Object>[], 'total': 0});
      });
      expect(find.byTooltip('稍后观看'), findsOneWidget);
      await tester.tap(find.byTooltip('稍后观看'));
      await tester.pumpAndSettle();
      expect(location(router), '/watchlist');
    });

    testWidgets('wide screens do not show the home icon', (tester) async {
      await pumpWatchlist(
        tester,
        '/',
        (api) => api.on('GET /api/home', home()),
        width: 1200,
        height: 800,
      );
      expect(find.byTooltip('稍后观看'), findsNothing);
    });
  });

  group('settings', () {
    testWidgets('the favorites switch is saved locally', (tester) async {
      final preferences = MemoryHomePreferences();
      final api = FakeAdminApi();
      api
        ..on('GET /api/me/preferences', {'subtitle_language': null})
        ..on('GET /api/me/webdav', {
          'enabled': false,
          'username': 'alice',
          'url': null,
        });
      final dio = Dio(BaseOptions(baseUrl: 'http://test'))
        ..httpClientAdapter = api;
      tester.view.physicalSize = const Size(390 * 3, 1600 * 3);
      tester.view.devicePixelRatio = 3;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            apiClientProvider.overrideWithValue(dio),
            apiBaseUrlProvider.overrideWithValue('http://test'),
            homePreferencesProvider.overrideWithValue(preferences),
          ],
          child: MaterialApp(
            theme: buildTheme(),
            home: const LocalGeneralPage(),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text('首页显示我的收藏'), findsOneWidget);
      final tile = find.widgetWithText(ListTile, '首页显示我的收藏');
      expect(
        tester
            .widget<Switch>(
              find.descendant(of: tile, matching: find.byType(Switch)),
            )
            .value,
        isFalse,
      );
      await tester.tap(tile);
      await tester.pumpAndSettle();
      expect(preferences.showFavorites, isTrue);
      expect(
        tester
            .widget<Switch>(
              find.descendant(of: tile, matching: find.byType(Switch)),
            )
            .value,
        isTrue,
      );
    });
  });
}
