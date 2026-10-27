import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/misc.dart' show Override;
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/core/providers.dart';
import 'package:iris/features/auth/auth_controller.dart';
import 'package:iris/features/detail/detail_page.dart';
import 'package:iris/features/detail/episode_detail_page.dart';
import 'package:iris/features/favorites/favorites_api.dart';
import 'package:iris/features/favorites/favorites_page.dart';
import 'package:iris/features/home/home_page.dart';
import 'package:iris/features/home/home_preferences.dart';
import 'package:iris/features/library/browse_api.dart';
import 'package:iris/features/person/person_page.dart';

import 'support/fake_api.dart';

// ---- 接口数据 ----

Map<String, Object?> card(
  int id,
  String title, {
  String kind = 'movie',
  int year = 2019,
}) => {
  'id': id,
  'library_id': 1,
  'kind': kind,
  'title': title,
  'original_title': null,
  'year': year,
  'rating': 8.0,
  'genres': <String>['科幻'],
  'runtime': kind == 'movie' ? 125 : null,
  'poster': <Object>[],
  'backdrop': <Object>[],
  'watched': false,
};

Map<String, Object?> detail(
  Map<String, Object?> card, {
  bool favorite = false,
  List<Map<String, Object?>> seasons = const [],
}) => {
  ...card,
  'favorite': favorite,
  'overview': '${card['title']}的简介',
  'content_rating': null,
  'watchlisted': false,
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

Map<String, Object?> episode(
  int id,
  int number, {
  String name = '科学边界',
  bool favorite = false,
}) => {
  'id': id,
  'number': number,
  'name': name,
  'overview': null,
  'air_date': null,
  'runtime': 45,
  'rating': null,
  'still': <Object>[],
  'versions': <Object>[],
  'watched': false,
  'progress': null,
  'favorite': favorite,
};

Map<String, Object?> seasonDetail(
  int id,
  int number,
  List<Map<String, Object?>> episodes,
) => {
  'id': id,
  'item_id': 20,
  'number': number,
  'name': '第 $number 季',
  'overview': null,
  'air_date': null,
  'poster': <Object>[],
  'episodes': episodes,
};

Map<String, Object?> person(int id, {String name = '吴京'}) => {
  'id': id,
  'name': name,
  'original_name': 'Wu Jing',
  'biography': null,
  'birthday': null,
  'deathday': null,
  'place_of_birth': null,
  'profile': <Object>[],
  'works': <Object>[],
  'favorite': false,
};

Map<String, Object?> home({List<Map<String, Object?>> favorites = const []}) =>
    {
      'libraries': <Object>[],
      'recent': <Object>[],
      'favorites': favorites,
      'watchlist': <Object>[],
      'shelves': <Object>[],
    };

String favoritesRoute(FavoriteKind kind, {int offset = 0}) =>
    'GET /api/me/favorites?kind=${kind.name}&offset=$offset&limit=$favoritesPageSize';

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
    GoRoute(path: '/', builder: (_, _) => const HomePage()),
    GoRoute(path: '/me/favorites', builder: (_, _) => const FavoritesPage()),
    GoRoute(
      path: '/item/:itemId/episode/:episodeId',
      builder: (_, state) => EpisodeDetailPage(
        itemId: int.parse(state.pathParameters['itemId']!),
        seasonId: int.parse(state.uri.queryParameters['season']!),
        episodeId: int.parse(state.pathParameters['episodeId']!),
      ),
    ),
    GoRoute(
      path: '/item/:id',
      builder: (_, state) =>
          DetailPage(id: int.parse(state.pathParameters['id']!)),
    ),
    GoRoute(
      path: '/person/:id',
      builder: (_, state) =>
          PersonPage(id: int.parse(state.pathParameters['id']!)),
    ),
  ],
);

Future<(FakeAdminApi, GoRouter)> pumpBrowse(
  WidgetTester tester,
  String location,
  void Function(FakeAdminApi api) setup, {
  bool settle = true,
  List<Override> overrides = const [],
}) async {
  final api = FakeAdminApi();
  setup(api);
  final dio = Dio(BaseOptions(baseUrl: 'http://test'))..httpClientAdapter = api;
  final router = testRouter(location);
  addTearDown(router.dispose);
  tester.view.physicalSize = const Size(390 * 3, 2400 * 3);
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        apiClientProvider.overrideWithValue(dio),
        apiBaseUrlProvider.overrideWithValue('http://test'),
        authControllerProvider.overrideWith(_SignedIn.new),
        ...overrides,
      ],
      child: MaterialApp.router(theme: buildTheme(), routerConfig: router),
    ),
  );
  if (settle) await tester.pumpAndSettle();
  return (api, router);
}

String location(GoRouter router) => router.state.uri.toString();

void main() {
  group('models', () {
    test('responses missing favorite fields are rejected', () {
      final noFavorite = detail(card(10, '流浪地球'))..remove('favorite');
      expect(
        () => MediaDetail.fromJson(noFavorite, 'http://test'),
        throwsA(isA<TypeError>()),
      );
      final noFavorites = home()..remove('favorites');
      expect(
        () => HomeFeed.fromJson(noFavorites, 'http://test'),
        throwsA(isA<TypeError>()),
      );
      final noPersonFavorite = person(7)..remove('favorite');
      expect(
        () => PersonDetail.fromJson(noPersonFavorite, 'http://test'),
        throwsA(isA<TypeError>()),
      );
    });

    test('the favorite filter round-trips through the library URL', () {
      final query = LibraryQuery.fromUri(1, {
        'watched': 'true',
        'favorite': 'true',
      });
      expect(query.favorite, isTrue);
      expect(query.filtered, isTrue);
      expect(query.toParams(), {'watched': 'true', 'favorite': 'true'});
      expect(query.cleared().favorite, isNull);
      // 只有 true 有筛选含义
      expect(LibraryQuery.fromUri(1, {'favorite': 'false'}).favorite, isNull);
    });
  });

  group('detail favorite button', () {
    testWidgets('favorites from the ⋯ menu and refreshes the detail', (
      tester,
    ) async {
      var favorite = false;
      final (api, _) = await pumpBrowse(tester, '/item/10', (api) {
        api.routes['GET /api/items/10'] = (_) =>
            (200, detail(card(10, '流浪地球'), favorite: favorite));
        api.routes['PUT /api/items/10/favorite'] = (_) {
          favorite = true;
          return (204, null);
        };
        api.routes['DELETE /api/items/10/favorite'] = (_) {
          favorite = false;
          return (204, null);
        };
      });
      // 收藏不再占用操作行的按钮，进「⋯」
      expect(find.byIcon(Icons.favorite_border), findsNothing);

      await tester.tap(find.byTooltip('更多操作'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('收藏'));
      await tester.pumpAndSettle();
      expect(
        api.requests.where((r) => r == 'PUT /api/items/10/favorite'),
        hasLength(1),
      );
      expect(find.text('已收藏'), findsOneWidget);

      // 刷过详情后，菜单里换成取消收藏
      await tester.tap(find.byTooltip('更多操作'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('取消收藏'));
      await tester.pumpAndSettle();
      expect(api.requests, contains('DELETE /api/items/10/favorite'));

      await tester.tap(find.byTooltip('更多操作'));
      await tester.pumpAndSettle();
      expect(find.text('收藏'), findsOneWidget);
    });

    testWidgets('shows the error and keeps the state when the request fails', (
      tester,
    ) async {
      final (api, _) = await pumpBrowse(tester, '/item/10', (api) {
        api
          ..on('GET /api/items/10', detail(card(10, '流浪地球')))
          ..on('PUT /api/items/10/favorite', {
            'error': {'code': 'internal', 'message': '服务器暂时不可用'},
          }, status: 500);
      });
      await tester.tap(find.byTooltip('更多操作'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('收藏'));
      await tester.pumpAndSettle();
      expect(find.text('服务器暂时不可用'), findsOneWidget);
      expect(
        api.requests.where((r) => r == 'PUT /api/items/10/favorite'),
        hasLength(1),
      );

      await tester.tap(find.byTooltip('更多操作'));
      await tester.pumpAndSettle();
      expect(find.text('收藏'), findsOneWidget);
    });

    testWidgets('an episode has its own favorite button', (tester) async {
      var favorite = false;
      final (api, _) = await pumpBrowse(
        tester,
        '/item/20/episode/301?season=21',
        (api) {
          api.routes['GET /api/items/20'] = (_) =>
              (200, detail(card(20, '三体', kind: 'series')));
          api.routes['GET /api/seasons/21'] = (_) =>
              (200, seasonDetail(21, 1, [episode(301, 1, favorite: favorite)]));
          api.routes['PUT /api/episodes/301/favorite'] = (_) {
            favorite = true;
            return (204, null);
          };
        },
      );
      expect(find.byTooltip('收藏'), findsOneWidget);
      await tester.tap(find.byTooltip('收藏'));
      await tester.pumpAndSettle();
      expect(api.requests, contains('PUT /api/episodes/301/favorite'));
      expect(find.byTooltip('取消收藏'), findsOneWidget);
    });

    testWidgets('a person has a favorite button on the header', (tester) async {
      var favorite = false;
      final (api, _) = await pumpBrowse(tester, '/person/7', (api) {
        api.routes['GET /api/people/7'] = (_) =>
            (200, {...person(7), 'favorite': favorite});
        api.routes['PUT /api/people/7/favorite'] = (_) {
          favorite = true;
          return (204, null);
        };
      });
      expect(find.byTooltip('收藏'), findsOneWidget);
      await tester.tap(find.byTooltip('收藏'));
      await tester.pumpAndSettle();
      expect(api.requests, contains('PUT /api/people/7/favorite'));
      expect(find.byTooltip('取消收藏'), findsOneWidget);
    });
  });

  group('favorites page', () {
    void emptyTabs(FakeAdminApi api) {
      for (final kind in FavoriteKind.values) {
        api.on(favoritesRoute(kind), {'items': <Object>[], 'total': 0});
      }
    }

    testWidgets('lists each kind and opens the right detail', (tester) async {
      final (_, router) = await pumpBrowse(tester, '/me/favorites', (api) {
        api
          ..on(favoritesRoute(FavoriteKind.movie), {
            'items': [card(10, '流浪地球')],
            'total': 1,
          })
          ..on(favoritesRoute(FavoriteKind.series), {
            'items': <Object>[],
            'total': 0,
          })
          ..on(favoritesRoute(FavoriteKind.episode), {
            'items': [
              {
                'item': card(20, '三体', kind: 'series'),
                'episode': {
                  'id': 301,
                  'season_id': 21,
                  'season_number': 1,
                  'number': 1,
                  'name': '科学边界',
                },
                'still': <Object>[],
              },
            ],
            'total': 1,
          })
          ..on('GET /api/items/20', detail(card(20, '三体', kind: 'series')))
          ..on('GET /api/seasons/21', seasonDetail(21, 1, [episode(301, 1)]))
          ..on(favoritesRoute(FavoriteKind.person), {
            'items': [
              {
                'id': 7,
                'name': '吴京',
                'original_name': 'Wu Jing',
                'profile': <Object>[],
              },
            ],
            'total': 1,
          });
      });
      expect(find.text('流浪地球'), findsOneWidget);

      await tester.tap(find.text('剧集'));
      await tester.pumpAndSettle();
      expect(find.text('还没有收藏'), findsOneWidget);

      await tester.tap(find.text('单集'));
      await tester.pumpAndSettle();
      expect(find.text('三体'), findsOneWidget);
      expect(find.text('第 1 季 第 1 集 · 科学边界'), findsOneWidget);
      await tester.tap(find.text('三体'));
      await tester.pumpAndSettle();
      expect(location(router), '/item/20/episode/301?season=21');

      await tester.tap(find.byTooltip('返回'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('人物'));
      await tester.pumpAndSettle();
      expect(find.text('吴京'), findsOneWidget);
      expect(find.text('Wu Jing'), findsOneWidget);
      await tester.tap(find.text('吴京'));
      await tester.pumpAndSettle();
      expect(location(router), '/person/7');
    });

    testWidgets('an empty tab explains how to favorite', (tester) async {
      await pumpBrowse(tester, '/me/favorites', emptyTabs);
      expect(find.text('还没有收藏'), findsOneWidget);
      expect(find.textContaining('在电影详情页点「收藏」'), findsOneWidget);
    });

    testWidgets('an error can be retried', (tester) async {
      final (api, _) = await pumpBrowse(tester, '/me/favorites', (_) {});
      expect(find.text('重试'), findsOneWidget);

      emptyTabs(api);
      await tester.tap(find.text('重试'));
      await tester.pumpAndSettle();
      expect(find.text('还没有收藏'), findsOneWidget);
    });
  });

  group('home favorites', () {
    testWidgets('the favorites rail is hidden by default', (tester) async {
      await pumpBrowse(tester, '/', (api) {
        api.on('GET /api/home', home(favorites: [card(10, '流浪地球')]));
      });
      expect(find.text('我的收藏'), findsNothing);
      expect(find.text('流浪地球'), findsNothing);
    });

    testWidgets('an enabled favorites rail opens the favorites page', (
      tester,
    ) async {
      final (_, router) = await pumpBrowse(
        tester,
        '/',
        (api) {
          api
            ..on('GET /api/home', home(favorites: [card(10, '流浪地球')]))
            ..on(favoritesRoute(FavoriteKind.movie), {
              'items': <Object>[],
              'total': 0,
            });
        },
        overrides: [
          homePreferencesProvider.overrideWithValue(
            MemoryHomePreferences(showFavorites: true),
          ),
        ],
      );
      expect(find.text('我的收藏'), findsOneWidget);
      expect(find.text('流浪地球'), findsOneWidget);
      await tester.tap(find.text('全部'));
      await tester.pumpAndSettle();
      expect(location(router), '/me/favorites');
    });

    testWidgets('no favorites means no rail', (tester) async {
      await pumpBrowse(
        tester,
        '/',
        (api) => api.on('GET /api/home', home()),
        overrides: [
          homePreferencesProvider.overrideWithValue(
            MemoryHomePreferences(showFavorites: true),
          ),
        ],
      );
      expect(find.text('我的收藏'), findsNothing);
    });
  });
}
