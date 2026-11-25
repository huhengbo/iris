part of 'player_page.dart';

/// 快退 / 快进按钮、双击和遥控器左右键的步长，几处必须一致（DESIGN.md「内置播放器」）。
/// 照爆米花前后对称，都是 10 秒。
const seekBackStep = Duration(seconds: 10);
const seekForwardStep = Duration(seconds: 10);

/// 播放速度选项，设置面板按这个顺序列
const _rates = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0];

/// 字幕字号缩放档位
const subtitleScaleOptions = <({String label, double value})>[
  (label: '小', value: 0.8),
  (label: '中', value: 1.0),
  (label: '大', value: 1.25),
];

/// 字幕底部位置档位（距离底边的逻辑像素）
const subtitleBottomPaddingOptions = <({String label, double value})>[
  (label: '偏下', value: 16.0),
  (label: '适中', value: 28.0),
  (label: '偏上', value: 52.0),
];

/// 双击位置决定这次跳多少：左侧后退、右侧前进，中间返回 null（切换播放暂停）。
/// 秒数和底栏的两个按钮一致。
Duration? doubleTapSeek(double x, double width) {
  if (x < width / 3) return -seekBackStep;
  if (x > width * 2 / 3) return seekForwardStep;
  return null;
}

/// 播放器里的时间一律写到时：「00:03:34」；还不知道片长时写「--:--:--」
String formatPlayerClock(Duration d) {
  if (d < Duration.zero) return '--:--:--';
  String two(int n) => n.toString().padLeft(2, '0');
  return '${two(d.inHours)}:${two(d.inMinutes.remainder(60))}:'
      '${two(d.inSeconds.remainder(60))}';
}

/// 播放器控制层，布局照搬网易爆米花（ADR 0032）：
/// - 顶部：返回、片名，右边投屏和齿轮；
/// - 左侧竖排旋转屏幕和画面比例，右侧锁屏（只在触屏上有）；
/// - 底部：两端时间夹着进度条，下面一行左边倍速，中间上一集 / 后退 10 秒 /
///   播放暂停 / 前进 10 秒 / 下一集，右边音轨、字幕、选集。
///
/// 触屏和遥控器共用这一套，[tv] 只调尺寸、焦点样式和进度条的操作方式。
/// 锁屏后只剩右侧的锁。
class PlayerControls extends StatelessWidget {
  const PlayerControls({
    super.key,
    required this.tv,
    required this.visible,
    required this.title,
    required this.position,
    required this.duration,
    required this.playing,
    required this.castable,
    required this.rate,
    required this.fill,
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
    required this.onRate,
    required this.onAudio,
    required this.onSubtitles,
    this.locked = false,
    this.episodic = false,
    this.buffer = Duration.zero,
    this.chapters = const [],
    this.onPrevious,
    this.onNext,
    this.onEpisodes,
    this.onRotate,
    this.onToggleFill,
    this.onToggleLock,
    this.onSkipIntro,
    this.now,
  });

  final bool tv;

  /// 控制层是否显示；隐藏时整层不接收点按
  final bool visible;
  final String title;
  final Duration position;
  final Duration duration;
  final Duration buffer;
  final List<PlayerChapter> chapters;
  final bool playing;

  /// 当前平台能把播放地址交给电视时才显示投屏按钮
  final bool castable;

  /// 当前倍速，显示在底栏左边
  final double rate;

  /// 画面填满屏幕，左侧比例按钮的图标跟着变
  final bool fill;
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
  final VoidCallback onRate;
  final VoidCallback onAudio;
  final VoidCallback onSubtitles;

  /// 锁屏：只剩右侧的锁，其他按钮和画面手势都不响应
  final bool locked;

  /// 在放剧集：底栏有上一集、下一集和选集；为空的那一侧是到头了，按钮变灰
  final bool episodic;
  final VoidCallback? onPrevious;
  final VoidCallback? onNext;
  final VoidCallback? onEpisodes;

  /// 旋转屏幕、切换画面比例、锁屏，只在触屏上有；为空就不显示
  final VoidCallback? onRotate;
  final VoidCallback? onToggleFill;
  final VoidCallback? onToggleLock;

  /// 跳过片头快捷操作
  final VoidCallback? onSkipIntro;

  /// 当前系统时间（测试注入用，默认取 DateTime.now）
  final DateTime? now;

  @override
  Widget build(BuildContext context) {
    final safe = MediaQuery.paddingOf(context);
    final lock = onToggleLock == null
        ? null
        : Align(
            alignment: Alignment.centerRight,
            child: Padding(
              padding: EdgeInsets.only(right: 16 + safe.right),
              child: _ControlButton(
                tv: tv,
                scrim: true,
                icon: locked ? Icons.lock_rounded : Icons.lock_open_rounded,
                tooltip: locked ? '解锁' : '锁屏',
                onPressed: onToggleLock,
              ),
            ),
          );
    return IgnorePointer(
      ignoring: !visible,
      child: AnimatedOpacity(
        opacity: visible ? 1 : 0,
        duration: Motion.short,
        curve: Motion.emphasized,
        child: Stack(
          fit: StackFit.expand,
          children: [
            IgnorePointer(
              ignoring: locked,
              child: AnimatedSwitcher(
                duration: Motion.short,
                switchInCurve: Motion.emphasized,
                switchOutCurve: Motion.emphasized,
                child: locked
                    ? const SizedBox.shrink(key: ValueKey('locked'))
                    : Stack(
                        key: const ValueKey('unlocked'),
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
                                position: position,
                                duration: duration,
                                rate: rate,
                                now: now,
                                castable: castable,
                                onClose: onClose,
                                onCast: onCast,
                                onOpenSettings: onOpenSettings,
                              ),
                            ),
                          ),
                          if (onRotate != null || onToggleFill != null)
                            Align(
                              alignment: Alignment.centerLeft,
                              child: Padding(
                                padding: EdgeInsets.only(left: 16 + safe.left),
                                child: Column(
                                  mainAxisSize: MainAxisSize.min,
                                  children: [
                                    if (onRotate case final rotate?)
                                      _ControlButton(
                                        tv: tv,
                                        scrim: true,
                                        icon: Icons.screen_rotation_rounded,
                                        tooltip: '旋转屏幕',
                                        onPressed: rotate,
                                      ),
                                    if (onRotate != null &&
                                        onToggleFill != null)
                                      const SizedBox(height: 32),
                                    if (onToggleFill case final toggle?)
                                      _ControlButton(
                                        tv: tv,
                                        scrim: true,
                                        icon: fill
                                            ? Icons.fullscreen_exit_rounded
                                            : Icons.fullscreen_rounded,
                                        tooltip: fill ? '显示完整画面' : '画面填满屏幕',
                                        onPressed: toggle,
                                      ),
                                  ],
                                ),
                              ),
                            ),
                          Align(
                            alignment: Alignment.bottomCenter,
                            child: _Scrim(
                              top: false,
                              tv: tv,
                              child: Column(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  if (onSkipIntro != null &&
                                      shouldShowSkipIntro(
                                        visible: visible,
                                        locked: locked,
                                        episodic: episodic,
                                        position: position,
                                        duration: duration,
                                        chapters: chapters,
                                      ))
                                    Align(
                                      alignment: Alignment.centerRight,
                                      child: Padding(
                                        padding: const EdgeInsets.only(
                                          right: 8,
                                          bottom: 6,
                                        ),
                                        child: _SkipIntroButton(
                                          tv: tv,
                                          onPressed: onSkipIntro!,
                                        ),
                                      ),
                                    ),
                                  _SeekRow(
                                    tv: tv,
                                    position: position,
                                    duration: duration,
                                    buffer: buffer,
                                    chapters: chapters,
                                    seekFocus: seekFocus,
                                    onScrubBy: onScrubBy,
                                    onScrubStart: onScrubStart,
                                    onScrub: onScrub,
                                    onScrubEnd: onScrubEnd,
                                  ),
                                  _BottomButtons(
                                    tv: tv,
                                    playing: playing,
                                    rate: rate,
                                    episodic: episodic,
                                    playFocus: playFocus,
                                    onTogglePlay: onTogglePlay,
                                    onSeekBy: onSeekBy,
                                    onRate: onRate,
                                    onAudio: onAudio,
                                    onSubtitles: onSubtitles,
                                    onPrevious: onPrevious,
                                    onNext: onNext,
                                    onEpisodes: onEpisodes,
                                  ),
                                ],
                              ),
                            ),
                          ),
                        ],
                      ),
              ),
            ),
            ?lock,
          ],
        ),
      ),
    );
  }
}

/// 顶栏：返回、片名、系统时间与预计结束时间、投屏、设置。剧集的片名由
/// [PlayerArgs.displayTitle] 写成「剧名 · 第 1 季 第 2 集」。
class _TopBar extends StatelessWidget {
  const _TopBar({
    required this.tv,
    required this.title,
    required this.position,
    required this.duration,
    required this.rate,
    required this.castable,
    required this.onClose,
    required this.onCast,
    required this.onOpenSettings,
    this.now,
  });

  final bool tv;
  final String title;
  final Duration position;
  final Duration duration;
  final double rate;
  final DateTime? now;
  final bool castable;
  final VoidCallback onClose;
  final VoidCallback onCast;
  final VoidCallback onOpenSettings;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final iconSize = tv ? 30.0 : 24.0;
    return LayoutBuilder(
      builder: (context, constraints) {
        final showClock = tv || constraints.maxWidth >= 480;
        return Row(
          children: [
            _ControlButton(
              tv: tv,
              icon: Icons.arrow_back_ios_new_rounded,
              iconSize: iconSize,
              tooltip: '返回',
              onPressed: onClose,
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
            if (showClock)
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 8),
                child: _TopClock(
                  tv: tv,
                  position: position,
                  duration: duration,
                  rate: rate,
                  now: now,
                ),
              ),
            if (castable)
              _ControlButton(
                tv: tv,
                icon: Icons.cast_rounded,
                iconSize: iconSize,
                tooltip: '投屏到电视',
                onPressed: onCast,
              ),
            _ControlButton(
              tv: tv,
              icon: Icons.settings_rounded,
              iconSize: iconSize,
              tooltip: '播放设置',
              onPressed: onOpenSettings,
            ),
          ],
        );
      },
    );
  }
}

class _TopClock extends StatelessWidget {
  const _TopClock({
    required this.tv,
    required this.position,
    required this.duration,
    required this.rate,
    this.now,
  });

  final bool tv;
  final Duration position;
  final Duration duration;
  final double rate;
  final DateTime? now;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final text = formatTopBarTime(
      now ?? DateTime.now(),
      position: position,
      duration: duration,
      rate: rate,
    );
    return Text(
      text,
      style: (tv ? textTheme.titleSmall : textTheme.bodySmall)!.copyWith(
        color: CinemaColors.text2,
        fontFeatures: const [FontFeature.tabularFigures()],
      ),
    );
  }
}

/// 进度条上方右侧的「跳过片头」轻量 scrim 胶囊按钮。
class _SkipIntroButton extends StatefulWidget {
  const _SkipIntroButton({required this.tv, required this.onPressed});

  final bool tv;
  final VoidCallback onPressed;

  @override
  State<_SkipIntroButton> createState() => _SkipIntroButtonState();
}

class _SkipIntroButtonState extends State<_SkipIntroButton> {
  bool _focused = false;
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    final reduced = Motion.reduced(context);
    final foreground = _focused ? CinemaColors.amberInk : CinemaColors.text0;
    final background = _focused
        ? CinemaColors.amber
        : _hovered
        ? Color.alphaBlend(CinemaColors.hover, CinemaColors.scrim)
        : CinemaColors.scrim;
    return FocusableActionDetector(
      mouseCursor: SystemMouseCursors.click,
      onShowFocusHighlight: (v) => setState(() => _focused = v),
      onShowHoverHighlight: (v) => setState(() => _hovered = v),
      actions: {
        ActivateIntent: CallbackAction<ActivateIntent>(
          onInvoke: (_) {
            widget.onPressed();
            return null;
          },
        ),
        ButtonActivateIntent: CallbackAction<ButtonActivateIntent>(
          onInvoke: (_) {
            widget.onPressed();
            return null;
          },
        ),
      },
      child: Semantics(
        button: true,
        label: '跳过片头',
        excludeSemantics: true,
        child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTap: widget.onPressed,
          child: PressScale(
            child: AnimatedScale(
              scale: _focused && !reduced ? 1.06 : 1,
              duration: reduced ? Duration.zero : Motion.focus,
              curve: Motion.emphasized,
              child: Container(
                padding: EdgeInsets.symmetric(
                  horizontal: widget.tv ? 16 : 14,
                  vertical: widget.tv ? 8 : 6,
                ),
                decoration: BoxDecoration(
                  color: background,
                  borderRadius: BorderRadius.circular(CinemaRadius.pill),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      Icons.fast_forward_rounded,
                      size: widget.tv ? 20 : 18,
                      color: foreground,
                    ),
                    const SizedBox(width: 6),
                    Text(
                      '跳过片头',
                      style:
                          (widget.tv
                                  ? Theme.of(context).textTheme.titleSmall
                                  : Theme.of(context).textTheme.labelMedium)!
                              .copyWith(color: foreground),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// 底栏按钮行：宽屏和横屏时正中一组按屏幕居中，窄屏或竖屏自动折为两行避免重叠。
class _BottomButtons extends StatelessWidget {
  const _BottomButtons({
    required this.tv,
    required this.playing,
    required this.rate,
    required this.episodic,
    required this.playFocus,
    required this.onTogglePlay,
    required this.onSeekBy,
    required this.onRate,
    required this.onAudio,
    required this.onSubtitles,
    required this.onPrevious,
    required this.onNext,
    required this.onEpisodes,
  });

  final bool tv;
  final bool playing;
  final double rate;
  final bool episodic;
  final FocusNode playFocus;
  final VoidCallback onTogglePlay;
  final ValueChanged<Duration> onSeekBy;
  final VoidCallback onRate;
  final VoidCallback onAudio;
  final VoidCallback onSubtitles;
  final VoidCallback? onPrevious;
  final VoidCallback? onNext;
  final VoidCallback? onEpisodes;

  @override
  Widget build(BuildContext context) {
    final rateButton = _ControlButton(
      tv: tv,
      label: '${_rateLabel(rate)}x',
      tooltip: '播放速度',
      onPressed: onRate,
    );
    Widget transportRow(double gapWidth) {
      final gap = SizedBox(width: gapWidth);
      return Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (episodic) ...[
            _ControlButton(
              tv: tv,
              icon: Icons.skip_previous_rounded,
              tooltip: '上一集',
              onPressed: onPrevious,
            ),
            gap,
          ],
          _ControlButton(
            tv: tv,
            icon: Icons.replay_10_rounded,
            tooltip: '后退 10 秒',
            onPressed: () => onSeekBy(-seekBackStep),
          ),
          gap,
          _ControlButton(
            tv: tv,
            primary: true,
            focusNode: playFocus,
            icon: playing ? Icons.pause_rounded : Icons.play_arrow_rounded,
            tooltip: playing ? '暂停' : '播放',
            onPressed: onTogglePlay,
          ),
          gap,
          _ControlButton(
            tv: tv,
            icon: Icons.forward_10_rounded,
            tooltip: '前进 10 秒',
            onPressed: () => onSeekBy(seekForwardStep),
          ),
          if (episodic) ...[
            gap,
            _ControlButton(
              tv: tv,
              icon: Icons.skip_next_rounded,
              tooltip: '下一集',
              onPressed: onNext,
            ),
          ],
        ],
      );
    }

    final utilityRow = Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        _ControlButton(
          tv: tv,
          icon: Icons.graphic_eq_rounded,
          tooltip: '音轨',
          onPressed: onAudio,
        ),
        _ControlButton(
          tv: tv,
          icon: Icons.subtitles_outlined,
          tooltip: '字幕',
          onPressed: onSubtitles,
        ),
        if (onEpisodes case final episodes?)
          _ControlButton(
            tv: tv,
            icon: Icons.playlist_play_rounded,
            tooltip: '选集',
            onPressed: episodes,
          ),
      ],
    );

    final playSize = tv ? 72.0 : 60.0;
    final btnSize = tv ? 60.0 : 48.0;
    final normalGap = tv ? 20.0 : 12.0;
    final transportCount = episodic ? 5 : 3;
    final transportWidth =
        playSize +
        btnSize * (transportCount - 1) +
        normalGap * (transportCount - 1);
    final rightCount = onEpisodes != null ? 3 : 2;
    final rightWidth = btnSize * rightCount;
    final leftWidth = tv ? 88.0 : 76.0;
    final centeredMinWidth =
        transportWidth +
        2 * (rightWidth > leftWidth ? rightWidth : leftWidth) +
        16;
    final singleRowMinWidth = leftWidth + transportWidth + rightWidth + 16;

    return LayoutBuilder(
      builder: (context, constraints) {
        final width = constraints.maxWidth;
        if (tv || width >= centeredMinWidth) {
          // 撑满宽度：外层 Column 给的是松约束，不撑满的话两侧按钮会叠到正中一组上
          return SizedBox(
            width: double.infinity,
            height: tv ? 80 : 64,
            child: Stack(
              alignment: Alignment.center,
              children: [
                Align(alignment: Alignment.centerLeft, child: rateButton),
                transportRow(normalGap),
                Align(alignment: Alignment.centerRight, child: utilityRow),
              ],
            ),
          );
        }
        if (width >= singleRowMinWidth) {
          return SizedBox(
            width: double.infinity,
            height: 64,
            child: Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [rateButton, transportRow(normalGap), utilityRow],
            ),
          );
        }
        return Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Center(child: transportRow(width < 320 ? 4 : normalGap)),
            const SizedBox(height: 4),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [rateButton, utilityRow],
            ),
          ],
        );
      },
    );
  }
}

/// 控制层的按钮：平时只有白色图标（或倍速文字），浮在画面两侧的可配 scrim 圆底；
/// 遥控器聚焦时衬一块琥珀圆底并略放大，触屏与鼠标补齐按压和悬停反馈。
/// [primary] 是播放暂停，比其他大一圈。[onPressed] 为空时变灰、不响应。
class _ControlButton extends StatefulWidget {
  const _ControlButton({
    required this.tv,
    required this.tooltip,
    required this.onPressed,
    this.icon,
    this.iconSize,
    this.label,
    this.focusNode,
    this.primary = false,
    this.scrim = false,
  }) : assert(icon != null || label != null);

  final bool tv;
  final IconData? icon;
  final double? iconSize;

  /// 用文字代替图标，如倍速「1.0x」
  final String? label;
  final String tooltip;
  final VoidCallback? onPressed;
  final FocusNode? focusNode;
  final bool primary;
  final bool scrim;

  @override
  State<_ControlButton> createState() => _ControlButtonState();
}

class _ControlButtonState extends State<_ControlButton> {
  bool _focused = false;
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    final onPressed = widget.onPressed;
    final enabled = onPressed != null;
    final reduced = Motion.reduced(context);
    final size = widget.primary
        ? (widget.tv ? 72.0 : 60.0)
        : (widget.tv ? 60.0 : 48.0);
    final foreground = _focused ? CinemaColors.amberInk : CinemaColors.text0;
    final background = _focused
        ? CinemaColors.amber
        : (_hovered && enabled
              ? (widget.scrim
                    ? Color.alphaBlend(CinemaColors.hover, CinemaColors.scrim)
                    : CinemaColors.hover)
              : (widget.scrim ? CinemaColors.scrim : Colors.transparent));
    final effectiveIconSize =
        widget.iconSize ??
        (widget.primary
            ? (widget.tv ? 48.0 : 44.0)
            : (widget.tv ? 36.0 : 30.0));
    final label = widget.label;
    final Widget content = label != null
        ? Text(
            label,
            style:
                (widget.tv
                        ? Theme.of(context).textTheme.titleMedium
                        : Theme.of(context).textTheme.titleSmall)!
                    .copyWith(
                      color: foreground,
                      fontFeatures: const [FontFeature.tabularFigures()],
                    ),
          )
        : AnimatedSwitcher(
            duration: reduced ? Duration.zero : Motion.short,
            switchInCurve: Motion.emphasized,
            switchOutCurve: Motion.emphasized,
            child: Icon(
              widget.icon,
              key: ValueKey(widget.icon),
              size: effectiveIconSize,
              color: foreground,
            ),
          );
    return FocusableActionDetector(
      focusNode: widget.focusNode,
      enabled: enabled,
      mouseCursor: enabled ? SystemMouseCursors.click : MouseCursor.defer,
      onShowFocusHighlight: (v) => setState(() => _focused = v),
      onShowHoverHighlight: (v) => setState(() => _hovered = v),
      actions: {
        ActivateIntent: CallbackAction<ActivateIntent>(
          onInvoke: (_) {
            onPressed?.call();
            return null;
          },
        ),
        // Web 上回车发的是 ButtonActivateIntent，空格和 TV 的确定键才是 ActivateIntent
        ButtonActivateIntent: CallbackAction<ButtonActivateIntent>(
          onInvoke: (_) {
            onPressed?.call();
            return null;
          },
        ),
      },
      child: Semantics(
        button: true,
        enabled: enabled,
        label: widget.tooltip,
        excludeSemantics: true,
        child: Tooltip(
          message: widget.tooltip,
          child: GestureDetector(
            // 整块都接收点按，只按在图标附近会漏到下面的画面手势
            behavior: HitTestBehavior.opaque,
            onTap: onPressed,
            child: PressScale(
              enabled: enabled,
              child: Opacity(
                opacity: enabled ? 1 : .4,
                child: AnimatedScale(
                  scale: _focused && !reduced ? 1.06 : 1,
                  duration: reduced ? Duration.zero : Motion.focus,
                  curve: Motion.emphasized,
                  // 不用 Container 的 alignment：放在 Align 里时它会撑满整行
                  child: AnimatedContainer(
                    duration: Motion.focus,
                    curve: Motion.emphasized,
                    width: label == null ? size : null,
                    height: size,
                    padding: label != null
                        ? const EdgeInsets.symmetric(horizontal: 12)
                        : null,
                    decoration: BoxDecoration(
                      color: background,
                      borderRadius: BorderRadius.circular(CinemaRadius.pill),
                    ),
                    child: Align(widthFactor: 1, child: content),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// 进度条和两端时间。触屏是可拖的滑块；遥控器上是可聚焦的 [_TvSeekBar]。
class _SeekRow extends StatelessWidget {
  const _SeekRow({
    required this.tv,
    required this.position,
    required this.duration,
    required this.buffer,
    required this.seekFocus,
    required this.onScrubBy,
    required this.onScrubStart,
    required this.onScrub,
    required this.onScrubEnd,
    this.chapters = const [],
  });

  final bool tv;
  final Duration position;
  final Duration duration;
  final Duration buffer;
  final List<PlayerChapter> chapters;
  final FocusNode seekFocus;
  final ValueChanged<Duration> onScrubBy;
  final VoidCallback onScrubStart;
  final ValueChanged<Duration> onScrub;
  final VoidCallback onScrubEnd;

  @override
  Widget build(BuildContext context) {
    final max = duration.inMilliseconds.toDouble();
    final buffered = max > 0
        ? buffer.inMilliseconds.clamp(0, max).toDouble()
        : 0.0;
    const sliderPadding = 12.0;
    return Row(
      children: [
        _TimeText(formatPlayerClock(position), tv: tv),
        Expanded(
          child: tv
              // 遥控器上下键移到进度条，左右键快退快进
              ? _TvSeekBar(
                  focusNode: seekFocus,
                  position: position,
                  duration: duration,
                  buffer: buffer,
                  chapters: chapters,
                  onSeekBy: onScrubBy,
                )
              // 触屏直接拖；遥控器用左右键快进快退，滑块不参与焦点移动。
              // 固定高度：Stack 里的 Align 给的是松约束，不固定的话滑块会撑满整屏
              : SizedBox(
                  height: 40,
                  child: LayoutBuilder(
                    builder: (context, constraints) {
                      final trackWidth =
                          (constraints.maxWidth - sliderPadding * 2).clamp(
                            0.0,
                            double.infinity,
                          );
                      return Stack(
                        alignment: Alignment.center,
                        children: [
                          ExcludeFocus(
                            child: SliderTheme(
                              data: SliderTheme.of(context).copyWith(
                                trackHeight: 3,
                                activeTrackColor: CinemaColors.amber,
                                secondaryActiveTrackColor: CinemaColors.text0
                                    .withValues(alpha: .45),
                                inactiveTrackColor: CinemaColors.track,
                                thumbColor: CinemaColors.amber,
                                overlayColor: CinemaColors.amberSoft,
                                thumbShape: const RoundSliderThumbShape(
                                  enabledThumbRadius: 7,
                                ),
                              ),
                              child: Slider(
                                padding: const EdgeInsets.symmetric(
                                  horizontal: sliderPadding,
                                ),
                                max: max > 0 ? max : 1,
                                value: max > 0
                                    ? position.inMilliseconds
                                          .clamp(0, max)
                                          .toDouble()
                                    : 0,
                                secondaryTrackValue: max > 0 && buffered > 0
                                    ? buffered
                                    : null,
                                onChangeStart: max > 0
                                    ? (_) => onScrubStart()
                                    : null,
                                onChanged: max > 0
                                    ? (v) => onScrub(
                                        Duration(milliseconds: v.round()),
                                      )
                                    : null,
                                onChangeEnd: max > 0
                                    ? (_) => onScrubEnd()
                                    : null,
                              ),
                            ),
                          ),
                          if (max > 0 && chapters.isNotEmpty)
                            IgnorePointer(
                              child: Stack(
                                alignment: Alignment.centerLeft,
                                children: [
                                  for (final chapter in chapters)
                                    if (chapter.start > Duration.zero &&
                                        chapter.start < duration)
                                      Positioned(
                                        left:
                                            sliderPadding +
                                            trackWidth *
                                                (chapter.start.inMilliseconds /
                                                    max) -
                                            1.5,
                                        child: Container(
                                          width: 3,
                                          height: 6,
                                          decoration: BoxDecoration(
                                            color: CinemaColors.text0
                                                .withValues(alpha: .8),
                                            borderRadius: BorderRadius.circular(
                                              CinemaRadius.pill,
                                            ),
                                          ),
                                        ),
                                      ),
                                ],
                              ),
                            ),
                        ],
                      );
                    },
                  ),
                ),
        ),
        _TimeText(
          formatPlayerClock(
            duration > Duration.zero ? duration : const Duration(seconds: -1),
          ),
          tv: tv,
        ),
      ],
    );
  }
}

/// 播放设置面板的分段。底栏的倍速、音轨、字幕按钮只打开其中一段，齿轮打开全部。
enum PlayerSettingsSection {
  audio('音频'),
  subtitle('字幕'),
  rate('播放速度'),
  picture('画面');

  const PlayerSettingsSection(this.label);
  final String label;
}

/// 播放设置面板：音频、字幕、播放速度、画面。[only] 为空时四段都列（齿轮），
/// 否则只列这一段、标题就是段名（底栏按钮）。
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
  double subtitleScale = 1.0,
  double subtitleBottomPadding = 28.0,
  double subtitleDelay = 0.0,
  bool autoNext = true,
  bool showStats = false,
  List<PlayerChapter> chapters = const [],
  Duration position = Duration.zero,
  ValueChanged<double>? onSubtitleScale,
  ValueChanged<double>? onSubtitleBottomPadding,
  ValueChanged<double>? onSubtitleDelay,
  ValueChanged<bool>? onAutoNext,
  ValueChanged<bool>? onShowStats,
  ValueChanged<PlayerChapter>? onJumpChapter,
  PlayerSettingsSection? only,
}) => showDialog<void>(
  context: context,
  builder: (context) => Dialog(
    child: ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 420),
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 12),
        child: _PlayerSettings(
          only: only,
          audios: audios,
          audioId: audioId,
          subtitles: subtitles,
          subtitleId: subtitleId,
          rate: rate,
          fill: fill,
          canSearchSubtitles: canSearchSubtitles,
          subtitleScale: subtitleScale,
          subtitleBottomPadding: subtitleBottomPadding,
          subtitleDelay: subtitleDelay,
          autoNext: autoNext,
          showStats: showStats,
          chapters: chapters,
          position: position,
          onAudio: onAudio,
          onSubtitle: onSubtitle,
          onRate: onRate,
          onFill: onFill,
          onSearchSubtitles: onSearchSubtitles,
          onSubtitleScale: onSubtitleScale,
          onSubtitleBottomPadding: onSubtitleBottomPadding,
          onSubtitleDelay: onSubtitleDelay,
          onAutoNext: onAutoNext,
          onShowStats: onShowStats,
          onJumpChapter: onJumpChapter,
        ),
      ),
    ),
  ),
);

class _PlayerSettings extends StatefulWidget {
  const _PlayerSettings({
    required this.only,
    required this.audios,
    required this.audioId,
    required this.subtitles,
    required this.subtitleId,
    required this.rate,
    required this.fill,
    required this.canSearchSubtitles,
    required this.subtitleScale,
    required this.subtitleBottomPadding,
    required this.subtitleDelay,
    required this.autoNext,
    required this.showStats,
    required this.chapters,
    required this.position,
    required this.onAudio,
    required this.onSubtitle,
    required this.onRate,
    required this.onFill,
    required this.onSearchSubtitles,
    this.onSubtitleScale,
    this.onSubtitleBottomPadding,
    this.onSubtitleDelay,
    this.onAutoNext,
    this.onShowStats,
    this.onJumpChapter,
  });

  final PlayerSettingsSection? only;
  final List<AudioTrack> audios;
  final String? audioId;
  final List<SubtitleTrack> subtitles;
  final String? subtitleId;
  final double rate;
  final bool fill;
  final bool canSearchSubtitles;
  final double subtitleScale;
  final double subtitleBottomPadding;
  final double subtitleDelay;
  final bool autoNext;
  final bool showStats;
  final List<PlayerChapter> chapters;
  final Duration position;
  final ValueChanged<AudioTrack> onAudio;
  final ValueChanged<SubtitleTrack?> onSubtitle;
  final ValueChanged<double> onRate;
  final ValueChanged<bool> onFill;
  final Future<void> Function() onSearchSubtitles;
  final ValueChanged<double>? onSubtitleScale;
  final ValueChanged<double>? onSubtitleBottomPadding;
  final ValueChanged<double>? onSubtitleDelay;
  final ValueChanged<bool>? onAutoNext;
  final ValueChanged<bool>? onShowStats;
  final ValueChanged<PlayerChapter>? onJumpChapter;

  @override
  State<_PlayerSettings> createState() => _PlayerSettingsState();
}

class _PlayerSettingsState extends State<_PlayerSettings> {
  // 面板不关就要自己记选中项，否则点了没反应
  late String? _audioId = widget.audioId;
  late String? _subtitleId = widget.subtitleId;
  late double _rate = widget.rate;
  late bool _fill = widget.fill;
  late double _subtitleScale = widget.subtitleScale;
  late double _subtitleBottomPadding = widget.subtitleBottomPadding;
  late double _subtitleDelay = widget.subtitleDelay;
  late bool _autoNext = widget.autoNext;
  late bool _showStats = widget.showStats;

  /// 搜索要关掉面板再开搜索弹层，不叠两层对话框
  Future<void> _search() async {
    Navigator.of(context).pop();
    await widget.onSearchSubtitles();
  }

  void _updateDelay(double next) {
    final rounded = (next * 10).round() / 10;
    setState(() => _subtitleDelay = rounded);
    widget.onSubtitleDelay?.call(rounded);
  }

  static String _formatDelay(double seconds) {
    if (seconds == 0) return '0.0s';
    final sign = seconds > 0 ? '+' : '';
    return '$sign${seconds.toStringAsFixed(1)}s';
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

    final only = widget.only;
    bool shows(PlayerSettingsSection section) =>
        only == null || only == section;
    // 只列一段时标题就是段名，段内不再重复小标题
    Widget? label(PlayerSettingsSection section) =>
        only == null ? _SectionLabel(section.label) : null;
    final nextChapter = nextChapterAfter(widget.chapters, widget.position);

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        PickerHeader(title: only?.label ?? '播放设置'),
        if (only == PlayerSettingsSection.audio && widget.audios.isEmpty)
          const _SectionLabel('这个文件只有一条音轨'),
        if (shows(PlayerSettingsSection.audio) && widget.audios.isNotEmpty) ...[
          ?label(PlayerSettingsSection.audio),
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
        if (shows(PlayerSettingsSection.subtitle)) ...[
          ?label(PlayerSettingsSection.subtitle),
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
                onTap: () => unawaited(_search()),
              ),
            ),
          _SubtitleAppearanceControls(
            scale: _subtitleScale,
            bottomPadding: _subtitleBottomPadding,
            delayText: _formatDelay(_subtitleDelay),
            onScaleChanged: (value) {
              setState(() => _subtitleScale = value);
              widget.onSubtitleScale?.call(value);
            },
            onBottomPaddingChanged: (value) {
              setState(() => _subtitleBottomPadding = value);
              widget.onSubtitleBottomPadding?.call(value);
            },
            onDelayStep: (delta) => _updateDelay(_subtitleDelay + delta),
            onDelayReset: () => _updateDelay(0.0),
          ),
        ],
        if (shows(PlayerSettingsSection.rate)) ...[
          ?label(PlayerSettingsSection.rate),
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
        ],
        if (shows(PlayerSettingsSection.picture)) ...[
          ?label(PlayerSettingsSection.picture),
          _ScrollIntoView(
            child: PickerOption(
              icon: Icons.fit_screen_outlined,
              title: '完整显示',
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
              icon: Icons.fullscreen_rounded,
              title: '填满屏幕',
              selected: _fill,
              autofocus: takeFocus(_fill),
              onTap: () {
                widget.onFill(true);
                setState(() => _fill = true);
              },
            ),
          ),
          _ScrollIntoView(
            child: _SettingsSwitchRow(
              icon: Icons.skip_next_rounded,
              title: '自动连播下一集',
              value: _autoNext,
              onChanged: (value) {
                setState(() => _autoNext = value);
                widget.onAutoNext?.call(value);
              },
            ),
          ),
          _ScrollIntoView(
            child: _SettingsSwitchRow(
              icon: Icons.info_outline_rounded,
              title: '播放信息',
              value: _showStats,
              onChanged: (value) {
                setState(() => _showStats = value);
                widget.onShowStats?.call(value);
              },
            ),
          ),
        ],
        if (only == null &&
            widget.chapters.isNotEmpty &&
            widget.onJumpChapter != null) ...[
          const _SectionLabel('章节'),
          if (nextChapter != null)
            _ScrollIntoView(
              child: PickerOption(
                icon: Icons.fast_forward_rounded,
                title: '下一章节 · ${nextChapter.title}',
                subtitle: formatPlayerClock(nextChapter.start),
                onTap: () => widget.onJumpChapter!(nextChapter),
              ),
            ),
          for (final chapter in widget.chapters)
            _ScrollIntoView(
              child: PickerOption(
                title: chapter.title,
                subtitle: formatPlayerClock(chapter.start),
                onTap: () => widget.onJumpChapter!(chapter),
              ),
            ),
        ],
      ],
    );
  }
}

class _SubtitleAppearanceControls extends StatelessWidget {
  const _SubtitleAppearanceControls({
    required this.scale,
    required this.bottomPadding,
    required this.delayText,
    required this.onScaleChanged,
    required this.onBottomPaddingChanged,
    required this.onDelayStep,
    required this.onDelayReset,
  });

  final double scale;
  final double bottomPadding;
  final String delayText;
  final ValueChanged<double> onScaleChanged;
  final ValueChanged<double> onBottomPaddingChanged;
  final ValueChanged<double> onDelayStep;
  final VoidCallback onDelayReset;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final labelStyle = textTheme.bodySmall!.copyWith(color: CinemaColors.text2);
    return Padding(
      padding: const EdgeInsets.fromLTRB(8, 8, 8, 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              SizedBox(width: 68, child: Text('字幕字号', style: labelStyle)),
              Expanded(
                child: Wrap(
                  spacing: 8,
                  runSpacing: 6,
                  children: [
                    for (final option in subtitleScaleOptions)
                      ChoiceChip(
                        label: Text(option.label),
                        selected: (scale - option.value).abs() < 0.01,
                        onSelected: (_) => onScaleChanged(option.value),
                      ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              SizedBox(width: 68, child: Text('字幕位置', style: labelStyle)),
              Expanded(
                child: Wrap(
                  spacing: 8,
                  runSpacing: 6,
                  children: [
                    for (final option in subtitleBottomPaddingOptions)
                      ChoiceChip(
                        label: Text(option.label),
                        selected: (bottomPadding - option.value).abs() < 0.5,
                        onSelected: (_) => onBottomPaddingChanged(option.value),
                      ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              SizedBox(width: 68, child: Text('时间偏移', style: labelStyle)),
              Expanded(
                child: Row(
                  children: [
                    OutlinedButton(
                      onPressed: () => onDelayStep(-0.5),
                      child: const Text('-0.5s'),
                    ),
                    const SizedBox(width: 6),
                    Expanded(
                      child: Tooltip(
                        message: '归零',
                        child: TextButton(
                          onPressed: onDelayReset,
                          child: Text(
                            delayText,
                            style: textTheme.titleSmall!.copyWith(
                              color: CinemaColors.text0,
                              fontFeatures: const [
                                FontFeature.tabularFigures(),
                              ],
                            ),
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(width: 6),
                    OutlinedButton(
                      onPressed: () => onDelayStep(0.5),
                      child: const Text('+0.5s'),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _SettingsSwitchRow extends StatelessWidget {
  const _SettingsSwitchRow({
    required this.icon,
    required this.title,
    required this.value,
    required this.onChanged,
  });

  final IconData icon;
  final String title;
  final bool value;
  final ValueChanged<bool> onChanged;

  @override
  Widget build(BuildContext context) {
    return FocusRing(
      child: SwitchListTile(
        value: value,
        onChanged: onChanged,
        contentPadding: const EdgeInsets.symmetric(horizontal: 8),
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.all(Radius.circular(CinemaRadius.card)),
        ),
        secondary: Container(
          width: 36,
          height: 36,
          decoration: BoxDecoration(
            color: value ? CinemaColors.amberSoft : CinemaColors.surface3,
            borderRadius: BorderRadius.circular(CinemaRadius.small),
          ),
          child: Icon(
            icon,
            size: 20,
            color: value ? CinemaColors.amber : CinemaColors.text1,
          ),
        ),
        title: Text(
          title,
          style: TextStyle(
            color: CinemaColors.text1,
            fontWeight: FontWeight.w500,
          ),
        ),
      ),
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
