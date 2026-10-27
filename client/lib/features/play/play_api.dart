import 'dart:convert';

import 'package:android_intent_plus/android_intent.dart';
import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../app/tv.dart';
import '../../core/api/api_client.dart';

/// 签发播放链接时的平台。服务端据此记录播放来源（ADR 0015）。
enum PlayPlatform {
  ios('ios', [
    ExternalPlayer.infuse,
    ExternalPlayer.vlc,
    ExternalPlayer.nPlayer,
    ExternalPlayer.senPlayer,
    ExternalPlayer.copyLink,
  ]),
  android('android', [ExternalPlayer.system, ExternalPlayer.copyLink]),

  /// 安卓 App：内置播放器为主，也能交给系统里装的其他播放器（ADR 0010 第二期），
  /// 或者投到同一个 Wi-Fi 里的电视（ADR 0018）
  androidApp('android', [
    ExternalPlayer.builtin,
    ExternalPlayer.cast,
    ExternalPlayer.system,
    ExternalPlayer.copyLink,
  ]),

  /// 安卓 TV：同一个 App，电视上没法粘贴，不给复制链接（M9）
  androidTv('android', [ExternalPlayer.builtin, ExternalPlayer.system]),

  /// 桌面浏览器没有可调起的播放器，只能复制链接
  desktop('web', [ExternalPlayer.copyLink]);

  const PlayPlatform(this.client, this.players);

  final String client;
  final List<ExternalPlayer> players;
}

/// 本机播放器（ADR 0010、0012）。
enum ExternalPlayer {
  builtin('内置播放器', Icons.play_arrow_rounded),
  cast('投屏到电视', Icons.cast_rounded),
  infuse('Infuse', Icons.play_circle_outline),
  vlc('VLC', Icons.smart_display_outlined),
  nPlayer('nPlayer', Icons.movie_outlined),
  senPlayer('SenPlayer', Icons.ondemand_video_outlined),
  system('系统播放器', Icons.open_in_new),
  copyLink('复制播放链接', Icons.link);

  const ExternalPlayer(this.label, this.icon);

  final String label;
  final IconData icon;

  /// 复制链接不算播放器；投屏看在不在电视旁边，每次现选。都不能记成默认
  bool get rememberable => this != copyLink && this != cast;
}

/// 要播放什么。电影可以是条目（服务端选默认版本）或指定版本；剧集是某一集或其中一个版本。
class PlayTarget {
  const PlayTarget.version(this.id) : _field = 'version_id';
  const PlayTarget.item(this.id) : _field = 'item_id';
  const PlayTarget.episode(this.id) : _field = 'episode_id';

  final String _field;
  final int id;

  Map<String, Object> body(
    PlayPlatform platform, {
    bool builtin = false,
    int? subtitleId,
  }) {
    final body = <String, Object>{_field: id, 'client': platform.client};
    if (builtin) body['player'] = 'builtin';
    if (subtitleId != null) body['subtitle_id'] = subtitleId;
    return body;
  }
}

class PlaySubtitle {
  const PlaySubtitle({
    required this.label,
    required this.url,
    required this.path,
    this.language,
    this.source = 'external',
  });

  factory PlaySubtitle.fromJson(Map<String, dynamic> json) => PlaySubtitle(
    label: json['label'] as String,
    language: json['language'] as String?,
    url: json['url'] as String?,
    path: json['path'] as String,
    source: json['source'] as String? ?? 'external',
  );

  final String label;
  final String? language;

  /// 对外地址，服务器没配 public_url 时为空
  final String? url;

  /// 相对服务器根的路径，内置播放器用自己连的服务器地址拼
  final String path;

  /// external 外挂字幕、online 服务端保存的在线字幕
  final String source;
}

class PlayTicket {
  const PlayTicket({
    required this.playableId,
    required this.versionId,
    required this.title,
    required this.url,
    required this.path,
    required this.subtitles,
    this.selectedSubtitleId,
    this.launch = const {},
    this.position,
  });

  factory PlayTicket.fromJson(Map<String, dynamic> json) => PlayTicket(
    playableId: json['playable_id'] as int,
    versionId: json['version_id'] as int,
    title: json['title'] as String,
    url: json['url'] as String?,
    path: json['path'] as String,
    subtitles: [
      for (final item in json['subtitles'] as List<dynamic>)
        PlaySubtitle.fromJson(item as Map<String, dynamic>),
    ],
    selectedSubtitleId: json['selected_subtitle_id'] as int?,
    launch: json['launch'] == null
        ? const {}
        : Map<String, String>.from(json['launch'] as Map),
    position: switch (json['position']) {
      final int seconds => Duration(seconds: seconds),
      _ => null,
    },
  );

  /// 播放进度按它上报
  final int playableId;

  /// 实际播放的版本，从内置播放器转去投屏时按它重新签发
  final int versionId;
  final String title;

  /// 播放跳转地址，24 小时内有效。服务器没配 public_url 时为空，只能用内置播放器
  final String? url;

  /// 相对服务器根的播放路径
  final String path;

  /// 第一条是默认字幕，调起链接里带的就是它
  final List<PlaySubtitle> subtitles;
  final int? selectedSubtitleId;

  /// 服务端拼好的调起链接，键是播放器名；没有对外地址时为空
  final Map<String, String> launch;

  /// 上次看到的位置，内置播放器从这里接着放
  final Duration? position;

  Uri? launchUri(ExternalPlayer player) {
    final link = switch (player) {
      ExternalPlayer.infuse => launch['infuse'],
      ExternalPlayer.vlc => launch['vlc'],
      ExternalPlayer.nPlayer => launch['nplayer'],
      ExternalPlayer.senPlayer => launch['senplayer'],
      ExternalPlayer.system => launch['android'],
      ExternalPlayer.builtin ||
      ExternalPlayer.cast ||
      ExternalPlayer.copyLink => null,
    };
    return link == null ? null : Uri.parse(link);
  }
}

Future<PlayTicket> issuePlay(
  Dio dio,
  PlayTarget target,
  PlayPlatform platform, {
  bool builtin = false,
  int? subtitleId,
}) async {
  final response = await dio.post<Map<String, dynamic>>(
    '/api/play',
    data: target.body(platform, builtin: builtin, subtitleId: subtitleId),
  );
  return PlayTicket.fromJson(response.data!);
}

/// 播放器只认状态码，真正的原因在跳转接口的响应体里；不跟随跳转地请求一次拿回来。
Future<({int? statusCode, String reason})> fetchPlayFailure(
  Dio dio,
  String url,
) async {
  final response = await dio.get<String>(
    url,
    options: Options(
      followRedirects: false,
      validateStatus: (_) => true,
      // 播放地址自带签名，不用登录令牌
      extra: {skipAuthExtra: true},
      responseType: ResponseType.plain,
    ),
  );
  return (statusCode: response.statusCode, reason: response.data?.trim() ?? '');
}

/// 播放列表里接着要播的版本；没有下一项时为 null。
Future<int?> nextPlaylistVersionId(
  Dio dio,
  int playlistId,
  int currentPlayableId,
) async {
  final response = await dio.get<Map<String, dynamic>?>(
    '/api/playlists/$playlistId/next',
    queryParameters: {'current_playable_id': currentPlayableId},
  );
  return response.data?['version_id'] as int?;
}

/// 内置播放器上报进度。看到九成服务端会记成已看。
Future<void> saveProgress(
  Dio dio,
  int playableId, {
  required Duration position,
  required Duration duration,
  String event = 'progress',
}) => dio.put<void>(
  '/api/playables/$playableId/progress',
  data: {
    'position': position.inSeconds,
    'duration': duration.inSeconds,
    'event': event,
  },
);

/// 内置播放器打开文件后上报流信息，详情页的版本据此显示音轨和内封字幕（ADR 0019）。
Future<void> reportMediaInfo(
  Dio dio,
  int versionId,
  Map<String, Object?> body,
) => dio.put<void>('/api/versions/$versionId/media-info', data: body);

/// 把 mpv 的 track-list（JSON）整理成上报的内容。外挂字幕是本系统挂上去的，不算；
/// 超出服务端长度限制的字段丢掉，不让一条奇怪的标签拖累整次上报。
Map<String, Object?> mediaInfoFromTrackList(
  String trackList, {
  required Duration duration,
  String? gamma,
  int? width,
  int? height,
}) {
  final tracks = [
    for (final t in jsonDecode(trackList) as List<dynamic>)
      if (t case final Map<String, dynamic> track
          when track['external'] != true)
        track,
  ];
  String? text(Object? value, int max) =>
      value is String && value.isNotEmpty && value.length <= max ? value : null;
  String? title(Object? value) => value is String && value.isNotEmpty
      ? (value.length > 200 ? value.substring(0, 200) : value)
      : null;
  int? positive(Object? value) => value is int && value > 0 ? value : null;

  // 封面图也算视频轨，跳过；优先正在播的那条
  final videos = tracks
      .where((t) => t['type'] == 'video' && t['image'] != true)
      .toList();
  final video =
      videos.where((t) => t['selected'] == true).firstOrNull ??
      videos.firstOrNull;
  final hdr = video?['dolby-vision-profile'] != null
      ? 'dolby_vision'
      : switch (gamma) {
          'pq' => 'hdr10',
          'hlg' => 'hlg',
          _ => null,
        };
  final w = positive(video?['demux-w']) ?? positive(width);
  final h = positive(video?['demux-h']) ?? positive(height);
  return {
    'duration': positive(duration.inSeconds),
    'width': w != null && w <= 16384 ? w : null,
    'height': h != null && h <= 16384 ? h : null,
    'video_codec': text(video?['codec'], 32),
    'hdr': hdr,
    'audio': [
      for (final t in tracks.where((t) => t['type'] == 'audio').take(64))
        {
          'language': text(t['lang'], 16),
          'title': title(t['title']),
          'codec': text(t['codec'], 32),
          'channels': switch (positive(t['demux-channel-count'])) {
            final int c when c <= 64 => c,
            _ => null,
          },
          'default': t['default'] == true,
        },
    ],
    'subtitles': [
      for (final t in tracks.where((t) => t['type'] == 'sub').take(128))
        {
          'language': text(t['lang'], 16),
          'title': title(t['title']),
          'codec': text(t['codec'], 32),
          'forced': t['forced'] == true,
          'default': t['default'] == true,
        },
    ],
  };
}

Future<String?> getSubtitleLanguage(Dio dio) async {
  final response = await dio.get<Map<String, dynamic>>('/api/me/preferences');
  return response.data?['subtitle_language'] as String?;
}

Future<void> saveSubtitleLanguage(Dio dio, String? language) => dio.patch<void>(
  '/api/me/preferences',
  data: {'subtitle_language': language},
);

/// 已看标记的范围：电影或整部剧、一季、一集。
enum WatchedScope {
  item('items'),
  season('seasons'),
  episode('episodes');

  const WatchedScope(this.path);

  final String path;
}

Future<void> setWatched(
  Dio dio,
  WatchedScope scope,
  int id, {
  required bool watched,
}) async {
  final path = '/api/${scope.path}/$id/watched';
  if (watched) {
    await dio.put<void>(path);
  } else {
    await dio.delete<void>(path);
  }
}

// ---- 平台相关，测试时替换 ----

/// Web 上 defaultTargetPlatform 反映的是浏览器所在的系统。
final playPlatformProvider = Provider<PlayPlatform>(
  (ref) => switch (defaultTargetPlatform) {
    TargetPlatform.iOS => PlayPlatform.ios,
    TargetPlatform.android when !kIsWeb =>
      ref.watch(televisionProvider)
          ? PlayPlatform.androidTv
          : PlayPlatform.androidApp,
    TargetPlatform.android => PlayPlatform.android,
    _ => PlayPlatform.desktop,
  },
);

typedef UrlOpener = Future<bool> Function(Uri uri);

/// 在当前页打开调起链接：新窗口会被 Safari 拦截，自定义协议也不需要新页。
final urlOpenerProvider = Provider<UrlOpener>(
  (ref) =>
      (uri) => launchUrl(uri, webOnlyWindowName: '_self'),
);

typedef VideoIntentLauncher = Future<void> Function(String url);

/// 安卓 App 交给系统里的播放器：发 ACTION_VIEW，每次弹系统的选择器。
final videoIntentProvider = Provider<VideoIntentLauncher>(
  (ref) =>
      (url) => AndroidIntent(
        action: 'action_view',
        data: url,
        type: 'video/*',
      ).launchChooser('选择播放器'),
);

/// 本机记住的默认播放器（按平台分开），以及内置播放器的画面是否填满屏幕。
abstract interface class PlayerPrefs {
  Future<ExternalPlayer?> read(PlayPlatform platform);
  Future<void> write(PlayPlatform platform, ExternalPlayer? player);
  Future<bool> readFill();
  Future<void> writeFill(bool fill);
}

class SharedPreferencesPlayerPrefs implements PlayerPrefs {
  final SharedPreferencesAsync _prefs = SharedPreferencesAsync();

  String _key(PlayPlatform platform) => 'play.${platform.name}.player';

  @override
  Future<ExternalPlayer?> read(PlayPlatform platform) async {
    final name = await _prefs.getString(_key(platform));
    final player = ExternalPlayer.values.asNameMap()[name];
    return platform.players.contains(player) ? player : null;
  }

  @override
  Future<void> write(PlayPlatform platform, ExternalPlayer? player) =>
      player == null
      ? _prefs.remove(_key(platform))
      : _prefs.setString(_key(platform), player.name);

  static const _fillKey = 'play.builtin.fill';

  @override
  Future<bool> readFill() async => await _prefs.getBool(_fillKey) ?? false;

  @override
  Future<void> writeFill(bool fill) => _prefs.setBool(_fillKey, fill);
}

class MemoryPlayerPrefs implements PlayerPrefs {
  final Map<PlayPlatform, ExternalPlayer> _players = {};
  bool _fill = false;

  @override
  Future<bool> readFill() async => _fill;

  @override
  Future<void> writeFill(bool fill) async => _fill = fill;

  @override
  Future<ExternalPlayer?> read(PlayPlatform platform) async =>
      _players[platform];

  @override
  Future<void> write(PlayPlatform platform, ExternalPlayer? player) async {
    if (player == null) {
      _players.remove(platform);
    } else {
      _players[platform] = player;
    }
  }
}

final playerPrefsProvider = Provider<PlayerPrefs>(
  (ref) => SharedPreferencesPlayerPrefs(),
);

/// 当前平台记住的默认播放器；为空时每次询问。
final defaultPlayerProvider =
    AsyncNotifierProvider<DefaultPlayerNotifier, ExternalPlayer?>(
      DefaultPlayerNotifier.new,
    );

class DefaultPlayerNotifier extends AsyncNotifier<ExternalPlayer?> {
  @override
  Future<ExternalPlayer?> build() =>
      ref.watch(playerPrefsProvider).read(ref.watch(playPlatformProvider));

  Future<void> set(ExternalPlayer? player) async {
    await ref
        .read(playerPrefsProvider)
        .write(ref.read(playPlatformProvider), player);
    state = AsyncData(player);
  }
}
