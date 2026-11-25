import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/core/providers.dart';
import 'package:iris/core/widgets/skeleton.dart';
import 'package:iris/features/auth/auth_controller.dart';
import 'package:iris/features/library/art_image.dart';
import 'package:iris/features/library/continue_watching_card.dart';
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
            {'id': 3, 'title': '漫威电影宇宙', 'item_count': 3, 'cover': <Object>[]},
          ]);
      });
      // 嵌在资料库页里，不另起一个「合集」AppBar
      expect(find.text('合集'), findsOneWidget);
      expect(find.text('漫威电影宇宙'), findsOneWidget);
      expect(find.text('3 部'), findsOneWidget);
    });

    testWidgets('list view uses a check icon instead of watched text', (
      tester,
    ) async {
      await pumpLibrary(tester, '/library/1?tab=library', (api) {
        api
          ..on('GET /api/libraries/1', library())
          ..on(
            itemsRoute(),
            page([
              card(10, '流浪地球', year: 2019, watched: true),
              card(11, '星际穿越', year: 2014, watched: false),
            ]),
          );
      });
      await tester.tap(find.byTooltip('列表视图'));
      await tester.pumpAndSettle();

      expect(find.text('流浪地球'), findsOneWidget);
      expect(find.text('星际穿越'), findsOneWidget);
      expect(find.byTooltip('标记未看'), findsOneWidget);
      expect(find.byTooltip('标记已看'), findsOneWidget);
      expect(find.byIcon(Icons.check_circle), findsOneWidget);
      expect(find.byIcon(Icons.check_circle_outline), findsOneWidget);
      expect(find.text('已看'), findsNothing);
      expect(find.text('未看'), findsNothing);
    });

    testWidgets(
      'continue hub in recommend tab renders 16:9 ContinueWatchingCard',
      (tester) async {
        final earth = card(10, '流浪地球', year: 2019);
        await pumpLibrary(tester, '/library/1', (api) {
          api
            ..on('GET /api/libraries/1', library())
            ..on('GET /api/libraries/1/hubs', [
              hub(
                'continue',
                '继续观看',
                [earth],
                recent: [
                  {
                    'item': earth,
                    'episode': null,
                    'still': <Object>[],
                    'progress': {'position': 1800, 'duration': 7500},
                  },
                ],
              ),
            ]);
        });

        expect(find.byType(ContinueWatchingCard), findsOneWidget);
        expect(find.byType(ProgressLine), findsOneWidget);
        expect(find.text('2019 · 电影 · 剩 95 分钟'), findsOneWidget);
      },
    );

    testWidgets(
      'list view shows skeleton while loading, series unwatched count, and actions',
      (tester) async {
        final completer = Completer<(int, Object?)>();
        final seriesCard = {
          'id': 20,
          'library_id': 1,
          'kind': 'series',
          'title': '三体',
          'original_title': null,
          'year': 2023,
          'rating': 8.6,
          'genres': ['科幻', '悬疑'],
          'runtime': null,
          'poster': <Object>[],
          'backdrop': <Object>[],
          'watched': false,
          'unwatched_count': 12,
        };
        final api = FakeAdminApi()
          ..on('GET /api/libraries/1', library())
          ..on(itemsRoute(), page([seriesCard]))
          ..on('GET /api/items/20', {
            ...seriesCard,
            'favorite': false,
            'watchlisted': false,
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
            'versions': <Object>[],
            'seasons': <Object>[],
            'next_episode': null,
            'progress': null,
          })
          ..on('PUT /api/items/20/watched', null, status: 204);

        final dio = Dio(BaseOptions(baseUrl: 'http://test'))
          ..httpClientAdapter = api
          ..interceptors.add(
            InterceptorsWrapper(
              onRequest: (options, handler) async {
                if (options.path.endsWith('/items')) {
                  await completer.future;
                }
                handler.next(options);
              },
            ),
          );
        final router = testRouter('/library/1?tab=library');
        addTearDown(router.dispose);
        final prefs = MemoryLibraryPreferences(viewMode: LibraryViewMode.list);
        tester.view.physicalSize = const Size(390 * 3, 1400 * 3);
        tester.view.devicePixelRatio = 3;
        addTearDown(tester.view.reset);

        await tester.pumpWidget(
          ProviderScope(
            overrides: [
              apiClientProvider.overrideWithValue(dio),
              apiBaseUrlProvider.overrideWithValue('http://test'),
              libraryPreferencesProvider.overrideWithValue(prefs),
              authControllerProvider.overrideWith(_SignedInUser.new),
            ],
            child: MaterialApp.router(
              theme: buildTheme(),
              routerConfig: router,
            ),
          ),
        );
        await tester.pump();
        await tester.pump(const Duration(milliseconds: 50));

        expect(find.byType(Skeleton), findsWidgets);

        completer.complete((200, null));
        await tester.pumpAndSettle();

        expect(find.text('2023 · 科幻 / 悬疑 · 未看 12 集 · 8.6 分'), findsOneWidget);
        await tester.tap(find.byTooltip('标记已看'));
        await tester.pumpAndSettle();
        expect(api.requests, contains('PUT /api/items/20/watched'));

        await tester.tap(find.byTooltip('更多操作'));
        await tester.pumpAndSettle();
        expect(find.text('加入稍后观看'), findsOneWidget);
        await tester.tap(find.byTooltip('关闭'));
        await tester.pumpAndSettle();

        // 桌面右键也可直接打开菜单
        await tester.tap(find.text('三体'), buttons: kSecondaryMouseButton);
        await tester.pumpAndSettle();
        expect(find.text('加入稍后观看'), findsOneWidget);
      },
    );

    testWidgets('az bar supports vertical drag with center preview bubble', (
      tester,
    ) async {
      final (api, router, _) = await pumpLibrary(
        tester,
        '/library/1?tab=library&sort=name&order=asc',
        (api) {
          api
            ..on('GET /api/libraries/1', library())
            ..on(
              itemsRoute(sort: 'name', order: 'asc'),
              page([card(10, 'Avatar', year: 2009)]),
            )
            ..on(
              itemsRoute(sort: 'name', order: 'asc', extra: '&letter=A'),
              page([card(10, 'Avatar', year: 2009)]),
            )
            ..on(
              itemsRoute(sort: 'name', order: 'asc', extra: '&letter=N'),
              page([]),
            );
        },
      );

      final azRect = tester.getRect(find.bySemanticsLabel('首字母跳转'));
      const bubbleKey = ValueKey('az-preview-bubble');
      final gesture = await tester.startGesture(
        Offset(azRect.center.dx, azRect.top + 8),
      );
      // 滑动超过 kTouchSlop 触发垂直拖拽，仍在「A」范围内
      await gesture.moveBy(const Offset(0, 24));
      await tester.pump();
      expect(find.byKey(bubbleKey), findsOneWidget);
      expect(
        find.descendant(of: find.byKey(bubbleKey), matching: find.text('A')),
        findsOneWidget,
      );

      await gesture.moveTo(azRect.center);
      await tester.pump();
      expect(
        find.descendant(of: find.byKey(bubbleKey), matching: find.text('N')),
        findsOneWidget,
      );

      await gesture.up();
      await tester.pumpAndSettle();
      expect(find.byKey(bubbleKey), findsNothing);
      expect(Uri.parse(location(router)).queryParameters['letter'], isNotNull);
    });

    testWidgets('pressing Escape exits selection mode', (tester) async {
      await pumpLibrary(tester, '/library/1?tab=library', (api) {
        api
          ..on('GET /api/libraries/1', library())
          ..on(itemsRoute(), page([card(10, '流浪地球', year: 2019)]));
      }, admin: true);

      await tester.tap(find.byTooltip('选择条目'));
      await tester.pumpAndSettle();
      expect(find.text('已选 0 项'), findsOneWidget);

      await tester.sendKeyEvent(LogicalKeyboardKey.escape);
      await tester.pumpAndSettle();
      expect(find.text('已选 0 项'), findsNothing);
    });

    testWidgets('admin can toggle selection mode from the library toolbar', (
      tester,
    ) async {
      await pumpLibrary(tester, '/library/1?tab=library', (api) {
        api
          ..on('GET /api/libraries/1', library())
          ..on(itemsRoute(), page([card(10, '流浪地球', year: 2019)]));
      }, admin: true);

      expect(find.byTooltip('选择条目'), findsOneWidget);
      await tester.tap(find.byTooltip('选择条目'));
      await tester.pumpAndSettle();

      expect(find.text('已选 0 项'), findsOneWidget);
      expect(find.byTooltip('批量操作'), findsOneWidget);

      await tester.tap(find.text('流浪地球'));
      await tester.pump();
      expect(find.text('已选 1 项'), findsOneWidget);

      await tester.tap(find.byTooltip('退出选择').first);
      await tester.pumpAndSettle();
      expect(find.text('已选 1 项'), findsNothing);
    });

    testWidgets('regular users do not see selection mode toggle in toolbar', (
      tester,
    ) async {
      await pumpLibrary(tester, '/library/1?tab=library', (api) {
        api
          ..on('GET /api/libraries/1', library())
          ..on(itemsRoute(), page([card(10, '流浪地球', year: 2019)]));
      });

      expect(find.byTooltip('选择条目'), findsNothing);
    });
  });
}
