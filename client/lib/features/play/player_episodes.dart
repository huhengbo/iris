part of 'player_page.dart';

/// 剧集的季集号，从签发的片名「剧名 S01E02」里取；电影返回 null
({int season, int episode})? episodeNumberOf(String title) {
  final match = _episodeTitle.firstMatch(title);
  if (match == null) return null;
  return (season: int.parse(match[2]!), episode: int.parse(match[3]!));
}

/// 上一集 / 下一集按季的顺序走。特别篇（第 0 季）不夹在正片中间，
/// 只有正在看特别篇时才在特别篇里前后切换。
List<SeasonSummary> playOrderSeasons(
  List<SeasonSummary> seasons,
  int currentSeason,
) {
  final ordered = [
    for (final season in seasons)
      if ((season.number == 0) == (currentSeason == 0)) season,
  ]..sort((a, b) => a.number.compareTo(b.number));
  return ordered;
}

/// 在 [episodes] 里从 [from] 往 [step] 方向找下一集有文件可放的，找不到返回 null
int? nextPlayableIndex(List<Episode> episodes, int from, int step) {
  for (var i = from + step; i >= 0 && i < episodes.length; i += step) {
    if (episodes[i].versions.isNotEmpty) return i;
  }
  return null;
}

/// 正在放的这一集在剧里的位置：所在的季、季里的分集，以及能前后切的季。
/// 跨季时才再取相邻那一季的分集。
class EpisodeQueue {
  const EpisodeQueue({
    required this.seasons,
    required this.season,
    required this.index,
  });

  /// 按 [playOrderSeasons] 排好的季
  final List<SeasonSummary> seasons;
  final SeasonDetail season;

  /// 正在放的一集在 [season] 里的下标
  final int index;

  Episode get current => season.episodes[index];

  int get _seasonIndex => seasons.indexWhere((s) => s.id == season.id);

  bool get hasPrevious =>
      nextPlayableIndex(season.episodes, index, -1) != null || _seasonIndex > 0;

  bool get hasNext =>
      nextPlayableIndex(season.episodes, index, 1) != null ||
      (_seasonIndex >= 0 && _seasonIndex < seasons.length - 1);

  /// 季里没有了就到相邻的季：往后是那一季的第一集，往前是最后一集
  Future<Episode?> neighbour(Dio dio, String baseUrl, int step) async {
    final within = nextPlayableIndex(season.episodes, index, step);
    if (within != null) return season.episodes[within];
    final other = _seasonIndex + step;
    if (_seasonIndex < 0 || other < 0 || other >= seasons.length) return null;
    final episodes = (await _fetchSeason(
      dio,
      baseUrl,
      seasons[other].id,
    )).episodes;
    final start = step > 0 ? -1 : episodes.length;
    final found = nextPlayableIndex(episodes, start, step);
    return found == null ? null : episodes[found];
  }
}

Future<SeasonDetail> _fetchSeason(Dio dio, String baseUrl, int id) async {
  final response = await dio.get<Map<String, dynamic>>('/api/seasons/$id');
  return SeasonDetail.fromJson(response.data!, baseUrl);
}

/// 取剧集和这一季的分集，找出正在放的一集。不是剧集或对不上时返回 null，
/// 底栏就不出上一集 / 下一集 / 选集。
Future<EpisodeQueue?> loadEpisodeQueue(
  Dio dio,
  String baseUrl, {
  required int itemId,
  required String title,
}) async {
  final number = episodeNumberOf(title);
  if (number == null) return null;
  final detail = await fetchMediaDetail(dio, baseUrl, itemId);
  final seasons = playOrderSeasons(detail.seasons, number.season);
  final summary = seasons.where((s) => s.number == number.season).firstOrNull;
  if (summary == null) return null;
  final season = await _fetchSeason(dio, baseUrl, summary.id);
  final index = season.episodes.indexWhere((e) => e.number == number.episode);
  if (index < 0) return null;
  return EpisodeQueue(seasons: seasons, season: season, index: index);
}

/// 选集：列出这一季的分集，当前这一集打勾，没有文件的灰掉。选了返回那一集。
Future<Episode?> showEpisodePicker(BuildContext context, EpisodeQueue queue) {
  final season = queue.season;
  return showDialog<Episode>(
    context: context,
    builder: (context) => Dialog(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 420),
        child: SingleChildScrollView(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 12),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              PickerHeader(
                title: '选集',
                subtitle: season.number == 0 ? '特别篇' : '第 ${season.number} 季',
              ),
              for (final (i, episode) in season.episodes.indexed)
                _ScrollIntoView(
                  child: PickerOption(
                    title: '第 ${episode.number} 集',
                    subtitle: episode.versions.isEmpty
                        ? '没有可播放的文件'
                        : episode.name,
                    selected: i == queue.index,
                    autofocus: i == queue.index,
                    enabled: episode.versions.isNotEmpty,
                    onTap: () => Navigator.of(context).pop(episode),
                  ),
                ),
            ],
          ),
        ),
      ),
    ),
  );
}
