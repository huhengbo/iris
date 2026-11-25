import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../app/motion.dart';
import '../../app/theme.dart';
import '../../app/tv.dart';

/// 横向片架的鼠标支持（照搬 Plex）：鼠标移到片架上时，左右两端出现翻页箭头，
/// 点一下滑过大半屏；滑到头的那一侧不出箭头；两端未滑到头时显示克制的边缘渐隐提示，
/// 桌面按住 Shift 滚轮可横向平滑滚动。触屏和 TV 不出箭头（直接滑动或用方向键）。
///
/// [builder] 里的横向滚动视图要用传进来的 [ScrollController]。
class RailScroller extends StatefulWidget {
  const RailScroller({super.key, required this.builder, this.controller});

  final Widget Function(BuildContext context, ScrollController controller)
  builder;

  /// 片架自己要控制滚动位置（比如打开时滚到某一项）时传进来，由调用方负责释放
  final ScrollController? controller;

  @override
  State<RailScroller> createState() => _RailScrollerState();
}

class _RailScrollerState extends State<RailScroller> {
  static const _fadeWidth = 28.0;

  ScrollController? _own;
  bool _hovered = false;
  bool _canBack = false;
  bool _canForward = false;
  double? _wheelTarget;

  ScrollController get _controller =>
      widget.controller ?? (_own ??= ScrollController());

  @override
  void dispose() {
    _own?.dispose();
    super.dispose();
  }

  bool _onMetrics(ScrollMetrics metrics) {
    final back = metrics.pixels > 1;
    final forward = metrics.pixels < metrics.maxScrollExtent - 1;
    if (back != _canBack || forward != _canForward) {
      // 通知可能发生在布局阶段，等这一帧结束再更新
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) {
          setState(() {
            _canBack = back;
            _canForward = forward;
          });
        }
      });
    }
    return false;
  }

  void _page(int direction) {
    final controller = _controller;
    if (!controller.hasClients) return;
    final position = controller.position;
    final target =
        (position.pixels + direction * position.viewportDimension * .8).clamp(
          0.0,
          position.maxScrollExtent,
        );
    _wheelTarget = null;
    if (Motion.reduced(context)) {
      controller.jumpTo(target);
    } else {
      controller.animateTo(
        target,
        duration: Motion.medium,
        curve: Motion.emphasized,
      );
    }
  }

  void _onPointerSignal(PointerSignalEvent event) {
    if (event is! PointerScrollEvent) return;
    if (!HardwareKeyboard.instance.isShiftPressed) return;
    final controller = _controller;
    if (!controller.hasClients) return;
    final delta = event.scrollDelta.dy != 0
        ? event.scrollDelta.dy
        : event.scrollDelta.dx;
    if (delta == 0) return;

    GestureBinding.instance.pointerSignalResolver.register(event, (_) {
      if (!mounted || !controller.hasClients) return;
      final position = controller.position;
      final base = _wheelTarget ?? position.pixels;
      final target = (base + delta).clamp(0.0, position.maxScrollExtent);
      _wheelTarget = target;
      if (Motion.reduced(context)) {
        _wheelTarget = null;
        controller.jumpTo(target);
      } else {
        controller
            .animateTo(target, duration: Motion.short, curve: Curves.easeOut)
            .whenComplete(() {
              if (_wheelTarget == target) _wheelTarget = null;
            });
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final bg = Theme.of(context).scaffoldBackgroundColor;
    final child = NotificationListener<ScrollMetricsNotification>(
      onNotification: (n) => _onMetrics(n.metrics),
      child: NotificationListener<ScrollNotification>(
        onNotification: (n) => _onMetrics(n.metrics),
        child: widget.builder(context, _controller),
      ),
    );
    final withFades = Stack(
      clipBehavior: Clip.none,
      children: [
        child,
        Positioned(
          top: 0,
          bottom: 0,
          left: 0,
          width: _fadeWidth,
          child: IgnorePointer(
            child: AnimatedOpacity(
              opacity: _canBack ? 1 : 0,
              duration: Motion.short,
              child: DecoratedBox(
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.centerLeft,
                    end: Alignment.centerRight,
                    colors: [
                      bg.withValues(alpha: 0.85),
                      bg.withValues(alpha: 0),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
        Positioned(
          top: 0,
          bottom: 0,
          right: 0,
          width: _fadeWidth,
          child: IgnorePointer(
            child: AnimatedOpacity(
              opacity: _canForward ? 1 : 0,
              duration: Motion.short,
              child: DecoratedBox(
                decoration: BoxDecoration(
                  gradient: LinearGradient(
                    begin: Alignment.centerRight,
                    end: Alignment.centerLeft,
                    colors: [
                      bg.withValues(alpha: 0.85),
                      bg.withValues(alpha: 0),
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      ],
    );

    if (isTelevision(context)) return withFades;
    final showBack = _hovered && _canBack;
    final showForward = _hovered && _canForward;
    return Listener(
      onPointerSignal: _onPointerSignal,
      child: MouseRegion(
        onEnter: (_) => setState(() => _hovered = true),
        onExit: (_) => setState(() => _hovered = false),
        child: Stack(
          clipBehavior: Clip.none,
          children: [
            withFades,
            Positioned.fill(
              child: Align(
                alignment: Alignment.centerLeft,
                child: IgnorePointer(
                  ignoring: !showBack,
                  child: AnimatedSwitcher(
                    duration: Motion.short,
                    switchInCurve: Motion.emphasized,
                    switchOutCurve: Motion.emphasized,
                    child: showBack
                        ? _Arrow(
                            key: const ValueKey('back-arrow'),
                            icon: Icons.chevron_left,
                            tooltip: '向前',
                            onPressed: () => _page(-1),
                          )
                        : const SizedBox.shrink(key: ValueKey('back-empty')),
                  ),
                ),
              ),
            ),
            Positioned.fill(
              child: Align(
                alignment: Alignment.centerRight,
                child: IgnorePointer(
                  ignoring: !showForward,
                  child: AnimatedSwitcher(
                    duration: Motion.short,
                    switchInCurve: Motion.emphasized,
                    switchOutCurve: Motion.emphasized,
                    child: showForward
                        ? _Arrow(
                            key: const ValueKey('forward-arrow'),
                            icon: Icons.chevron_right,
                            tooltip: '向后',
                            onPressed: () => _page(1),
                          )
                        : const SizedBox.shrink(key: ValueKey('forward-empty')),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Arrow extends StatelessWidget {
  const _Arrow({
    super.key,
    required this.icon,
    required this.tooltip,
    required this.onPressed,
  });

  final IconData icon;
  final String tooltip;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 8),
      // 键盘和遥控器用方向键走片架，箭头只给鼠标
      child: ExcludeFocus(
        child: PressScale(
          child: IconButton.filled(
            tooltip: tooltip,
            style: IconButton.styleFrom(
              fixedSize: const Size.square(40),
              backgroundColor: CinemaColors.scrim,
              foregroundColor: CinemaColors.text0,
            ),
            onPressed: onPressed,
            icon: Icon(icon, size: 28),
          ),
        ),
      ),
    );
  }
}
