import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../app/motion.dart';
import '../../app/theme.dart';

/// AI 等待指示：小号光圈在半开和全开之间呼吸开合，旁边是状态文字（DESIGN.md「AI 等待指示」）。
///
/// 只用于 AI 正在工作的地方，不用于普通加载。系统开启「减少动态效果」时光圈停在半开，
/// 文字后面改为秒数，仍然看得出在等。
class AiWaitIndicator extends StatefulWidget {
  const AiWaitIndicator({super.key, this.label = '正在思考…'});

  final String label;

  /// 图标尺寸，和 bodyMedium 的行高相配。
  static const size = 20.0;

  @override
  State<AiWaitIndicator> createState() => _AiWaitIndicatorState();
}

class _AiWaitIndicatorState extends State<AiWaitIndicator>
    with SingleTickerProviderStateMixin {
  late final _controller = AnimationController(
    vsync: this,
    // breath 是开再合的完整周期，一个方向只占一半
    duration: Motion.breath ~/ 2,
  );
  late final _curve = CurvedAnimation(
    parent: _controller,
    curve: Motion.emphasized,
    reverseCurve: Motion.emphasized,
  );
  Timer? _clock;
  int _seconds = 0;
  bool? _reduced;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final reduced = Motion.reduced(context);
    if (reduced == _reduced) return;
    _reduced = reduced;
    if (reduced) {
      _controller
        ..stop()
        ..value = 0;
      _clock ??= Timer.periodic(const Duration(seconds: 1), (_) {
        if (mounted) setState(() => _seconds++);
      });
    } else {
      _clock?.cancel();
      _clock = null;
      _controller.repeat(reverse: true);
    }
  }

  @override
  void dispose() {
    _clock?.cancel();
    _curve.dispose();
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final reduced = _reduced ?? false;
    final text = reduced && _seconds > 0
        ? '${widget.label.replaceAll(RegExp(r'…$'), '')} · ${_seconds}s'
        : widget.label;
    return Semantics(
      liveRegion: true,
      // 读屏只播报状态文字；秒数每秒都变，播报出来是干扰
      label: widget.label,
      child: ExcludeSemantics(
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            SizedBox.square(
              dimension: AiWaitIndicator.size,
              child: CustomPaint(painter: _BreathingIris(_curve)),
            ),
            const SizedBox(width: 8),
            Flexible(
              child: Text(
                text,
                style: Theme.of(context).textTheme.bodyMedium
                    ?.copyWith(color: CinemaColors.text1),
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// 开机动画那枚光圈的缩小版：琥珀圆底、深色圆盘，盘里是六边形开口。
/// [progress] 为 0 时半开，为 1 时全开，开口随之放大并转过 30°。
class _BreathingIris extends CustomPainter {
  _BreathingIris(this.progress) : super(repaint: progress);

  final Animation<double> progress;

  /// 以圆底半径为 1：圆盘 0.6，开口半径在 0.22（半开）和 0.5（全开）之间。
  static const _disk = 0.6;
  static const _halfOpen = 0.22;
  static const _fullOpen = 0.5;

  @override
  void paint(Canvas canvas, Size size) {
    final t = progress.value;
    final radius = size.shortestSide / 2;
    final amber = Paint()..color = CinemaColors.amber;
    canvas
      ..save()
      ..translate(size.width / 2, size.height / 2)
      ..drawCircle(Offset.zero, radius, amber)
      ..drawCircle(
        Offset.zero,
        radius * _disk,
        Paint()..color = CinemaColors.amberInk,
      );
    final hole = radius * (_halfOpen + (_fullOpen - _halfOpen) * t);
    final rotation = t * math.pi / 6;
    final hexagon = [
      for (var k = 0; k < 6; k++)
        Offset.fromDirection(-math.pi / 2 + k * math.pi / 3 + rotation, hole),
    ];
    canvas
      ..drawPath(Path()..addPolygon(hexagon, true), amber)
      ..restore();
  }

  @override
  bool shouldRepaint(_BreathingIris oldDelegate) =>
      oldDelegate.progress != progress;
}
