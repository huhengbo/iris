part of 'player_page.dart';

/// 快退 / 快进按钮、双击和遥控器左右键的步长，几处必须一致（DESIGN.md「内置播放器」）。
const seekBackStep = Duration(seconds: 10);
const seekForwardStep = Duration(seconds: 30);

/// 播放速度选项，设置面板按这个顺序列
const _rates = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0];

/// 双击位置决定这次跳多少：左侧后退、右侧前进，中间返回 null（切换播放暂停）。
/// 秒数和中央的两个按钮一致。
Duration? doubleTapSeek(double x, double width) {
  if (x < width / 3) return -seekBackStep;
  if (x > width * 2 / 3) return seekForwardStep;
  return null;
}

/// 播放器控制层：顶部是关闭、片名、投屏和设置，中央是后退 10 秒 / 播放暂停 /
/// 前进 30 秒，底部是进度条和时间。触屏和遥控器共用这一套布局，[tv] 只调尺寸、
/// 焦点样式和进度条的操作方式（遥控器左右键跳，触屏直接拖）。
class PlayerControls extends StatelessWidget {
  const PlayerControls({
    super.key,
    required this.tv,
    required this.visible,
    required this.buffering,
    required this.title,
    required this.position,
    required this.duration,
    required this.playing,
    required this.castable,
    required this.playFocus,
    required this.seekFocus,
    required this.onClose,
    required this.onCast,
    required this.onTogglePlay,
    required this.onSeekBy,
    required this.onScrubBy,
    required this.onOpenSettings,
    required this.onScrubStart,
    required this.onScrub,
    required this.onScrubEnd,
  });

  final bool tv;

  /// 控制层是否显示；隐藏时整层不接收点按
  final bool visible;

  /// 缓冲中：播放按钮的位置转圈
  final bool buffering;
  final String title;
  final Duration position;
  final Duration duration;
  final bool playing;

  /// 当前平台能把播放地址交给电视时才显示投屏按钮
  final bool castable;
  final FocusNode playFocus;
  final FocusNode seekFocus;
  final VoidCallback onClose;
  final VoidCallback onCast;
  final VoidCallback onTogglePlay;

  /// 后退 / 前进按钮立刻跳转
  final ValueChanged<Duration> onSeekBy;

  /// 遥控器在进度条上连按左右键：播放器先累计目标位置，停手后再一次跳
  final ValueChanged<Duration> onScrubBy;
  final VoidCallback onOpenSettings;
  final VoidCallback onScrubStart;
  final ValueChanged<Duration> onScrub;
  final VoidCallback onScrubEnd;

  @override
  Widget build(BuildContext context) {
    return IgnorePointer(
      ignoring: !visible,
      child: AnimatedOpacity(
        opacity: visible ? 1 : 0,
        duration: Motion.short,
        child: Stack(
          fit: StackFit.expand,
          children: [
            Align(
              alignment: Alignment.topCenter,
              child: _Scrim(
                top: true,
                tv: tv,
                child: _TopBar(
                  tv: tv,
                  title: title,
                  castable: castable,
                  onClose: onClose,
                  onCast: onCast,
                  onOpenSettings: onOpenSettings,
                ),
              ),
            ),
            Center(
              child: _CenterButtons(
                tv: tv,
                playing: playing,
                buffering: buffering,
                playFocus: playFocus,
                onTogglePlay: onTogglePlay,
                onSeekBy: onSeekBy,
              ),
            ),
            Align(
              alignment: Alignment.bottomCenter,
              child: _Scrim(
                top: false,
                tv: tv,
                child: _SeekRow(
                  tv: tv,
                  position: position,
                  duration: duration,
                  seekFocus: seekFocus,
                  onScrubBy: onScrubBy,
                  onScrubStart: onScrubStart,
                  onScrub: onScrub,
                  onScrubEnd: onScrubEnd,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// 顶栏：关闭（返回）、片名、投屏、设置。剧集的片名由 [PlayerArgs.displayTitle]
/// 写成「剧名 · 第 1 季 第 2 集」。
class _TopBar extends StatelessWidget {
  const _TopBar({
    required this.tv,
    required this.title,
    required this.castable,
    required this.onClose,
    required this.onCast,
    required this.onOpenSettings,
  });

  final bool tv;
  final String title;
  final bool castable;
  final VoidCallback onClose;
  final VoidCallback onCast;
  final VoidCallback onOpenSettings;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    return Row(
      children: [
        IconButton(
          tooltip: '关闭',
          iconSize: tv ? 32 : 24,
          onPressed: onClose,
          icon: const Icon(Icons.close_rounded),
        ),
        const SizedBox(width: 4),
        Expanded(
          child: Text(
            title,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: (tv ? textTheme.headlineSmall : textTheme.titleMedium)!
                .copyWith(color: CinemaColors.text0),
          ),
        ),
        if (castable)
          IconButton(
            tooltip: '投屏到电视',
            iconSize: tv ? 32 : 24,
            onPressed: onCast,
            icon: const Icon(Icons.cast_rounded),
          ),
        IconButton(
          tooltip: '播放设置',
          iconSize: tv ? 32 : 24,
          onPressed: onOpenSettings,
          icon: const Icon(Icons.settings_rounded),
        ),
      ],
    );
  }
}

class _CenterButtons extends StatelessWidget {
  const _CenterButtons({
    required this.tv,
    required this.playing,
    required this.buffering,
    required this.playFocus,
    required this.onTogglePlay,
    required this.onSeekBy,
  });

  final bool tv;
  final bool playing;
  final bool buffering;
  final FocusNode playFocus;
  final VoidCallback onTogglePlay;
  final ValueChanged<Duration> onSeekBy;

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        _RoundButton(
          tv: tv,
          icon: Icons.replay_10_rounded,
          tooltip: '后退 10 秒',
          onPressed: () => onSeekBy(-seekBackStep),
        ),
        SizedBox(width: tv ? 20 : 16),
        _RoundButton(
          tv: tv,
          primary: true,
          focusNode: playFocus,
          loading: buffering,
          icon: playing ? Icons.pause_rounded : Icons.play_arrow_rounded,
          tooltip: playing ? '暂停' : '播放',
          onPressed: onTogglePlay,
        ),
        SizedBox(width: tv ? 20 : 16),
        _RoundButton(
          tv: tv,
          icon: Icons.forward_30_rounded,
          tooltip: '前进 30 秒',
          onPressed: () => onSeekBy(seekForwardStep),
        ),
      ],
    );
  }
}

/// 中央的圆按钮。遥控器聚焦时整颗变琥珀并略放大，隔着客厅也看得出焦点在哪；
/// 触屏点按不留焦点样式。[primary] 是播放暂停，比两侧大一圈。
class _RoundButton extends StatefulWidget {
  const _RoundButton({
    required this.tv,
    required this.icon,
    required this.tooltip,
    required this.onPressed,
    this.focusNode,
    this.primary = false,
    this.loading = false,
  });

  final bool tv;
  final IconData icon;
  final String tooltip;
  final VoidCallback onPressed;
  final FocusNode? focusNode;
  final bool primary;

  /// 缓冲中，用同尺寸的小圈代替图标
  final bool loading;

  @override
  State<_RoundButton> createState() => _RoundButtonState();
}

class _RoundButtonState extends State<_RoundButton> {
  bool _focused = false;

  @override
  Widget build(BuildContext context) {
    final size = widget.primary
        ? (widget.tv ? 84.0 : 68.0)
        : (widget.tv ? 64.0 : 52.0);
    final foreground = _focused ? CinemaColors.amberInk : CinemaColors.text0;
    return FocusableActionDetector(
      focusNode: widget.focusNode,
      mouseCursor: SystemMouseCursors.click,
      onShowFocusHighlight: (v) => setState(() => _focused = v),
      actions: {
        ActivateIntent: CallbackAction<ActivateIntent>(
          onInvoke: (_) {
            widget.onPressed();
            return null;
          },
        ),
        // Web 上回车发的是 ButtonActivateIntent，空格和 TV 的确定键才是 ActivateIntent
        ButtonActivateIntent: CallbackAction<ButtonActivateIntent>(
          onInvoke: (_) {
            widget.onPressed();
            return null;
          },
        ),
      },
      child: Semantics(
        button: true,
        label: widget.tooltip,
        excludeSemantics: true,
        child: Tooltip(
          message: widget.tooltip,
          child: GestureDetector(
            // 圆按钮整颗都接收点按，只按在图标附近会漏到下面的画面手势
            behavior: HitTestBehavior.opaque,
            onTap: widget.onPressed,
            child: AnimatedScale(
              scale: _focused ? 1.06 : 1,
              duration: Motion.short,
              child: AnimatedContainer(
                duration: Motion.short,
                width: size,
                height: size,
                alignment: Alignment.center,
                decoration: BoxDecoration(
                  color: _focused ? CinemaColors.amber : CinemaColors.scrim,
                  shape: BoxShape.circle,
                ),
                child: widget.loading
                    ? SizedBox.square(
                        dimension: widget.primary ? 24 : 20,
                        child: CircularProgressIndicator(
                          strokeWidth: 2.4,
                          color: foreground,
                        ),
                      )
                    : Icon(
                        widget.icon,
                        size: widget.primary ? (widget.tv ? 44 : 40) : 30,
                        color: foreground,
                      ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// 进度条和时间。触屏是可拖的滑块；遥控器上是可聚焦的 [_TvSeekBar]。
class _SeekRow extends StatelessWidget {
  const _SeekRow({
    required this.tv,
    required this.position,
    required this.duration,
    required this.seekFocus,
    required this.onScrubBy,
    required this.onScrubStart,
    required this.onScrub,
    required this.onScrubEnd,
  });

  final bool tv;
  final Duration position;
  final Duration duration;
  final FocusNode seekFocus;
  final ValueChanged<Duration> onScrubBy;
  final VoidCallback onScrubStart;
  final ValueChanged<Duration> onScrub;
  final VoidCallback onScrubEnd;

  @override
  Widget build(BuildContext context) {
    final max = duration.inMilliseconds.toDouble();
    return Row(
      children: [
        _TimeText(formatClock(position), tv: tv),
        Expanded(
          child: tv
              // 遥控器上下键移到进度条，左右键快退快进
              ? _TvSeekBar(
                  focusNode: seekFocus,
                  position: position,
                  duration: duration,
                  onSeekBy: onScrubBy,
                )
              // 触屏直接拖；遥控器用左右键快进快退，滑块不参与焦点移动。
              // 固定高度：Stack 里的 Align 给的是松约束，不固定的话滑块会撑满整屏
              : SizedBox(
                  height: 48,
                  child: ExcludeFocus(
                    child: SliderTheme(
                      data: SliderTheme.of(context).copyWith(
                        trackHeight: 3,
                        activeTrackColor: CinemaColors.amber,
                        inactiveTrackColor: CinemaColors.track,
                        thumbColor: CinemaColors.amber,
                        overlayColor: CinemaColors.amberSoft,
                        thumbShape: const RoundSliderThumbShape(
                          enabledThumbRadius: 7,
                        ),
                      ),
                      child: Slider(
                        max: max > 0 ? max : 1,
                        value: max > 0
                            ? position.inMilliseconds.clamp(0, max).toDouble()
                            : 0,
                        onChangeStart: max > 0 ? (_) => onScrubStart() : null,
                        onChanged: max > 0
                            ? (v) => onScrub(Duration(milliseconds: v.round()))
                            : null,
                        onChangeEnd: max > 0 ? (_) => onScrubEnd() : null,
                      ),
                    ),
                  ),
                ),
        ),
        _TimeText(formatClock(duration), tv: tv),
      ],
    );
  }
}

/// 播放设置面板：音频、字幕、播放速度、画面合在一处（原来是底栏的三个独立按钮）。
/// 选中立即生效、面板不关，可以连着调几项；「搜索字幕」先关掉面板，交给播放器
/// 打开搜索弹层，下载好直接切到新字幕。
Future<void> showPlayerSettings(
  BuildContext context, {
  required List<AudioTrack> audios,
  required String? audioId,
  required List<SubtitleTrack> subtitles,
  required String? subtitleId,
  required double rate,
  required bool fill,
  required bool canSearchSubtitles,
  required ValueChanged<AudioTrack> onAudio,
  required ValueChanged<SubtitleTrack?> onSubtitle,
  required ValueChanged<double> onRate,
  required ValueChanged<bool> onFill,
  required Future<void> Function() onSearchSubtitles,
}) => showDialog<void>(
  context: context,
  builder: (context) => Dialog(
    child: ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 420),
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 12),
        child: _PlayerSettings(
          audios: audios,
          audioId: audioId,
          subtitles: subtitles,
          subtitleId: subtitleId,
          rate: rate,
          fill: fill,
          canSearchSubtitles: canSearchSubtitles,
          onAudio: onAudio,
          onSubtitle: onSubtitle,
          onRate: onRate,
          onFill: onFill,
          onSearchSubtitles: onSearchSubtitles,
        ),
      ),
    ),
  ),
);

class _PlayerSettings extends StatefulWidget {
  const _PlayerSettings({
    required this.audios,
    required this.audioId,
    required this.subtitles,
    required this.subtitleId,
    required this.rate,
    required this.fill,
    required this.canSearchSubtitles,
    required this.onAudio,
    required this.onSubtitle,
    required this.onRate,
    required this.onFill,
    required this.onSearchSubtitles,
  });

  final List<AudioTrack> audios;
  final String? audioId;
  final List<SubtitleTrack> subtitles;
  final String? subtitleId;
  final double rate;
  final bool fill;
  final bool canSearchSubtitles;
  final ValueChanged<AudioTrack> onAudio;
  final ValueChanged<SubtitleTrack?> onSubtitle;
  final ValueChanged<double> onRate;
  final ValueChanged<bool> onFill;
  final Future<void> Function() onSearchSubtitles;

  @override
  State<_PlayerSettings> createState() => _PlayerSettingsState();
}

class _PlayerSettingsState extends State<_PlayerSettings> {
  // 面板不关就要自己记选中项，否则点了没反应
  late String? _audioId = widget.audioId;
  late String? _subtitleId = widget.subtitleId;
  late double _rate = widget.rate;
  late bool _fill = widget.fill;

  /// 搜索要关掉面板再开搜索弹层，不叠两层对话框
  Future<void> _search() async {
    Navigator.of(context).pop();
    await widget.onSearchSubtitles();
  }

  @override
  Widget build(BuildContext context) {
    final subtitleSelected = widget.subtitles.any((t) => t.id == _subtitleId);
    // 默认焦点：当前项；字幕是「自动」时没有选中项，落在「关闭」上
    var focusTaken = false;
    bool takeFocus(bool preferred) {
      if (focusTaken || !preferred) return false;
      focusTaken = true;
      return true;
    }

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const PickerHeader(title: '播放设置'),
        if (widget.audios.isNotEmpty) ...[
          const _SectionLabel('音频'),
          for (final (i, track) in widget.audios.indexed)
            _ScrollIntoView(
              child: PickerOption(
                title: _trackLabel(track.title, track.language, '音轨', i),
                selected: _audioId == track.id,
                autofocus: takeFocus(_audioId == track.id),
                onTap: () {
                  widget.onAudio(track);
                  setState(() => _audioId = track.id);
                },
              ),
            ),
        ],
        const _SectionLabel('字幕'),
        _ScrollIntoView(
          child: PickerOption(
            title: '关闭',
            selected: _subtitleId == 'no',
            autofocus: takeFocus(!subtitleSelected),
            onTap: () {
              widget.onSubtitle(null);
              setState(() => _subtitleId = 'no');
            },
          ),
        ),
        for (final (i, track) in widget.subtitles.indexed)
          _ScrollIntoView(
            child: PickerOption(
              title: _trackLabel(track.title, track.language, '字幕', i),
              selected: _subtitleId == track.id,
              autofocus: takeFocus(_subtitleId == track.id),
              onTap: () {
                widget.onSubtitle(track);
                setState(() => _subtitleId = track.id);
              },
            ),
          ),
        if (widget.canSearchSubtitles)
          _ScrollIntoView(
            child: PickerOption(
              icon: Icons.travel_explore,
              title: '搜索字幕',
              subtitle: '从在线字幕下载器搜索，下载后立即切换',
              onTap: () => unawaited(_search()),
            ),
          ),
        const _SectionLabel('播放速度'),
        for (final rate in _rates)
          _ScrollIntoView(
            child: PickerOption(
              title: rate == 1 ? '正常' : '${_rateLabel(rate)}×',
              selected: _rate == rate,
              autofocus: takeFocus(_rate == rate),
              onTap: () {
                widget.onRate(rate);
                setState(() => _rate = rate);
              },
            ),
          ),
        const _SectionLabel('画面'),
        _ScrollIntoView(
          child: PickerOption(
            title: '完整显示',
            subtitle: '比例不变，整个画面都在',
            selected: !_fill,
            autofocus: takeFocus(!_fill),
            onTap: () {
              widget.onFill(false);
              setState(() => _fill = false);
            },
          ),
        ),
        _ScrollIntoView(
          child: PickerOption(
            title: '填满屏幕',
            subtitle: '等比放大铺满屏幕，裁掉多出的边',
            selected: _fill,
            autofocus: takeFocus(_fill),
            onTap: () {
              widget.onFill(true);
              setState(() => _fill = true);
            },
          ),
        ),
      ],
    );
  }
}

/// 遥控器把焦点移进面板深处时，把这一行滚进视野：TV 的过扫描加放大字号，
/// 面板会比一屏高。
class _ScrollIntoView extends StatelessWidget {
  const _ScrollIntoView({required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return Focus(
      canRequestFocus: false,
      skipTraversal: true,
      onFocusChange: (focused) {
        if (!focused) return;
        Scrollable.ensureVisible(
          context,
          alignment: .5,
          duration: Motion.short,
          curve: Motion.emphasized,
        );
      },
      child: child,
    );
  }
}
