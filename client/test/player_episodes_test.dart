import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/core/api/paging.dart';
import 'package:iris/features/library/browse_api.dart';
import 'package:iris/features/play/player_page.dart';

const _file = [MediaVersion(id: 1, label: '1080p', subtitles: [])];

Episode _episode(int number, {bool playable = true}) => Episode(
  id: 100 + number,
  number: number,
  versions: playable ? _file : const [],
  favorite: false,
);

SeasonSummary _season(int id, int number) =>
    SeasonSummary(id: id, number: number, episodeCount: 3);

void main() {
  test('从签发的片名取季集号，电影没有', () {
    expect(episodeNumberOf('三体 S01E02'), (season: 1, episode: 2));
    expect(episodeNumberOf('三体 S00E05'), (season: 0, episode: 5));
    expect(episodeNumberOf('流浪地球'), isNull);
  });

  test('上一集 / 下一集按季号走，特别篇不夹在正片中间', () {
    final seasons = [_season(3, 2), _season(9, 0), _season(1, 1)];
    expect(playOrderSeasons(seasons, 1).map((s) => s.number), [1, 2]);
    // 正在看特别篇时只在特别篇里切
    expect(playOrderSeasons(seasons, 0).map((s) => s.number), [0]);
  });

  test('前后找有文件可放的一集，跳过没有文件的', () {
    final episodes = [_episode(1), _episode(2, playable: false), _episode(3)];
    expect(nextPlayableIndex(episodes, 0, 1), 2);
    expect(nextPlayableIndex(episodes, 2, -1), 0);
    expect(nextPlayableIndex(episodes, 2, 1), isNull);
    expect(nextPlayableIndex(episodes, 0, -1), isNull);
    // 跨季时从一季的两端外面起找
    expect(nextPlayableIndex(episodes, -1, 1), 0);
    expect(nextPlayableIndex(episodes, episodes.length, -1), 2);
  });

  test('季里到头了还有相邻的季时，上一集 / 下一集仍可用', () {
    final seasons = [_season(1, 1), _season(2, 2)];
    SeasonDetail season(int id, int number) => SeasonDetail(
      id: id,
      itemId: 7,
      number: number,
      episodes: [_episode(1), _episode(2)],
    );

    final firstOfFirst = EpisodeQueue(
      seasons: seasons,
      season: season(1, 1),
      index: 0,
    );
    expect(firstOfFirst.hasPrevious, isFalse);
    expect(firstOfFirst.hasNext, isTrue);

    final lastOfFirst = EpisodeQueue(
      seasons: seasons,
      season: season(1, 1),
      index: 1,
    );
    expect(lastOfFirst.hasNext, isTrue);

    final lastOfLast = EpisodeQueue(
      seasons: seasons,
      season: season(2, 2),
      index: 1,
    );
    expect(lastOfLast.hasNext, isFalse);
    expect(lastOfLast.hasPrevious, isTrue);
  });

  test('EpisodeQueue.select 切换到指定季与集并更新前后边界', () {
    final seasons = [_season(1, 1), _season(2, 2)];
    final season1 = SeasonDetail(
      id: 1,
      itemId: 7,
      number: 1,
      episodes: [_episode(1), _episode(2)],
    );
    final season2 = SeasonDetail(
      id: 2,
      itemId: 7,
      number: 2,
      episodes: [_episode(1), _episode(2)],
    );

    final initial = EpisodeQueue(seasons: seasons, season: season1, index: 0);
    final selected = initial.select(season2, 1);
    expect(selected.season.id, 2);
    expect(selected.index, 1);
    expect(selected.current.id, 102);
    expect(selected.hasPrevious, isTrue);
    expect(selected.hasNext, isFalse);
  });

  testWidgets('选集弹窗多季时支持顶部切换季并按需加载并返回新队列', (tester) async {
    final seasons = [_season(1, 1), _season(2, 2)];
    final season1 = SeasonDetail(
      id: 1,
      itemId: 7,
      number: 1,
      episodes: [_episode(1), _episode(2)],
    );
    final season2 = SeasonDetail(
      id: 2,
      itemId: 7,
      number: 2,
      episodes: [
        Episode(
          id: 201,
          number: 1,
          name: '第二季第一集',
          versions: _file,
          favorite: false,
        ),
        Episode(
          id: 202,
          number: 2,
          name: '第二季第二集',
          versions: _file,
          favorite: false,
        ),
      ],
    );
    final queue = EpisodeQueue(seasons: seasons, season: season1, index: 1);
    final loadedIds = <int>[];
    EpisodeQueue? picked;

    await tester.pumpWidget(
      MaterialApp(
        theme: buildTheme(),
        home: Scaffold(
          body: Builder(
            builder: (context) => TextButton(
              onPressed: () async {
                picked = await showEpisodePicker(
                  context,
                  queue,
                  loadSeason: (id) async {
                    loadedIds.add(id);
                    return season2;
                  },
                );
              },
              child: const Text('选集'),
            ),
          ),
        ),
      ),
    );

    await tester.tap(find.text('选集'));
    await tester.pumpAndSettle();

    expect(find.widgetWithText(ChoiceChip, '第 1 季'), findsOneWidget);
    expect(find.widgetWithText(ChoiceChip, '第 2 季'), findsOneWidget);

    await tester.tap(find.widgetWithText(ChoiceChip, '第 2 季'));
    await tester.pumpAndSettle();

    expect(loadedIds, [2]);
    expect(find.text('第二季第一集'), findsOneWidget);

    await tester.tap(find.text('第二季第一集'));
    await tester.pumpAndSettle();

    expect(picked, isNotNull);
    expect(picked!.season.id, 2);
    expect(picked!.index, 0);
    expect(picked!.current.id, 201);
  });

  testWidgets('选集弹窗显示剧照缩略图、2px 琥珀进度条与未选中已看标记', (tester) async {
    final season = SeasonDetail(
      id: 1,
      itemId: 7,
      number: 1,
      episodes: [
        Episode(
          id: 101,
          number: 1,
          name: '已看的一集',
          still: const Artwork([(width: 320, url: 'http://localhost/s1.jpg')]),
          versions: _file,
          watched: true,
          favorite: false,
        ),
        Episode(
          id: 102,
          number: 2,
          name: '正在播的一集',
          still: const Artwork([(width: 320, url: 'http://localhost/s2.jpg')]),
          versions: _file,
          watched: false,
          progress: const PlayProgress(
            position: Duration(minutes: 15),
            duration: Duration(minutes: 30),
          ),
          favorite: false,
        ),
      ],
    );
    final queue = EpisodeQueue(
      seasons: [_season(1, 1)],
      season: season,
      index: 1,
    );

    await tester.pumpWidget(
      MaterialApp(
        theme: buildTheme(),
        home: Scaffold(
          body: Builder(
            builder: (context) => TextButton(
              onPressed: () => showEpisodePicker(context, queue),
              child: const Text('选集'),
            ),
          ),
        ),
      ),
    );

    await tester.tap(find.text('选集'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));

    // 两集都有 72x40 的缩略图容器
    expect(
      find.byWidgetPredicate(
        (w) => w is SizedBox && w.width == 72 && w.height == 40,
      ),
      findsNWidgets(2),
    );
    // 第二集有 2px 进度条（FractionallySizedBox widthFactor = 0.5）
    final bar = tester.widget<FractionallySizedBox>(
      find.byType(FractionallySizedBox),
    );
    expect(bar.widthFactor, closeTo(0.5, 0.01));

    // 第 1 集未选中且已看，展示柔和已看勾；第 2 集正在播，展示琥珀选中勾
    expect(find.byIcon(Icons.check_circle_outline_rounded), findsOneWidget);
    expect(find.byIcon(Icons.check), findsOneWidget);
  });
}
