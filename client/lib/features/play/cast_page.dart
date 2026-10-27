import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/motion.dart';
import '../../app/theme.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/picker_sheet.dart';
import '../../core/widgets/snack.dart';
import '../../core/format.dart';
import '../../core/providers.dart';
import 'cast.dart';
import 'dlna.dart';
import 'play_api.dart';

part 'cast_picker.dart';
part 'cast_widgets.dart';

// ---- 投屏控制 ----

const _seekStep = Duration(seconds: 10);

/// 电视推上地址后这么久还没开始播放，多半是放不了这个格式
const _startTimeout = Duration(seconds: 40);

/// 连续这么多次查询失败，提示和电视断开了
const _lostAfter = 5;

enum _Phase { loading, playing, ended, failed }

/// 投屏控制页：电视放，手机当遥控器。每秒查一次电视的状态，
/// 播放中每 15 秒上报进度，和内置播放器一样记续播和已看。
class CastPage extends ConsumerStatefulWidget {
  const CastPage({super.key, required this.args});

  final CastArgs args;

  @override
  ConsumerState<CastPage> createState() => _CastPageState();
}

class _CastPageState extends ConsumerState<CastPage>
    with WidgetsBindingObserver {
  late final CastRenderer _renderer;
  late final Dio _dio;

  _Phase _phase = _Phase.loading;
  String? _error;
  bool _playing = false;
  Duration _position = Duration.zero;
  Duration? _duration;

  /// 拖动进度条时的目标位置
  Duration? _scrub;

  /// 刚拖动过，电视还没跳过去，这段时间里查到的旧位置不采用
  Timer? _settleTimer;

  /// 已经开始播放过：之后再查到停止，才算电视那边停了
  bool _started = false;

  /// 开始播放后跳到这里：先是上次看到的位置，电视中途停了再投时是停下的位置
  late Duration? _resumeAt = widget.args.start;
  bool _resumed = false;
  int _failures = 0;
  bool _polling = false;
  Timer? _pollTimer;
  Timer? _reportTimer;
  Timer? _startTimer;
  Duration? _reported;

  /// 正在停止和存进度，期间再点返回或结束都不重复执行
  bool _exiting = false;

  @override
  void initState() {
    super.initState();
    _renderer = ref.read(castRendererProvider)(widget.args.device);
    _dio = ref.read(apiClientProvider);
    WidgetsBinding.instance.addObserver(this);
    _reportTimer = Timer.periodic(const Duration(seconds: 15), (_) {
      if (_playing) unawaited(_report());
    });
    unawaited(_load());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _pollTimer?.cancel();
    _reportTimer?.cancel();
    _startTimer?.cancel();
    _settleTimer?.cancel();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused) unawaited(_report());
  }

  Future<void> _load() async {
    _pollTimer?.cancel();
    _startTimer?.cancel();
    setState(() {
      _phase = _Phase.loading;
      _error = null;
      _started = false;
      _resumed = false;
      _failures = 0;
    });
    try {
      await _renderer.load(widget.args.url, widget.args.title);
      // 电视加载期间已经退出（停止指令已发出），不能再让它开始放
      if (_exiting || !mounted) return;
      try {
        await _renderer.play();
      } on CastException catch (e) {
        // 地址已经推上去了。有的电视收到播放指令就开始放，却不应答直接断开连接，
        // 这时交给后面的状态查询判断有没有开始播放，没有的话超时再报
        if (e.fault) rethrow;
        debugPrint('cast: play not answered, waiting for the TV: ${e.message}');
      }
    } on CastException catch (e) {
      if (mounted) _fail(e.message);
      return;
    }
    if (!mounted) return;
    _startTimer = Timer(_startTimeout, () {
      if (!_started) _fail('电视没有开始播放，可能放不了这个视频的格式');
    });
    _pollTimer = Timer.periodic(const Duration(seconds: 1), (_) => _poll());
    unawaited(_poll());
  }

  void _fail(String message) {
    _pollTimer?.cancel();
    _startTimer?.cancel();
    setState(() {
      _phase = _Phase.failed;
      _error = message;
      _playing = false;
    });
  }

  Future<void> _poll() async {
    if (_polling) return;
    _polling = true;
    try {
      final status = await _renderer.status();
      if (!mounted) return;
      _failures = 0;
      _apply(status);
    } on CastException {
      if (mounted) setState(() => _failures++);
    } finally {
      _polling = false;
    }
  }

  void _apply(CastStatus status) {
    final active =
        status.state == CastState.playing || status.state == CastState.paused;
    if (active && !_started) {
      _started = true;
      _startTimer?.cancel();
    }
    // 播放过之后停下：片子放完了，或者有人用电视遥控器停了
    if (_started && status.state == CastState.stopped) {
      _pollTimer?.cancel();
      final duration = _duration;
      // 放完了重新投从头开始，中途停的从停下的地方接着放
      _resumeAt =
          duration != null && duration - _position > const Duration(minutes: 1)
          ? _position
          : null;
      setState(() {
        _phase = _Phase.ended;
        _playing = false;
      });
      unawaited(_report(event: 'ended'));
      return;
    }
    final settling = _settleTimer?.isActive ?? false;
    setState(() {
      if (_started) _phase = _Phase.playing;
      _playing = status.state == CastState.playing;
      if (status.duration != null) _duration = status.duration;
      // 停止后电视会把位置归零，只在播放和暂停时采用
      if (active && !settling && status.position != null) {
        _position = status.position!;
      }
    });
    if (status.state == CastState.playing) unawaited(_resume());
  }

  /// 电视读到片长后再跳到上次的位置，太早跳多半被忽略。
  Future<void> _resume() async {
    final start = _resumeAt;
    final duration = _duration;
    if (_resumed || start == null || duration == null) return;
    _resumed = true;
    if (start <= Duration.zero || start >= duration) return;
    await _seek(start);
  }

  Future<void> _seek(Duration target) async {
    final duration = _duration;
    if (target < Duration.zero) target = Duration.zero;
    if (duration != null && target > duration) target = duration;
    _settleTimer?.cancel();
    _settleTimer = Timer(const Duration(seconds: 2), () {});
    setState(() => _position = target);
    try {
      await _renderer.seek(target);
    } on CastException catch (e) {
      if (mounted) showSnack(context, e.message);
    }
  }

  Future<void> _togglePlay() async {
    final playing = !_playing;
    setState(() => _playing = playing);
    try {
      if (playing) {
        await _renderer.play();
      } else {
        await _renderer.pause();
        unawaited(_report(event: 'paused'));
      }
    } on CastException catch (e) {
      if (!mounted) return;
      setState(() => _playing = !playing);
      showSnack(context, e.message);
    }
  }

  /// 上报进度。失败不打扰，下一次定时上报会补上。
  Future<void> _report({String event = 'progress'}) async {
    final duration = _duration;
    final position = _position;
    if (duration == null ||
        !_started ||
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
        event: event,
      );
    } on DioException {
      _reported = null;
    }
  }

  /// 存好进度再退出；[stop] 时同时让电视停下。
  Future<void> _exit({required bool stop}) async {
    if (_exiting) return;
    setState(() => _exiting = true);
    _pollTimer?.cancel();
    if (stop) {
      try {
        await _renderer.stop().timeout(const Duration(seconds: 3));
      } on Object {
        // 电视已经关了或者断开了，不影响退出
      }
    }
    await _report(event: stop ? 'ended' : 'progress')
        .timeout(const Duration(seconds: 3), onTimeout: () {});
    if (mounted) Navigator.of(context).pop();
  }

  /// 返回时电视还在放：问一下是停掉还是留着它放。
  /// 还没开始放时直接让电视停下，免得人走了电视才开始放。
  Future<void> _onBack() async {
    if (_exiting) return;
    if (_phase != _Phase.playing) {
      await _exit(stop: _phase == _Phase.loading);
      return;
    }
    final stop = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('电视还在播放'),
        content: const Text('可以让电视停下；也可以留着它继续放，但之后的观看进度不会再记录。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('继续放并退出'),
          ),
          FilledButton(
            autofocus: true,
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('停止播放'),
          ),
        ],
      ),
    );
    if (stop != null && mounted) await _exit(stop: stop);
  }

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop) unawaited(_onBack());
      },
      child: Scaffold(
        appBar: AppBar(
          leading: IconButton(
            tooltip: '返回',
            onPressed: _onBack,
            icon: const Icon(Icons.arrow_back),
          ),
          title: const Text('投屏'),
        ),
        body: SafeArea(
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 560),
              child: ListView(
                padding: const EdgeInsets.fromLTRB(20, 12, 20, 28),
                children: [
                  _DeviceHeader(
                    name: widget.args.device.name,
                    status: _statusText(),
                    active: _phase == _Phase.playing,
                  ),
                  const SizedBox(height: 28),
                  Text(
                    widget.args.title,
                    textAlign: TextAlign.center,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: textTheme.titleLarge!.copyWith(
                      color: CinemaColors.text0,
                    ),
                  ),
                  const SizedBox(height: 20),
                  AnimatedSwitcher(
                    duration: Motion.short,
                    child: switch (_phase) {
                      _Phase.failed => _problem(
                        key: const ValueKey('failed'),
                        title: '没能投到电视',
                        body: _error ?? '电视没有说明原因，可以再试一次。',
                        retry: '重试',
                      ),
                      _Phase.ended => _problem(
                        key: const ValueKey('ended'),
                        title: '电视已停止播放',
                        body: '片子放完了，或者在电视上停止了播放。',
                        retry: '重新投屏',
                      ),
                      _ => _controls(key: const ValueKey('controls')),
                    },
                  ),
                  if (widget.args.hasSubtitles) ...[
                    const SizedBox(height: 28),
                    Row(
                      children: [
                        const Icon(
                          Icons.subtitles_off_outlined,
                          size: 18,
                          color: CinemaColors.text2,
                        ),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            '外挂字幕投不到电视上，电视只显示视频自带的字幕',
                            style: textTheme.bodySmall,
                          ),
                        ),
                      ],
                    ),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  String _statusText() => switch (_phase) {
    _Phase.loading => '正在连接电视…',
    _Phase.failed => '没有在播放',
    _Phase.ended => '已停止',
    _Phase.playing when _failures >= _lostAfter => '和电视断开了，正在重新连接…',
    _Phase.playing => _playing ? '正在播放' : '已暂停',
  };

  Widget _controls({required Key key}) {
    final textTheme = Theme.of(context).textTheme;
    final duration = _duration;
    final position = _scrub ?? _position;
    final max = duration?.inMilliseconds.toDouble() ?? 0;
    final ready = _phase == _Phase.playing;
    return Column(
      key: key,
      children: [
        ExcludeFocus(
          child: SliderTheme(
            data: SliderTheme.of(context).copyWith(
              trackHeight: 3,
              activeTrackColor: CinemaColors.amber,
              inactiveTrackColor: CinemaColors.track,
              thumbColor: CinemaColors.amber,
              overlayColor: CinemaColors.amberSoft,
              thumbShape: const RoundSliderThumbShape(enabledThumbRadius: 7),
            ),
            child: Slider(
              max: max > 0 ? max : 1,
              value: max > 0
                  ? position.inMilliseconds.clamp(0, max).toDouble()
                  : 0,
              onChanged: ready && max > 0
                  ? (v) => setState(
                      () => _scrub = Duration(milliseconds: v.round()),
                    )
                  : null,
              onChangeEnd: ready && max > 0
                  ? (v) {
                      setState(() => _scrub = null);
                      unawaited(_seek(Duration(milliseconds: v.round())));
                    }
                  : null,
            ),
          ),
        ),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 24),
          child: Row(
            children: [
              Text(formatClock(position), style: _timeStyle(textTheme)),
              const Spacer(),
              Text(
                duration == null ? '--:--' : formatClock(duration),
                style: _timeStyle(textTheme),
              ),
            ],
          ),
        ),
        const SizedBox(height: 20),
        Row(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            IconButton.filledTonal(
              tooltip: '快退 10 秒',
              iconSize: 26,
              style: IconButton.styleFrom(fixedSize: const Size.square(56)),
              onPressed: ready ? () => _seek(_position - _seekStep) : null,
              icon: const Icon(Icons.replay_10_rounded),
            ),
            const SizedBox(width: 24),
            SizedBox.square(
              dimension: 72,
              child: IconButton.filled(
                autofocus: true,
                tooltip: _playing ? '暂停' : '播放',
                iconSize: 36,
                style: IconButton.styleFrom(
                  backgroundColor: CinemaColors.amber,
                  foregroundColor: CinemaColors.amberInk,
                ),
                // 连接中也保持可用，琥珀底和默认焦点才在；只是点了不起作用
                onPressed: () {
                  if (ready) unawaited(_togglePlay());
                },
                icon: _phase == _Phase.loading
                    ? const SizedBox.square(
                        dimension: 26,
                        child: CircularProgressIndicator(
                          strokeWidth: 2.5,
                          color: CinemaColors.amberInk,
                        ),
                      )
                    : AnimatedSwitcher(
                        duration: Motion.short,
                        child: Icon(
                          _playing
                              ? Icons.pause_rounded
                              : Icons.play_arrow_rounded,
                          key: ValueKey(_playing),
                        ),
                      ),
              ),
            ),
            const SizedBox(width: 24),
            IconButton.filledTonal(
              tooltip: '快进 10 秒',
              iconSize: 26,
              style: IconButton.styleFrom(fixedSize: const Size.square(56)),
              onPressed: ready ? () => _seek(_position + _seekStep) : null,
              icon: const Icon(Icons.forward_10_rounded),
            ),
          ],
        ),
        const SizedBox(height: 28),
        OutlinedButton.icon(
          onPressed: _exiting ? null : () => _exit(stop: true),
          icon: const Icon(Icons.cancel_presentation_outlined),
          label: Text(_exiting ? '正在结束…' : '结束投屏'),
        ),
      ],
    );
  }

  Widget _problem({
    required Key key,
    required String title,
    required String body,
    required String retry,
  }) {
    final textTheme = Theme.of(context).textTheme;
    return Semantics(
      key: key,
      container: true,
      liveRegion: true,
      child: Column(
        children: [
          Text(
            title,
            style: textTheme.titleMedium!.copyWith(
              color: _phase == _Phase.failed
                  ? CinemaColors.error
                  : CinemaColors.text0,
            ),
          ),
          const SizedBox(height: 4),
          Text(body, textAlign: TextAlign.center, style: textTheme.bodySmall),
          const SizedBox(height: 20),
          Row(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              TextButton(
                onPressed: _exiting ? null : () => _exit(stop: false),
                child: const Text('退出'),
              ),
              const SizedBox(width: 12),
              FilledButton(
                autofocus: true,
                onPressed: _load,
                child: Text(retry),
              ),
            ],
          ),
        ],
      ),
    );
  }

  TextStyle _timeStyle(TextTheme textTheme) => textTheme.bodySmall!.copyWith(
    color: CinemaColors.text1,
    fontFeatures: const [FontFeature.tabularFigures()],
  );
}
