import 'package:flutter/material.dart';

import '../../app/motion.dart';
import '../../app/theme.dart';
import '../../app/tv.dart';

/// 横向片架的鼠标支持（照搬 Plex）：鼠标移到片架上时，左右两端出现翻页箭头，
/// 点一下滑过大半屏；滑到头的那一侧不出箭头。触屏和 TV 不出箭头（直接滑动或用方向键）。
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
  ScrollController? _own;
  bool _hovered = false;
  bool _canBack = false;
  bool _canForward = false;

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
    if (Motion.reduced(context)) {
      controller.jumpTo(target);
    } else {
      controller.animateTo(
        target,
        duration: Motion.focus,
        curve: Motion.emphasized,
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final child = NotificationListener<ScrollMetricsNotification>(
      onNotification: (n) => _onMetrics(n.metrics),
      child: NotificationListener<ScrollNotification>(
        onNotification: (n) => _onMetrics(n.metrics),
        child: widget.builder(context, _controller),
      ),
    );
    if (isTelevision(context)) return child;
    return MouseRegion(
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      child: Stack(
        children: [
          child,
          if (_hovered && _canBack)
            Positioned.fill(
              child: Align(
                alignment: Alignment.centerLeft,
                child: _Arrow(
                  icon: Icons.chevron_left,
                  tooltip: '向前',
                  onPressed: () => _page(-1),
                ),
              ),
            ),
          if (_hovered && _canForward)
            Positioned.fill(
              child: Align(
                alignment: Alignment.centerRight,
                child: _Arrow(
                  icon: Icons.chevron_right,
                  tooltip: '向后',
                  onPressed: () => _page(1),
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _Arrow extends StatelessWidget {
  const _Arrow({
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
    );
  }
}
