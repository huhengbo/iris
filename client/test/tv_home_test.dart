import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/app/tv.dart';
import 'package:iris/features/library/browse_api.dart';
import 'package:iris/features/tv/tv_home_page.dart';
import 'package:iris/main.dart';

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
}
