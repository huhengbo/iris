import 'package:flutter_test/flutter_test.dart';
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
}
