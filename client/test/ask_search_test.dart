import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/app/tv.dart';
import 'package:iris/core/providers.dart';
import 'package:iris/core/server.dart';
import 'package:iris/features/ai/ai_availability.dart';
import 'package:iris/features/library/browse_api.dart';
import 'package:iris/features/search/ask_search_api.dart';
import 'package:iris/features/search/search_page.dart';

import 'support/fake_api.dart';

const _question = '推荐一部轻松的喜剧电影';

Map<String, Object?> card(int id, String title, {int year = 2020}) => {
  'id': id,
  'library_id': 1,
  'kind': 'movie',
  'title': title,
  'year': year,
  'genres': <String>[],
  'poster': <Object>[],
  'backdrop': <Object>[],
  'watched': false,
};

Map<String, Object?> searchPage(List<Map<String, Object?>> items) => {
  'items': items,
  'total': items.length,
  'people': <Object>[],
  'episodes': <Object>[],
};

String route(String q) => 'GET /api/search?q=$q&offset=0&limit=$browsePageSize';

Map<String, Object?> asked({
  Map<String, Object?>? understood,
  List<Map<String, Object?>>? items,
  int? total,
}) => {
  'understood':
      understood ??
      {
        'kind': 'movie',
        'genres': ['喜剧'],
        'year_from': 2000,
        'max_runtime': 120,
        'watched': false,
        'semantic_query': '轻松愉快、适合全家一起看',
      },
  'items': items ?? [card(11, '三傻大闹宝莱坞', year: 2009)],
  'total': total ?? (items?.length ?? 1),
  'semantic_used': true,
};

void main() {
  group('looksLikeQuestion', () {
    test('descriptions and questions are recognised', () {
      for (final text in [
        '推荐一部轻松的喜剧电影',
        '有没有适合全家看的动画片',
        '想看点烧脑的',
        '周末晚上想放松一下看点什么好呢',
        '最近高分的科幻片？',
      ]) {
        expect(looksLikeQuestion(text), isTrue, reason: text);
      }
    });

    test('titles and names are not', () {
      for (final text in [
        '流浪地球',
        '吴京',
        'Inception',
        'The Lord of the Rings',
        '三体',
        '',
      ]) {
        expect(looksLikeQuestion(text), isFalse, reason: text);
      }
    });
  });

  group('AskFilters', () {
    final filters = AskFilters.fromJson({
      'kind': 'movie',
      'genres': ['喜剧', '爱情'],
      'year_from': 2000,
      'min_rating': 7.5,
      'max_runtime': 120,
      'watched': false,
      'semantic_query': ' 轻松 ',
    });

    test('describes every active condition', () {
      expect(filters.active, [
        AskCondition.kind,
        AskCondition.genres,
        AskCondition.years,
        AskCondition.rating,
        AskCondition.runtime,
        AskCondition.watched,
        AskCondition.semantic,
      ]);
      expect(
        [for (final c in filters.active) filters.describe(c)],
        ['电影', '喜剧 · 爱情', '2000 年后', '评分 7.5+', '2 小时内', '没看过的', '像：轻松'],
      );
    });

    test('year ranges and minutes read naturally', () {
      expect(
        const AskFilters(
          yearFrom: 1990,
          yearTo: 1999,
        ).describe(AskCondition.years),
        '1990–1999 年',
      );
      expect(
        const AskFilters(yearTo: 1999).describe(AskCondition.years),
        '1999 年前',
      );
      expect(
        const AskFilters(
          yearFrom: 2010,
          yearTo: 2010,
        ).describe(AskCondition.years),
        '2010 年',
      );
      expect(
        const AskFilters(maxRuntime: 90).describe(AskCondition.runtime),
        '90 分钟内',
      );
    });

    test('removing a condition keeps the others and serialises compactly', () {
      final without = filters.without(AskCondition.genres);
      expect(without.genres, isEmpty);
      expect(without.toJson(), {
        'kind': 'movie',
        'year_from': 2000,
        'min_rating': 7.5,
        'max_runtime': 120,
        'watched': false,
        'semantic_query': ' 轻松 ',
      });
      expect(const AskFilters().isEmpty, isTrue);
    });
  });

  late FakeAdminApi api;
  late GoRouter router;

  Future<void> pumpSearch(
    WidgetTester tester, {
    required String query,
    AiAvailability ai = const AiAvailability(chat: true, askSearch: true),
    bool tv = false,
    void Function(FakeAdminApi api)? setup,
  }) async {
    api = FakeAdminApi();
    final dio = Dio(BaseOptions(baseUrl: 'http://test'))
      ..httpClientAdapter = api;
    router = GoRouter(
      initialLocation: '/search?q=$query',
      routes: [
        GoRoute(
          path: '/search',
          builder: (_, state) =>
              SearchPage(query: state.uri.queryParameters['q'] ?? ''),
        ),
        GoRoute(
          path: '/ai',
          builder: (_, state) =>
              Scaffold(body: Text('对话:${state.uri.queryParameters['q']}')),
        ),
        GoRoute(
          path: '/item/:id',
          builder: (_, _) => const Scaffold(body: Text('详情页')),
        ),
      ],
    );
    addTearDown(router.dispose);
    tester.view.physicalSize = const Size(390 * 3, 1600 * 3);
    tester.view.devicePixelRatio = 3;
    addTearDown(tester.view.reset);
    api.on(route(_question), searchPage([card(1, '快乐星球')]));
    api.on(route('流浪'), searchPage([card(1, '流浪地球')]));
    api.on(route('不存在'), searchPage([]));
    setup?.call(api);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          apiClientProvider.overrideWithValue(dio),
          apiBaseUrlProvider.overrideWithValue('http://test'),
          aiAvailabilityProvider.overrideWithValue(ai),
          televisionProvider.overrideWithValue(tv),
        ],
        child: MaterialApp.router(theme: buildTheme(), routerConfig: router),
      ),
    );
    // 等待指示是循环动画，不能 pumpAndSettle
    for (var i = 0; i < 10; i++) {
      await tester.pump(const Duration(milliseconds: 100));
    }
  }

  int asks() =>
      api.requests.where((r) => r == 'POST /api/ai/ask-search').length;

  testWidgets('a description gets an AI block next to the normal results', (
    tester,
  ) async {
    await pumpSearch(
      tester,
      query: _question,
      setup: (api) => api.on('POST /api/ai/ask-search', asked()),
    );

    expect(find.text('AI 理解为'), findsOneWidget);
    for (final chip in [
      '电影',
      '喜剧',
      '2000 年后',
      '2 小时内',
      '没看过的',
      '像：轻松愉快、适合全家一起看',
    ]) {
      expect(
        find.widgetWithText(InputChip, chip),
        findsOneWidget,
        reason: chip,
      );
    }
    expect(find.text('三傻大闹宝莱坞'), findsOneWidget);
    // 普通结果照常显示
    expect(find.text('快乐星球'), findsOneWidget);
    expect(api.bodies['POST /api/ai/ask-search'], {
      'query': _question,
      'limit': 12,
    });
    expect(find.text('去对话继续问'), findsOneWidget);
  });

  testWidgets('removing a condition re-queries without asking the model', (
    tester,
  ) async {
    await pumpSearch(
      tester,
      query: _question,
      setup: (api) {
        api.on('POST /api/ai/ask-search', asked());
        api.on(
          'POST /api/ai/ask-search/query',
          asked(
            understood: {
              'kind': 'movie',
              'genres': ['喜剧'],
              'year_from': 2000,
              'max_runtime': 120,
              'semantic_query': '轻松愉快、适合全家一起看',
            },
            items: [card(11, '三傻大闹宝莱坞'), card(12, '功夫')],
          ),
        );
      },
    );

    await tester.tap(
      find.descendant(
        of: find.widgetWithText(InputChip, '没看过的'),
        matching: find.byIcon(Icons.clear),
      ),
    );
    for (var i = 0; i < 5; i++) {
      await tester.pump(const Duration(milliseconds: 100));
    }

    expect(api.bodies['POST /api/ai/ask-search/query'], {
      'filters': {
        'kind': 'movie',
        'genres': ['喜剧'],
        'year_from': 2000,
        'max_runtime': 120,
        'semantic_query': '轻松愉快、适合全家一起看',
      },
      'limit': 12,
    });
    expect(asks(), 1);
    expect(find.text('功夫'), findsOneWidget);
    expect(find.widgetWithText(InputChip, '没看过的'), findsNothing);
  });

  testWidgets('tapping the kind chip switches between movies and series', (
    tester,
  ) async {
    await pumpSearch(
      tester,
      query: _question,
      setup: (api) {
        api.on('POST /api/ai/ask-search', asked());
        api.on('POST /api/ai/ask-search/query', asked(items: []));
      },
    );

    await tester.tap(find.widgetWithText(InputChip, '电影'));
    for (var i = 0; i < 5; i++) {
      await tester.pump(const Duration(milliseconds: 100));
    }

    expect(
      (api.bodies['POST /api/ai/ask-search/query']! as Map)['filters'],
      containsPair('kind', 'series'),
    );
    expect(find.text('库里没有符合这些条件的片'), findsOneWidget);
  });

  testWidgets('a failing model only costs the block, not the results', (
    tester,
  ) async {
    await pumpSearch(
      tester,
      query: _question,
      setup: (api) => api.on('POST /api/ai/ask-search', {
        'error': {'code': 'ai_unavailable', 'message': 'AI 暂不可用：超时'},
      }, status: 502),
    );

    expect(find.text('AI 暂不可用'), findsOneWidget);
    expect(find.text('AI 理解为'), findsNothing);
    expect(find.text('快乐星球'), findsOneWidget);
  });

  testWidgets('an empty normal result also asks the model', (tester) async {
    await pumpSearch(
      tester,
      query: '不存在',
      setup: (api) => api.on(
        'POST /api/ai/ask-search',
        asked(understood: {'semantic_query': '不存在'}, items: []),
      ),
    );

    expect(asks(), 1);
    expect(find.text('没有找到「不存在」'), findsOneWidget);
    expect(find.widgetWithText(InputChip, '像：不存在'), findsOneWidget);
  });

  testWidgets('no conditions means no results, not the whole library', (
    tester,
  ) async {
    await pumpSearch(
      tester,
      query: _question,
      setup: (api) => api.on(
        'POST /api/ai/ask-search',
        asked(understood: <String, Object?>{}, items: [card(11, '三傻大闹宝莱坞')]),
      ),
    );

    expect(find.textContaining('没有提取出具体条件'), findsOneWidget);
    expect(find.text('三傻大闹宝莱坞'), findsNothing);
    // 普通结果不受影响
    expect(find.text('快乐星球'), findsOneWidget);
  });

  testWidgets('titles do not trigger the model', (tester) async {
    await pumpSearch(
      tester,
      query: '流浪',
      setup: (api) => api.on('POST /api/ai/ask-search', asked()),
    );

    expect(asks(), 0);
    expect(find.text('AI 理解为'), findsNothing);
    expect(find.text('流浪地球'), findsOneWidget);
  });

  testWidgets('hidden when AI is not configured and on TV', (tester) async {
    await pumpSearch(
      tester,
      query: _question,
      ai: const AiAvailability(),
      setup: (api) => api.on('POST /api/ai/ask-search', asked()),
    );
    expect(asks(), 0);
    expect(find.text('AI 理解为'), findsNothing);
    expect(find.text('快乐星球'), findsOneWidget);

    await pumpSearch(
      tester,
      query: _question,
      tv: true,
      setup: (api) => api.on('POST /api/ai/ask-search', asked()),
    );
    expect(asks(), 0);
    expect(find.text('AI 理解为'), findsNothing);
  });

  testWidgets('continue in chat carries the question over', (tester) async {
    await pumpSearch(
      tester,
      query: _question,
      setup: (api) => api.on('POST /api/ai/ask-search', asked()),
    );

    await tester.tap(find.text('去对话继续问'));
    await tester.pumpAndSettle();

    expect(find.text('对话:$_question'), findsOneWidget);
  });

  testWidgets('no chat entry when only the search block is available', (
    tester,
  ) async {
    await pumpSearch(
      tester,
      query: _question,
      ai: const AiAvailability(askSearch: true),
      setup: (api) => api.on('POST /api/ai/ask-search', asked()),
    );

    expect(find.text('AI 理解为'), findsOneWidget);
    expect(find.text('去对话继续问'), findsNothing);
  });
}
