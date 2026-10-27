import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/core/providers.dart';
import 'package:iris/features/library/browse_api.dart';
import 'package:iris/features/search/search_page.dart';

import 'support/fake_api.dart';

Map<String, Object?> card(int id, String title) => {
  'id': id,
  'library_id': 1,
  'kind': 'movie',
  'title': title,
  'genres': <String>[],
  'poster': <Object>[],
  'backdrop': <Object>[],
  'watched': false,
};

Map<String, Object?> page({
  List<Map<String, Object?>> items = const [],
  List<Map<String, Object?>> people = const [],
  List<Map<String, Object?>> episodes = const [],
}) => {
  'items': items,
  'total': items.length,
  'people': people,
  'episodes': episodes,
};

String route(String q) => 'GET /api/search?q=$q&offset=0&limit=$browsePageSize';

void main() {
  late FakeAdminApi api;
  late GoRouter router;

  Future<void> pumpSearch(WidgetTester tester, {String? initial}) async {
    api = FakeAdminApi();
    final dio = Dio(BaseOptions(baseUrl: 'http://test'))
      ..httpClientAdapter = api;
    router = GoRouter(
      initialLocation: initial == null ? '/search' : '/search?q=$initial',
      routes: [
        GoRoute(
          path: '/search',
          builder: (_, state) =>
              SearchPage(query: state.uri.queryParameters['q'] ?? ''),
        ),
        GoRoute(
          path: '/person/:id',
          builder: (_, _) => const Scaffold(body: Text('人物页')),
        ),
      ],
    );
    addTearDown(router.dispose);
    tester.view.physicalSize = const Size(390 * 3, 1600 * 3);
    tester.view.devicePixelRatio = 3;
    addTearDown(tester.view.reset);
    api.on(route('流浪'), page(items: [card(1, '流浪地球')]));
    api.on(
      route('吴京'),
      page(
        items: [card(1, '流浪地球'), card(2, '疯狂的外星人')],
        people: [
          {'id': 9, 'name': '吴京', 'original_name': null, 'profile': <Object>[]},
        ],
        episodes: [
          {
            'id': 5,
            'item_id': 3,
            'season_id': 4,
            'season_number': 1,
            'number': 1,
            'name': '第一集',
            'show_title': '三体',
            'still': <Object>[],
          },
        ],
      ),
    );
    api.on(route('不存在'), page());
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          apiClientProvider.overrideWithValue(dio),
          apiBaseUrlProvider.overrideWithValue('http://test'),
        ],
        child: MaterialApp.router(theme: buildTheme(), routerConfig: router),
      ),
    );
    await tester.pumpAndSettle();
  }

  List<String> searches() =>
      api.requests.where((r) => r.contains('/api/search')).toList();

  testWidgets('debounces typing and searches once after 300ms', (tester) async {
    await pumpSearch(tester);
    expect(searches(), isEmpty);

    await tester.enterText(find.byType(TextField), '流');
    await tester.pump(const Duration(milliseconds: 200));
    await tester.enterText(find.byType(TextField), '流浪');
    await tester.pump(const Duration(milliseconds: 200));
    // 第二次输入重新计时，此时还没搜
    expect(searches(), isEmpty);

    await tester.pump(const Duration(milliseconds: 150));
    await tester.pumpAndSettle();
    expect(searches(), [route('流浪')]);
    expect(find.text('流浪地球'), findsOneWidget);
    expect(router.state.uri.queryParameters['q'], '流浪');
  });

  testWidgets('clear button resets the query and shows history', (
    tester,
  ) async {
    await pumpSearch(tester, initial: '流浪');
    expect(find.text('流浪地球'), findsOneWidget);

    await tester.tap(find.byTooltip('清除'));
    await tester.pumpAndSettle();
    expect(router.state.uri.queryParameters, isEmpty);
    expect(
      tester.widget<TextField>(find.byType(TextField)).controller!.text,
      '',
    );
    expect(find.text('流浪地球'), findsNothing);
    expect(find.text('搜索影片和剧集'), findsOneWidget);
  });

  testWidgets('shows people, episodes and works as separate sections', (
    tester,
  ) async {
    await pumpSearch(tester, initial: '吴京');

    expect(find.text('人物'), findsOneWidget);
    expect(find.text('单集'), findsOneWidget);
    expect(find.text('影片和剧集'), findsOneWidget);
    expect(find.text('吴京'), findsWidgets);
    expect(find.text('三体 · 第一集'), findsOneWidget);
    expect(find.text('流浪地球'), findsOneWidget);
    expect(find.text('疯狂的外星人'), findsOneWidget);
    expect(find.textContaining('没有找到'), findsNothing);

    await tester.tap(find.widgetWithText(ActionChip, '吴京'));
    await tester.pumpAndSettle();
    expect(find.text('人物页'), findsOneWidget);
  });

  testWidgets('empty result explains what to try', (tester) async {
    await pumpSearch(tester, initial: '不存在');

    expect(find.text('没有找到「不存在」'), findsOneWidget);
    expect(find.textContaining('检查拼写'), findsOneWidget);
    expect(find.textContaining('缩短关键词'), findsOneWidget);
    expect(find.textContaining('资料库'), findsOneWidget);

    await tester.tap(find.text('清除搜索'));
    await tester.pumpAndSettle();
    expect(router.state.uri.queryParameters, isEmpty);
    expect(find.textContaining('没有找到'), findsNothing);
  });
}
