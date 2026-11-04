part of 'player_page.dart';

/// 内置播放器要放什么。地址已经用当前服务器拼好。
class PlayerArgs {
  const PlayerArgs({
    required this.playableId,
    this.versionId,
    required this.title,
    required this.url,
    this.subtitles = const [],
    this.start,
    this.castable = false,
    this.cellularConfirmed = false,
    this.playlistId,
    this.itemId,
  });

  /// 用服务器签发的播放凭据和当前服务器地址组装
  factory PlayerArgs.fromTicket(
    PlayTicket ticket,
    String baseUrl, {
    bool castable = false,
    bool cellularConfirmed = false,
    int? playlistId,
    int? itemId,
  }) => PlayerArgs(
    playableId: ticket.playableId,
    versionId: ticket.versionId,
    title: ticket.title,
    url: '$baseUrl${ticket.path}',
    subtitles: [
      for (final s in ticket.subtitles)
        SubtitleTrack.uri(
          '$baseUrl${s.path}',
          title: s.label,
          language: s.language,
        ),
    ],
    start: ticket.position,
    castable: castable,
    cellularConfirmed: cellularConfirmed,
    playlistId: playlistId,
    itemId: itemId,
  );

  final int playableId;

  /// 播放的版本，打开文件后按它上报流信息（ADR 0019）
  final int? versionId;
  final String title;
  final String url;

  /// 外挂字幕，第一条默认选中
  final List<SubtitleTrack> subtitles;

  /// 上次看到的位置
  final Duration? start;

  /// 控制条上有投屏按钮（手机 App）。选了电视后播放器带着它退出，由调用方接着投屏
  final bool castable;

  /// 播放入口已经提示并得到用户同意使用移动数据。
  final bool cellularConfirmed;

  /// 播放列表连播上下文；外部播放器和投屏不设置此字段。
  final int? playlistId;

  /// 所属作品。剧集据此取分集，底栏才有上一集 / 下一集 / 选集
  final int? itemId;

  /// 顶栏的片名。服务端给剧集签发的标题是「剧名 S01E02」
  /// （backend/app/playback/service.py 的 _target），界面按术语表写成
  /// 「剧名 · 第 1 季 第 2 集」，和详情页、播放器选择里的一致；电影原样返回。
  String get displayTitle {
    final match = _episodeTitle.firstMatch(title);
    if (match == null) return title;
    final season = int.parse(match[2]!);
    return '${match[1]} · ${formatEpisode(season, int.parse(match[3]!))}';
  }
}

/// 剧集在签发的播放凭据里的片名格式
final _episodeTitle = RegExp(r'^(.+) S(\d+)E(\d+)$');
