import 'package:flutter/material.dart';

import '../../app/motion.dart';
import '../../app/theme.dart';

/// 加载骨架：surface3 底加缓慢掠过的微光；减少动态效果时不动。
class Skeleton extends StatefulWidget {
  const Skeleton({super.key, this.borderRadius = BorderRadius.zero});

  final BorderRadius borderRadius;

  @override
  State<Skeleton> createState() => _SkeletonState();
}

class _SkeletonState extends State<Skeleton>
    with SingleTickerProviderStateMixin {
  late final _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1600),
  );

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (Motion.reduced(context)) {
      _controller.stop();
    } else if (!_controller.isAnimating) {
      _controller.repeat();
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, _) {
        final x = -2.0 + 4.0 * _controller.value;
        return DecoratedBox(
          decoration: BoxDecoration(
            borderRadius: widget.borderRadius,
            gradient: LinearGradient(
              begin: Alignment(x - 1, -0.4),
              end: Alignment(x + 1, 0.4),
              colors: const [
                CinemaColors.surface3,
                CinemaColors.shimmer,
                CinemaColors.surface3,
              ],
              stops: const [0.3, 0.5, 0.7],
            ),
          ),
          child: const SizedBox.expand(),
        );
      },
    );
  }
}
