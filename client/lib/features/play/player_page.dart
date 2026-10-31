import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:media_kit/media_kit.dart';
import 'package:media_kit_video/media_kit_video.dart';

import '../../app/motion.dart';
import '../../app/theme.dart';
import '../../app/tv.dart';
import '../../core/widgets/picker_sheet.dart';
import '../../core/format.dart';
import '../../core/network_status.dart';
import '../../core/providers.dart';
import '../auth/auth_controller.dart';
import '../subtitles/subtitle_search_sheet.dart';
import '../subtitles/subtitles_api.dart';
import 'cast_page.dart';
import 'network_guard.dart';
import 'play_api.dart';

part 'player_args.dart';
part 'player_controls.dart';
part 'player_tv_controls.dart';
part 'player_widgets.dart';

/// 横屏沉浸式播放。触屏：点按显示/隐藏控制条，双击左右快退快进、中间暂停，
/// 长按 2 倍速，左右滑动拖进度。遥控器/键盘：控制条隐藏时左右快退快进，
/// 确认键暂停并呼出控制条。
class PlayerPage extends ConsumerStatefulWidget {
  const PlayerPage({super.key, required this.args});

  final PlayerArgs args;

  @override
  ConsumerState<PlayerPage> createState() => _PlayerPageState();
}

class _PlayerPageState extends ConsumerState<PlayerPage>
    with WidgetsBindingObserver {
  final _player = Player();

  /// 电视的解码器和内存都吃紧：4K HEVC 走默认的「硬解后拷回 CPU 再用 GPU 渲染」会卡死，
  /// 改成硬解直接输出到画面（mediacodec_embed）
  late final _video = VideoController(
    _player,
    configuration: ref.read(televisionProvider)
        ? const VideoControllerConfiguration(
            vo: 'mediacodec_embed',
            hwdec: 'mediacodec',
          )
        : const VideoControllerConfiguration(),
  );
  late final Dio _dio;
  final _subscriptions = <StreamSubscription<Object?>>[];
  final _rootFocus = FocusNode(debugLabel: 'player');
  final _playFocus = FocusNode(debugLabel: 'player.play');
  final _seekFocus = FocusNode(debugLabel: 'player.seek');
  late final bool _tv = ref.read(televisionProvider);

  bool _playing = false;
  bool _buffering = true;
  Duration _position = Duration.zero;
  Duration _duration = Duration.zero;
  String? _error;
  bool _subtitlesLoaded = false;

  /// 播放中从「搜索字幕」加载的字幕。media_kit 的轨道列表不一定立刻反映出来，
  /// 本地留一份，下一次打开字幕选择时还能看到并切换。
  final _downloadedSubtitles = <SubtitleTrack>[];

  /// 当前用户能搜索在线字幕，且这个版本已知、有可用的下载器
  bool _canSearchSubtitles = false;

  bool _controls = true;
  Timer? _hideTimer;
  Timer? _scrubTimer;
  Timer? _errorTimer;

  double _rate = 1;
  bool _boost = false;

  /// 画面填满屏幕（裁掉超出的部分）。默认完整显示；片源把黑边压进画面、
  /// 或画面比例和屏幕差得多时，填满才像全屏。本机记住这个选择
  bool _fill = false;

  /// 拖动进度条或滑动屏幕时的目标位置
  Duration? _scrub;

  /// 快进快退的提示，如「+10 秒」
  String? _toast;
  Timer? _toastTimer;

  Timer? _reportTimer;
  Duration? _reported;
  bool _mediaInfoReported = false;
  bool _exiting = false;

  late final NetworkStatusSource _network;
  NetworkKind? _networkKind;
  bool _cellularConfirmed = false;
  bool _networkPromptOpen = false;

  @override
  void initState() {
    super.initState();
    _dio = ref.read(apiClientProvider);
    _network = ref.read(networkStatusProvider);
    _cellularConfirmed = widget.args.cellularConfirmed;
    unawaited(
      ref.read(playerPrefsProvider).readFill().then((fill) {
        if (mounted) setState(() => _fill = fill);
      }),
    );
    WidgetsBinding.instance.addObserver(this);
    SystemChrome.setEnabledSystemUIMode(SystemUiMode.immersiveSticky);
    SystemChrome.setPreferredOrientations([
      DeviceOrientation.landscapeLeft,
      DeviceOrientation.landscapeRight,
    ]);
    final stream = _player.stream;
    _subscriptions.addAll([
      stream.playing.listen((playing) {
        setState(() => _playing = playing);
        if (playing) {
          _scheduleHide();
        } else {
          unawaited(_report(event: 'paused'));
        }
      }),
      stream.buffering.listen((v) => setState(() => _buffering = v)),
      stream.position.listen((v) => setState(() => _position = v)),
      stream.duration.listen((v) {
        setState(() => _duration = v);
        if (v > Duration.zero) {
          unawaited(_loadSubtitles());
          unawaited(_reportMediaInfo());
        }
      }),
      // 画面参数在第一帧解码后才有，HDR 要靠它判断
      stream.videoParams.listen((_) => unawaited(_reportMediaInfo())),
      stream.completed.listen((done) {
        if (done && _duration > Duration.zero) unawaited(_exit(finished: true));
      }),
      stream.error.listen(_onError),
    ]);
    _subscriptions.add(_network.changes.listen(_onNetworkChanged));
    // 播放中每 15 秒存一次进度，中途退出或被杀掉也能接着看
    _reportTimer = Timer.periodic(const Duration(seconds: 15), (_) {
      if (_playing) unawaited(_report());
    });
    // 遥控器进来时焦点落在中央的播放按钮上（DESIGN.md「多端：Android TV」的默认焦点）
    if (_tv) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && _controls) _playFocus.requestFocus();
      });
    }
    unawaited(_startPlayback());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    for (final s in _subscriptions) {
      unawaited(s.cancel());
    }
    _hideTimer?.cancel();
    _scrubTimer?.cancel();
    _errorTimer?.cancel();
    _toastTimer?.cancel();
    _reportTimer?.cancel();
    // 没走 _exit 就被移除时（如切换服务器）也存一次进度
    if (!_exiting) unawaited(_report());
    unawaited(_player.dispose());
    _rootFocus.dispose();
    _playFocus.dispose();
    _seekFocus.dispose();
    SystemChrome.setEnabledSystemUIMode(SystemUiMode.edgeToEdge);
    SystemChrome.setPreferredOrientations(const []);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused) unawaited(_report());
    if (state == AppLifecycleState.resumed) unawaited(_refreshNetwork());
  }

  Future<void> _startPlayback() async {
    final current = await _network.current();
    if (!mounted) return;
    _networkKind = current;
    if (current.isMobile && !_cellularConfirmed) {
      final allowed = await confirmCellularPlayback(
        context,
        duringPlayback: false,
      );
      if (!mounted) return;
      if (!allowed) {
        Navigator.of(context).pop();
        return;
      }
      _cellularConfirmed = true;
    }
    await _open(widget.args.start);
  }

  Future<void> _refreshNetwork() async {
    final current = await _network.current();
    if (mounted) _onNetworkChanged(current);
  }

  void _onNetworkChanged(NetworkKind current) {
    final previous = _networkKind;
    _networkKind = current;
    if (!current.isMobile) _cellularConfirmed = false;
    if (!mounted ||
        previous == null ||
        !isCellularSwitch(previous, current) ||
        _cellularConfirmed ||
        _networkPromptOpen) {
      return;
    }
    unawaited(_handleCellularSwitch());
  }

  Future<void> _handleCellularSwitch() async {
    _networkPromptOpen = true;
    await _player.pause();
    if (!mounted) {
      _networkPromptOpen = false;
      return;
    }
    final allowed = await confirmCellularPlayback(
      context,
      duringPlayback: true,
    );
    _networkPromptOpen = false;
    if (!mounted || _exiting) return;
    if (allowed) {
      _cellularConfirmed = true;
      await _player.play();
    } else {
      _showControls(focusPlay: true);
    }
  }

  Future<void> _open(Duration? start) async {
    setState(() {
      _error = null;
      _buffering = true;
      _subtitlesLoaded = false;
    });
    await _tuneSeeking();
    await _player.open(Media(widget.args.url, start: start));
    if (_rate != 1) await _player.setRate(_rate);
  }

  /// mpv 默认的绝对跳转是精确跳转：先退回上一个关键帧，再解码到目标位置。网络来源（115 直链）
  /// 要把这一段全部下载下来，关键帧稀疏的高码率片源跳一次要等很久。改成直接落在关键帧上，
  /// 偏差在一个关键帧间隔内，和 Emby 系客户端的跳转行为一致。
  Future<void> _tuneSeeking() async {
    final platform = _player.platform;
    if (platform is! NativePlayer) return;
    try {
      // Web 的 NativePlayer stub 没有该原生方法；上面的类型保护保证这里只在 App 执行。
      await (platform as dynamic).setProperty('hr-seek', 'no');
    } on Object {
      // 调不了就保持默认跳转，不影响播放
    }
  }

  /// 文件加载后才能挂外挂字幕。倒序挂，最后选中的是第一条（默认字幕）。
  Future<void> _loadSubtitles() async {
    if (_subtitlesLoaded) return;
    _subtitlesLoaded = true;
    for (final track in widget.args.subtitles.reversed) {
      await _player.setSubtitleTrack(track);
    }
  }

  /// 文件时长和画面参数都有了之后，把 mpv 读到的流信息报给服务端，每次播放报一次。
  /// 只是给详情页看的，失败了不打扰观看，下次播放再报。
  Future<void> _reportMediaInfo() async {
    final versionId = widget.args.versionId;
    final platform = _player.platform;
    final state = _player.state;
    if (_mediaInfoReported ||
        versionId == null ||
        platform is! NativePlayer ||
        state.duration <= Duration.zero ||
        (state.videoParams.w ?? 0) <= 0) {
      return;
    }
    _mediaInfoReported = true;
    try {
      final body = mediaInfoFromTrackList(
        // Web 的 NativePlayer stub 没有该原生方法；上面的类型保护保证这里只在 App 执行。
        await (platform as dynamic).getProperty('track-list') as String,
        duration: state.duration,
        gamma: state.videoParams.gamma,
        width: state.videoParams.w,
        height: state.videoParams.h,
      );
      await reportMediaInfo(_dio, versionId, body);
    } on DioException {
      // 网络问题，下次播放再报
    } on FormatException {
      // track-list 不是预期的 JSON，不报
    }
  }

  /// 播放器的错误文本是英文且带着完整播放地址（含令牌），不直接给人看。
  /// 缺 TrueHD 音轨解码器、PGS 字幕解码器这类次要错误也会走到这里，但视频本身
  /// 能放，所以不立刻判失败：留几秒让片源加载，视频真的没起来才算。
  void _onError(String message) {
    if (!mounted) return;
    // 已经在播时多半是字幕之类的次要错误，提示一下就好
    if (_duration > Duration.zero) {
      _showToast('有一部分内容没能加载');
      return;
    }
    _errorTimer?.cancel();
    _errorTimer = Timer(const Duration(seconds: 4), () {
      if (!mounted || _error != null) return;
      final state = _player.state;
      if (state.duration > Duration.zero && (state.videoParams.w ?? 0) > 0) {
        _showToast('有一部分内容没能加载');
        return;
      }
      setState(() {
        _error = '没能打开这个视频，检查网络后重试';
        _buffering = false;
      });
      unawaited(_explain());
    });
  }

  /// 播放器只认状态码，真正的原因（来源连不上、文件被删等）在跳转接口的响应体里，
  /// 不跟随跳转地请求一次拿回来。
  Future<void> _explain() async {
    try {
      final (:statusCode, :reason) = await fetchPlayFailure(
        _dio,
        widget.args.url,
      );
      if (!mounted || _error == null) return;
      if ((statusCode ?? 0) >= 400 && reason.isNotEmpty) {
        setState(() => _error = reason);
      } else if (statusCode == 302) {
        // 地址没问题，是视频本身播放器解不了
        setState(() => _error = '播放器解不了这个视频，可以换系统播放器试试');
      }
    } on DioException {
      // 连服务器都连不上，保留默认提示
    }
  }

  /// 上报进度。失败不打扰观看，下一次定时上报会补上。
  Future<void> _report({
    bool finished = false,
    String event = 'progress',
  }) async {
    final duration = _player.state.duration;
    final position = finished ? duration : _player.state.position;
    if (duration <= Duration.zero ||
        (event == 'progress' && position == _reported)) {
      return;
    }
    _reported = position;
    try {
      await saveProgress(
        _dio,
        widget.args.playableId,
        position: position,
        duration: duration,
        event: finished ? 'ended' : event,
      );
    } on DioException {
      _reported = null;
    }
  }

  /// 暂停并存好进度，选一台电视；选了就带着它退出，从刚存的进度接着在电视上放。
  Future<void> _castToTv() async {
    _hideTimer?.cancel();
    await _player.pause();
    await _report().timeout(const Duration(seconds: 3), onTimeout: () {});
    if (!mounted) return;
    final device = await showCastDevicePicker(context);
    if (!mounted) return;
    if (device == null) {
      _scheduleHide();
      return;
    }
    _exiting = true;
    Navigator.of(context).pop(device);
  }

  /// 存好进度再退出，回到详情页时进度和「已看」已经是新的。
  Future<void> _exit({bool finished = false}) async {
    if (_exiting) return;
    _exiting = true;
    await _player.pause();
    await _report(finished: finished)
        .timeout(const Duration(seconds: 3), onTimeout: () {});
    if (finished && widget.args.playlistId != null && mounted) {
      try {
        final versionId = await nextPlaylistVersionId(
          _dio,
          widget.args.playlistId!,
          widget.args.playableId,
        );
        if (versionId != null && mounted) {
          final ticket = await issuePlay(
            _dio,
            PlayTarget.version(versionId),
            ref.read(playPlatformProvider),
            builtin: true,
          );
          if (!mounted) return;
          Navigator.of(context).pushReplacement(
            MaterialPageRoute<void>(
              builder: (_) => PlayerPage(
                args: PlayerArgs.fromTicket(
                  ticket,
                  _dio.options.baseUrl,
                  playlistId: widget.args.playlistId,
                ),
              ),
            ),
          );
          return;
        }
      } on DioException {
        // 连播失败时回到列表，当前项已经正常记为已看。
      }
    }
    if (mounted) Navigator.of(context).pop();
  }

  // ---- 控制条 ----

  void _showControls({bool focusPlay = false}) {
    setState(() => _controls = true);
    if (focusPlay) _playFocus.requestFocus();
    _scheduleHide();
  }

  void _hideControls() {
    _hideTimer?.cancel();
    setState(() => _controls = false);
    _rootFocus.requestFocus();
  }

  void _scheduleHide() {
    _hideTimer?.cancel();
    _hideTimer = Timer(const Duration(seconds: 4), () {
      if (mounted && _playing && _scrub == null) _hideControls();
    });
  }

  void _showToast(String text) {
    _toastTimer?.cancel();
    setState(() => _toast = text);
    _toastTimer = Timer(const Duration(milliseconds: 1200), () {
      if (mounted) setState(() => _toast = null);
    });
  }

  Future<void> _togglePlay() => _player.playOrPause();

  Future<void> _seekBy(Duration delta) async {
    final target = _clamp(_position + delta);
    final seconds = delta.inSeconds;
    _showToast(
      '${seconds > 0 ? '+' : '−'}${seconds.abs()} 秒 · ${formatClock(target)}',
    );
    await _player.seek(target);
  }

  /// 遥控器在进度条上连按左右键：先累计目标位置，停手后一次性跳转，
  /// 免得每按一下都向网络流发一次 seek
  void _scrubBy(Duration delta) {
    _hideTimer?.cancel();
    _scrubTimer?.cancel();
    setState(() => _scrub = _clamp((_scrub ?? _position) + delta));
    _scrubTimer = Timer(const Duration(milliseconds: 600), () async {
      await _onDragEnd();
      if (mounted) _scheduleHide();
    });
  }

  Duration _clamp(Duration d) {
    if (d < Duration.zero) return Duration.zero;
    if (_duration > Duration.zero && d > _duration) return _duration;
    return d;
  }

  Future<void> _setRate(double rate) async {
    setState(() => _rate = rate);
    await _player.setRate(rate);
  }

  KeyEventResult _onKey(FocusNode node, KeyEvent event) {
    if (event is KeyUpEvent) return KeyEventResult.ignored;
    final key = event.logicalKey;
    if (key == LogicalKeyboardKey.mediaPlayPause) {
      unawaited(_togglePlay());
      return KeyEventResult.handled;
    }
    if (key == LogicalKeyboardKey.mediaFastForward) {
      unawaited(_seekBy(seekForwardStep));
      return KeyEventResult.handled;
    }
    if (key == LogicalKeyboardKey.mediaRewind) {
      unawaited(_seekBy(-seekBackStep));
      return KeyEventResult.handled;
    }
    // 控制条显示时方向键和确认键交给按钮做焦点移动和点按
    if (_controls) {
      _scheduleHide();
      return KeyEventResult.ignored;
    }
    if (key == LogicalKeyboardKey.arrowLeft) {
      unawaited(_seekBy(-seekBackStep));
    } else if (key == LogicalKeyboardKey.arrowRight) {
      unawaited(_seekBy(seekForwardStep));
    } else if (key == LogicalKeyboardKey.select ||
        key == LogicalKeyboardKey.enter ||
        key == LogicalKeyboardKey.space) {
      if (event is KeyDownEvent) {
        unawaited(_togglePlay());
        _showControls(focusPlay: true);
      }
    } else if (key == LogicalKeyboardKey.arrowUp ||
        key == LogicalKeyboardKey.arrowDown) {
      _showControls(focusPlay: true);
    } else {
      return KeyEventResult.ignored;
    }
    return KeyEventResult.handled;
  }

  // ---- 手势 ----

  Offset? _doubleTapAt;
  Duration _dragFrom = Duration.zero;
  double _dragDx = 0;

  /// 双击：左侧后退、右侧前进、中间暂停，秒数和中央的按钮一致
  void _onDoubleTap(double width) {
    final seek = doubleTapSeek(_doubleTapAt?.dx ?? width / 2, width);
    unawaited(seek == null ? _togglePlay() : _seekBy(seek));
  }

  void _onDragStart(DragStartDetails _) {
    if (_duration <= Duration.zero) return;
    _dragFrom = _position;
    _dragDx = 0;
    setState(() => _scrub = _position);
  }

  /// 划过整个屏幕宽度约等于 2 分钟
  void _onDragUpdate(DragUpdateDetails details, double width) {
    if (_scrub == null) return;
    _dragDx += details.delta.dx;
    final delta = Duration(milliseconds: (_dragDx / width * 120000).round());
    setState(() => _scrub = _clamp(_dragFrom + delta));
  }

  Future<void> _onDragEnd() async {
    final target = _scrub;
    if (target == null) return;
    setState(() => _scrub = null);
    await _player.seek(target);
  }

  void _onLongPressStart() {
    if (!_playing) return;
    setState(() => _boost = true);
    unawaited(_player.setRate(2));
  }

  void _onLongPressEnd() {
    if (!_boost) return;
    setState(() => _boost = false);
    unawaited(_player.setRate(_rate));
  }

  // ---- 弹层 ----

  /// 齿轮面板：音频、字幕、播放速度、画面都在这里，选中立即生效。
  Future<void> _openSettings() async {
    _hideTimer?.cancel();
    final tracks = _player.state.tracks;
    final current = _player.state.track;
    final loaded = tracks.subtitle.where(_isRealTrack).toList();
    final downloaded = _downloadedSubtitles
        .where((t) => !loaded.any((track) => track.id == t.id))
        .toList();
    await showPlayerSettings(
      context,
      audios: tracks.audio.where(_isRealTrack).toList(),
      audioId: current.audio.id,
      subtitles: [...loaded, ...downloaded],
      subtitleId: current.subtitle.id,
      rate: _rate,
      fill: _fill,
      canSearchSubtitles: _canSearchSubtitles,
      onAudio: (track) => unawaited(_player.setAudioTrack(track)),
      onSubtitle: (track) =>
          unawaited(_player.setSubtitleTrack(track ?? SubtitleTrack.no())),
      onRate: (rate) => unawaited(_setRate(rate)),
      onFill: (fill) => unawaited(_setFill(fill)),
      onSearchSubtitles: _searchSubtitles,
    );
    if (mounted) _scheduleHide();
  }

  /// 播放中搜索并下载在线字幕。拿到带签名的 path 后直接用当前服务器地址
  /// 交给 mpv，addSubtitle/setSubtitleTrack 不会重新打开文件，播放不中断。
  Future<void> _searchSubtitles() async {
    final versionId = widget.args.versionId;
    if (versionId == null) return;
    final result = await showSubtitleSearchSheet(
      context,
      versionId: versionId,
      title: widget.args.title,
    );
    if (result == null || !mounted) return;
    final path = result.playPath;
    if (path == null || path.isEmpty) {
      _showToast('字幕下载好了，但没有拿到播放地址');
      return;
    }
    final track = SubtitleTrack.uri(
      '${ref.read(apiBaseUrlProvider)}$path',
      title: result.subtitle.label,
      language: result.subtitle.language,
    );
    setState(() {
      _downloadedSubtitles
        ..removeWhere((t) => t.id == track.id)
        ..add(track);
    });
    await _player.setSubtitleTrack(track);
    if (mounted) _showToast('已切换到「${result.subtitle.label}」');
  }

  @override
  Widget build(BuildContext context) {
    // 提前取下载器状态，用户打开设置面板时「搜索字幕」能直接出现
    final providers =
        ref.watch(authControllerProvider).value?.canDownloadSubtitles == true
        ? ref.watch(subtitleProvidersProvider(widget.args.versionId)).value
        : null;
    _canSearchSubtitles =
        widget.args.versionId != null && (providers?.usable ?? false);
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (didPop) return;
        // 电视上返回键先收起控制层，再按一次才退出
        if (_tv && _controls) {
          _hideControls();
        } else {
          unawaited(_exit());
        }
      },
      child: Scaffold(
        backgroundColor: Colors.black,
        body: Focus(
          focusNode: _rootFocus,
          autofocus: true,
          onKeyEvent: _onKey,
          child: LayoutBuilder(
            builder: (context, constraints) {
              final width = constraints.maxWidth;
              return Stack(
                fit: StackFit.expand,
                children: [
                  Video(
                    controller: _video,
                    controls: NoVideoControls,
                    wakelock: true,
                    fit: _fill ? BoxFit.cover : BoxFit.contain,
                    subtitleViewConfiguration: _subtitleStyle(
                      constraints.maxHeight,
                    ),
                  ),
                  GestureDetector(
                    behavior: HitTestBehavior.opaque,
                    onTap: () => _controls ? _hideControls() : _showControls(),
                    onDoubleTapDown: (d) => _doubleTapAt = d.localPosition,
                    onDoubleTap: () => _onDoubleTap(width),
                    onLongPressStart: (_) => _onLongPressStart(),
                    onLongPressEnd: (_) => _onLongPressEnd(),
                    onHorizontalDragStart: _onDragStart,
                    onHorizontalDragUpdate: (d) => _onDragUpdate(d, width),
                    onHorizontalDragEnd: (_) => _onDragEnd(),
                    onHorizontalDragCancel: _onDragEnd,
                  ),
                  // 控制层显示时缓冲的小圈在播放按钮里，收起后单独放在屏幕中间
                  if (_buffering && !_controls && _error == null)
                    const Center(
                      child: CircularProgressIndicator(
                        color: CinemaColors.amber,
                      ),
                    ),
                  _centerLabel(),
                  _controlsLayer(),
                  if (_error case final error?) _errorLayer(error),
                ],
              );
            },
          ),
        ),
      ),
    );
  }

  /// 画面「完整 / 填满」，选择记在本机
  Future<void> _setFill(bool fill) async {
    if (fill == _fill) return;
    setState(() => _fill = fill);
    _showToast(fill ? '画面填满屏幕' : '显示完整画面');
    _scheduleHide();
    await ref.read(playerPrefsProvider).writeFill(fill);
  }

  /// 字幕按屏幕高度定字号（约 5.5%），白字黑描边、不加底色块，贴在屏幕下方。
  /// 播放器默认按 1080p 缩放，手机上会小到十几像素。
  SubtitleViewConfiguration _subtitleStyle(double height) {
    const base = 32.0;
    const outline = Colors.black87;
    return SubtitleViewConfiguration(
      style: const TextStyle(
        height: 1.35,
        fontSize: base,
        color: Colors.white,
        fontWeight: FontWeight.w500,
        shadows: [
          Shadow(color: outline, blurRadius: 3),
          Shadow(color: outline, offset: Offset(1, 1), blurRadius: 1),
          Shadow(color: outline, offset: Offset(-1, -1), blurRadius: 1),
        ],
      ),
      textScaler: TextScaler.linear((height * .055).clamp(18.0, 48.0) / base),
      padding: const EdgeInsets.fromLTRB(48, 0, 48, 28),
    );
  }

  /// 屏幕中间的临时提示：滑动拖进度、2 倍速、快进快退
  Widget _centerLabel() {
    final scrub = _scrub;
    final String? text;
    if (scrub != null && !_controls) {
      text = '${formatClock(scrub)} / ${formatClock(_duration)}';
    } else if (_boost) {
      text = '2× 快进中';
    } else {
      text = _toast;
    }
    return IgnorePointer(
      child: Align(
        alignment: const Alignment(0, -0.55),
        child: AnimatedOpacity(
          opacity: text == null ? 0 : 1,
          duration: Motion.short,
          child: text == null
              ? const SizedBox.shrink()
              : DecoratedBox(
                  decoration: BoxDecoration(
                    color: CinemaColors.scrim,
                    borderRadius: BorderRadius.circular(CinemaRadius.pill),
                  ),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 16,
                      vertical: 8,
                    ),
                    child: Text(
                      text,
                      style: Theme.of(context).textTheme.titleSmall!.copyWith(
                        color: CinemaColors.text0,
                        fontFeatures: const [FontFeature.tabularFigures()],
                      ),
                    ),
                  ),
                ),
        ),
      ),
    );
  }

  Widget _controlsLayer() {
    return PlayerControls(
      tv: _tv,
      visible: _controls,
      buffering: _buffering && _error == null,
      title: widget.args.displayTitle,
      position: _scrub ?? _position,
      duration: _duration,
      playing: _playing,
      castable: widget.args.castable,
      playFocus: _playFocus,
      seekFocus: _seekFocus,
      onClose: () => unawaited(_exit()),
      onCast: () => unawaited(_castToTv()),
      onTogglePlay: () => unawaited(_togglePlay()),
      onSeekBy: (delta) => unawaited(_seekBy(delta)),
      onScrubBy: _scrubBy,
      onOpenSettings: () => unawaited(_openSettings()),
      onScrubStart: () => _hideTimer?.cancel(),
      onScrub: (position) => setState(() => _scrub = position),
      onScrubEnd: () {
        unawaited(_onDragEnd());
        _scheduleHide();
      },
    );
  }

  Widget _errorLayer(String error) {
    final textTheme = Theme.of(context).textTheme;
    return ColoredBox(
      color: Colors.black,
      child: SafeArea(
        child: Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(
                  Icons.error_outline,
                  size: 40,
                  color: CinemaColors.error,
                ),
                const SizedBox(height: 12),
                Text(
                  '没能播放',
                  style: textTheme.titleMedium!.copyWith(
                    color: CinemaColors.text0,
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  error,
                  textAlign: TextAlign.center,
                  maxLines: 3,
                  overflow: TextOverflow.ellipsis,
                  style: textTheme.bodySmall,
                ),
                const SizedBox(height: 20),
                Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    TextButton(onPressed: _exit, child: const Text('返回')),
                    const SizedBox(width: 12),
                    FilledButton(
                      autofocus: true,
                      onPressed: () => _open(_position),
                      child: const Text('重试'),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
