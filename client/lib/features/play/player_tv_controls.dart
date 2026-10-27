part of 'player_page.dart';

/// 遥控器上的进度条：上下键移到它，左右键快退快进，长按连续跳。
/// 长按时一次跳三倍，和按住方向键翻页的手感一致。
class _TvSeekBar extends StatefulWidget {
  const _TvSeekBar({
    required this.focusNode,
    required this.position,
    required this.duration,
    required this.onSeekBy,
  });

  final FocusNode focusNode;
  final Duration position;
  final Duration duration;
  final void Function(Duration delta) onSeekBy;

  @override
  State<_TvSeekBar> createState() => _TvSeekBarState();
}

class _TvSeekBarState extends State<_TvSeekBar> {
  bool _focused = false;

  KeyEventResult _onKey(FocusNode node, KeyEvent event) {
    if (event is KeyUpEvent) return KeyEventResult.ignored;
    final times = event is KeyRepeatEvent ? 3 : 1;
    if (event.logicalKey == LogicalKeyboardKey.arrowLeft) {
      widget.onSeekBy(-seekBackStep * times);
      return KeyEventResult.handled;
    }
    if (event.logicalKey == LogicalKeyboardKey.arrowRight) {
      widget.onSeekBy(seekForwardStep * times);
      return KeyEventResult.handled;
    }
    return KeyEventResult.ignored;
  }

  @override
  Widget build(BuildContext context) {
    final total = widget.duration.inMilliseconds;
    final fraction = total > 0
        ? (widget.position.inMilliseconds / total).clamp(0.0, 1.0)
        : 0.0;
    final trackHeight = _focused ? 10.0 : 6.0;
    return Focus(
      focusNode: widget.focusNode,
      onKeyEvent: _onKey,
      onFocusChange: (v) => setState(() => _focused = v),
      child: SizedBox(
        height: 32,
        child: LayoutBuilder(
          builder: (context, constraints) {
            final width = constraints.maxWidth;
            return Stack(
              alignment: Alignment.centerLeft,
              clipBehavior: Clip.none,
              children: [
                AnimatedContainer(
                  duration: Motion.short,
                  height: trackHeight,
                  decoration: BoxDecoration(
                    color: CinemaColors.track,
                    borderRadius: BorderRadius.circular(trackHeight),
                  ),
                ),
                AnimatedContainer(
                  duration: Motion.short,
                  height: trackHeight,
                  width: width * fraction,
                  decoration: BoxDecoration(
                    color: CinemaColors.amber,
                    borderRadius: BorderRadius.circular(trackHeight),
                  ),
                ),
                if (_focused)
                  Positioned(
                    left: (width * fraction - 12).clamp(0.0, width - 24),
                    child: Container(
                      width: 24,
                      height: 24,
                      decoration: BoxDecoration(
                        color: CinemaColors.amber,
                        shape: BoxShape.circle,
                        border: Border.all(color: CinemaColors.text0, width: 3),
                      ),
                    ),
                  ),
              ],
            );
          },
        ),
      ),
    );
  }
}
