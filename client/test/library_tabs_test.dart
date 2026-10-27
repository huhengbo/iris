import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/core/providers.dart';
import 'package:iris/features/auth/auth_controller.dart';
import 'package:iris/features/library/library_page.dart';
import 'package:iris/features/library/library_preferences.dart';

import 'support/fake_api.dart';

const movies = {
  'id': 1,
  'name': '电影',
  'kind': 'movie',
  'item_count': 2,
  'cover': <Object>[],
};

Map<String, Object?> card(
  int id,
  String title, {
  int? year,
  double? rating,
  int? runtime,
  bool watched = false,
}) => {
  'id': id,
  'library_id': 1,
  'kind': 'movie',
  'title': title,
  'original_title': null,
  'year': year,
  'rating': rating,
  'genres': ['科幻'],
  'runtime': runtime ?? 125,
  'poster': <Object>[],
  'backdrop': <Object>[],
  'watched': watched,
};

Map<String, Object?> page(List<Map<String, Object?>> items) => {
  'items': items,
  'total': items.length,
};

Map<String, Object?> library({
  List<Map<String, Object?>> categories = const [],
}) => {
  ...movies,
  'genres': ['科幻', '喜剧'],
  'years': [2019, 2014],
  'categories': categories,
};

Map<String, Object?> hub(
  String kind,
  String title,
  List<Map<String, Object?>> items, {
  String? arg,
  List<Map<String, Object?>> recent = const [],
}) => {
  'kind': kind,
  'arg': arg,
  'title': title,
  'items': items,
  'recent': recent,
};

String itemsRoute({
  String sort = 'added',
  String order = 'desc',
  String extra = '',
}) =>
    'GET /api/libraries/1/items?sort=$sort&order=$order$extra&offset=0&limit=60';

GoRouter testRouter(String location) => GoRouter(
  initialLocation: location,
  routes: [
    GoRoute(
      path: '/',
      builder: (_, _) => const Scaffold(body: Text('首页')),
    ),
    GoRoute(
      path: '/library/:id',
      builder: (_, state) => LibraryPage(
        id: int.parse(state.pathParameters['id']!),
        params: state.uri.queryParameters,
      ),
    ),
    GoRoute(
      path: '/libraries/:id',
      builder: (_, state) => LibraryPage(
        id: int.parse(state.pathParameters['id']!),
        params: state.uri.queryParameters,
      ),
    ),
  ],
);

class _SignedInUser extends AuthController {
  @override
  Future<AppUser?> build() async => const AppUser(
    id: 2,
    username: 'viewer',
    role: 'user',
    disabled: false,
    allowSubtitleDownload: true,
  );
}

class _SignedInAdmin extends AuthController {
  @override
  Future<AppUser?> build() async => const AppUser(
    id: 1,
    username: 'admin',
    role: 'admin',
    disabled: false,
    allowSubtitleDownload: true,
  );
}

Future<(FakeAdminApi, GoRouter, MemoryLibraryPreferences)> pumpLibrary(
  WidgetTester tester,
  String location,
  void Function(FakeAdminApi api) setup, {
  bool admin = false,
}) async {
  final api = FakeAdminApi();
  setup(api);
  final dio = Dio(BaseOptions(baseUrl: 'http://test'))..httpClientAdapter = api;
  final router = testRouter(location);
  addTearDown(router.dispose);
  final prefs = MemoryLibraryPreferences();
  tester.view.physicalSize = const Size(390 * 3, 1400 * 3);
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        apiClientProvider.overrideWithValue(dio),
        apiBaseUrlProvider.overrideWithValue('http://test'),
        libraryPreferencesProvider.overrideWithValue(prefs),
        authControllerProvider.overrideWith(
          admin ? _SignedInAdmin.new : _SignedInUser.new,
        ),
      ],
      child: MaterialApp.router(theme: buildTheme(), routerConfig: router),
    ),
  );
  await tester.pumpAndSettle();
  return (api, router, prefs);
}

String location(GoRouter router) => router.state.uri.toString();

/// 点弹出菜单里的一项，和 library_ui_test 的 pickMenu 一样先找到菜单行。
Future<void> tapMenuItem(WidgetTester tester, String text) async {
  await tester.tap(
    find.ancestor(
      of: find.text(text).last,
      matching: find.byWidgetPredicate((widget) => widget is PopupMenuItem),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  group('tabs', () {
    testWidgets('defaults to the recommendation tab and switches by address', (
      tester,
    ) async {
      final (api, router, _) = await pumpLibrary(tester, '/library/1', (api) {
        api
          ..on('GET /api/libraries/1', library())
          ..on('GET /api/libraries/1/hubs', [
            hub('latest', '最近添加的电影', [card(10, '流浪地球', year: 2019)]),
          ])
          ..on(itemsRoute(), page([card(10, '流浪地球')]));
      });
      // 四个标签按 Plex 顺序，默认「推荐」
      expect(find.text('推荐'), findsOneWidget);
      expect(find.text('资料库'), findsOneWidget);
      expect(find.text('合集'), findsOneWidget);
      expect(find.text('分类'), findsOneWidget);
      expect(find.text('最近添加的电影'), findsOneWidget);
      expect(api.requests, contains('GET /api/libraries/1/hubs'));

      await tester.tap(find.text('资料库'));
      await tester.pumpAndSettle();
      expect(location(router), '/library/1?tab=library');
      expect(api.requests, contains(itemsRoute()));

      await tester.tap(find.text('推荐'));
      await tester.pumpAndSettle();
      expect(location(router), '/library/1');
    });

    testWidgets('the libraries branch supports the same tabs', (tester) async {
      final (api, router, _) = await pumpLibrary(
        tester,
        '/libraries/1?tab=library',
        (api) {
          api
            ..on('GET /api/libraries/1', library())
            ..on(itemsRoute(), page([card(10, '流浪地球')]));
        },
      );
      expect(location(router), '/libraries/1?tab=library');
      expect(find.text('流浪地球'), findsOneWidget);

      await tester.tap(find.text('推荐'));
      await tester.pumpAndSettle();
      expect(location(router), '/libraries/1');
      expect(api.requests, contains('GET /api/libraries/1/hubs'));
    });

    testWidgets('empty recommendation rows explain why', (tester) async {
      await pumpLibrary(tester, '/library/1', (api) {
        api
          ..on('GET /api/libraries/1', library())
          ..on('GET /api/libraries/1/hubs', <Object>[]);
      });
      expect(find.text('还没有推荐内容'), findsOneWidget);
    });

    testWidgets('a failing recommendation tab can retry', (tester) async {
      final (api, _, _) = await pumpLibrary(tester, '/library/1', (api) {
        api.on('GET /api/libraries/1', library());
      });
      expect(find.text('重试'), findsOneWidget);
      api.on('GET /api/libraries/1/hubs', [
        hub('latest', '最近添加的电影', [card(10, '流浪地球')]),
      ]);
      await tester.tap(find.text('重试'));
      await tester.pumpAndSettle();
      expect(find.text('最近添加的电影'), findsOneWidget);
    });

    testWidgets('categories open the library tab with the genre filter', (
      tester,
    ) async {
      final (api, router, _) = await pumpLibrary(
        tester,
        '/library/1?tab=categories',
        (api) {
          api
            ..on(
              'GET /api/libraries/1',
              library(
                categories: [
                  {'name': '科幻', 'item_count': 2, 'image': <Object>[]},
                ],
              ),
            )
            ..on(itemsRoute(extra: '&genre=科幻'), page([]));
        },
      );
      expect(find.text('科幻'), findsOneWidget);
      expect(find.text('2 部'), findsOneWidget);
      await tester.tap(find.text('科幻'));
      await tester.pumpAndSettle();
      final uri = Uri.parse(location(router));
      expect(uri.path, '/library/1');
      expect(uri.queryParameters, {'tab': 'library', 'genre': '科幻'});
      expect(api.requests, contains(itemsRoute(extra: '&genre=科幻')));
    });

    testWidgets('the collections tab keeps the list inside the page', (
      tester,
    ) async {
      await pumpLibrary(tester, '/library/1?tab=collections', (api) {
        api
          ..on('GET /api/libraries/1', library())
          ..on('GET /api/libraries/1/collections', [
            {'id': 3, 'title': '漫威电影宇宙', 'overview': '按上映顺序'},
          ]);
      });
      // 嵌在资料库页里，不另起一个「合集」AppBar
      expect(find.text('合集'), findsOneWidget);
      expect(find.text('漫威电影宇宙'), findsOneWidget);
      expect(find.text('按上映顺序'), findsOneWidget);
    });
  });
}
