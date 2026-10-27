import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/servers.dart';
import '../features/auth/auth_controller.dart';
import 'motion.dart';
import 'router.dart';
import 'theme.dart';

/// 冷启动时套在整个应用外面，登录状态恢复好（[sessionSettled]）才放开。
class LaunchGate extends ConsumerWidget {
  const LaunchGate({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context, WidgetRef ref) => LaunchSplash(
    ready: sessionSettled(
      ref.watch(authControllerProvider),
      ref.watch(serverGateProvider),
    ),
    child: child,
  );
}

/// 开机动画：接着系统启动屏画同一枚光圈（160 圆底，屏幕正中），[ready] 后叶片旋转张开，
/// 应用从六边形开口里铺满屏幕。进程启动时播一次，从后台切回来不会再播；
/// 减弱动态效果时改成淡入。几何和 tool/brand/iris.py 生成的图标一致。
class LaunchSplash extends StatefulWidget {
  const LaunchSplash({super.key, required this.ready, required this.child});

  final bool ready;
  final Widget child;

  static const duration = Duration(milliseconds: 900);

  /// 先慢后快再缓停：叶片起步看得清，开口很快铺满。
  static const curve = Cubic(0.7, 0, 0.25, 1);

  /// 恢复登录超过这么久才露出转圈，正常启动时不会一闪而过。
  static const slowAfter = Duration(milliseconds: 1500);

  @override
  State<LaunchSplash> createState() => _LaunchSplashState();
}

class _LaunchSplashState extends State<LaunchSplash>
    with SingleTickerProviderStateMixin {
  late final _controller = AnimationController(
    vsync: this,
    duration: LaunchSplash.duration,
  )..addStatusListener(_onStatus);
  late final _progress = CurvedAnimation(
    parent: _controller,
    curve: LaunchSplash.curve,
  );
  Timer? _slowTimer;
  bool _started = false;
  bool _slow = false;
  bool _done = false;

  @override
  void initState() {
    super.initState();
    _slowTimer = Timer(LaunchSplash.slowAfter, () {
      if (mounted) setState(() => _slow = true);
    });
    if (widget.ready) _start();
  }

  @override
  void didUpdateWidget(LaunchSplash oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.ready && !_started) _start();
  }

  void _start() {
    _started = true;
    _slowTimer?.cancel();
    // 目标页面先在开口后面画好一帧，再张开
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted) return;
      if (Motion.reduced(context)) _controller.duration = Motion.short;
      _controller.forward();
    });
  }

  void _onStatus(AnimationStatus status) {
    if (status.isCompleted && mounted) setState(() => _done = true);
  }

  @override
  void dispose() {
    _slowTimer?.cancel();
    _progress.dispose();
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final reduced = Motion.reduced(context);
    // 层次固定不变，动画结束后应用（路由和页面状态）不会被重建
    return Stack(
      fit: StackFit.expand,
      children: [
        if (_done)
          const SizedBox.shrink()
        else
          Semantics(
            label: '正在启动',
            child: Stack(
              fit: StackFit.expand,
              children: [
                CustomPaint(
                  painter: _IrisPainter(
                    reduced ? kAlwaysDismissedAnimation : _progress,
                    backdrop: true,
                  ),
                ),
                Center(
                  child: Padding(
                    padding: const EdgeInsets.only(top: IrisMark.size + 96),
                    child: AnimatedOpacity(
                      opacity: _slow && !_started ? 1 : 0,
                      duration: Motion.medium,
                      child: const SizedBox.square(
                        dimension: 24,
                        child: CircularProgressIndicator(strokeWidth: 2.5),
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        IgnorePointer(
          ignoring: !_done,
          child: ExcludeSemantics(
            excluding: !_done,
            child: FadeTransition(
              opacity: reduced && !_done
                  ? _controller
                  : kAlwaysCompleteAnimation,
              child: ClipPath(
                clipper: reduced || _done ? null : _OpeningClipper(_progress),
                clipBehavior: reduced || _done ? Clip.none : Clip.antiAlias,
                child: widget.child,
              ),
            ),
          ),
        ),
      ],
    );
  }
}

/// 光圈几何，单位是逻辑像素，原点在圆心。对应 100 网格放大 1.6 倍：
/// 圆底半径 80（系统启动屏的 160dp 圆底），圆盘 48，开口 17.6，叶片缝 3.84。
abstract final class _Iris {
  static const base = 80.0;
  static const disk = 48.0;
  static const hole = 17.6;
  static const gap = 3.84;

  /// 开口顶点，第一个朝上。
  static List<Offset> hexagon(double radius, [double rotation = 0]) => [
    for (var k = 0; k < 6; k++)
      Offset.fromDirection(-math.pi / 2 + k * math.pi / 3 + rotation, radius),
  ];

  /// 叶片张开到开口盖住整个 [size] 时的放大倍数。
  static double fullScale(Size size) =>
      (size.center(Offset.zero).distance / math.cos(math.pi / 6) + 2) / hole;
}

/// 开口转过 60°，和图标叶片一起转。
double _rotation(double t) => t * math.pi / 3;

double _scale(double t, Size size) => 1 + (_Iris.fullScale(size) - 1) * t;

class _OpeningClipper extends CustomClipper<Path> {
  _OpeningClipper(this.progress) : super(reclip: progress);

  final Animation<double> progress;

  @override
  Path getClip(Size size) {
    final t = progress.value;
    if (t == 0) return Path();
    final points = _Iris.hexagon(
      _Iris.hole * _scale(t, size),
      _rotation(t),
    ).map((p) => p + size.center(Offset.zero)).toList();
    return Path()..addPolygon(points, true);
  }

  @override
  bool shouldReclip(_OpeningClipper oldClipper) =>
      oldClipper.progress != progress;
}

class _IrisPainter extends CustomPainter {
  _IrisPainter(this.progress, {this.backdrop = false})
    : super(repaint: progress);

  final Animation<double> progress;

  /// 开机动画铺暖黑底；作为标志使用时只画图标。
  final bool backdrop;

  @override
  void paint(Canvas canvas, Size size) {
    if (backdrop) canvas.drawPaint(Paint()..color = CinemaColors.bg);
    final t = progress.value;
    final scale = backdrop
        ? _scale(t, size)
        : size.shortestSide / (_Iris.base * 2);
    final amber = Paint()..color = CinemaColors.amber;
    canvas
      ..save()
      ..translate(size.width / 2, size.height / 2)
      ..rotate(_rotation(t))
      ..scale(scale)
      ..drawCircle(Offset.zero, _Iris.base, amber)
      ..drawCircle(
        Offset.zero,
        _Iris.disk,
        Paint()..color = CinemaColors.amberInk,
      )
      ..clipPath(
        Path()
          ..addOval(Rect.fromCircle(center: Offset.zero, radius: _Iris.disk)),
      );
    // 开口和叶片缝用底色盖出来；张开时开口后面是应用本身
    final hexagon = _Iris.hexagon(_Iris.hole);
    canvas.drawPath(Path()..addPolygon(hexagon, true), amber);
    final gap = Paint()
      ..color = CinemaColors.amber
      ..strokeWidth = _Iris.gap
      ..style = PaintingStyle.stroke;
    for (var k = 0; k < 6; k++) {
      final from = hexagon[k];
      final along = hexagon[(k + 1) % 6] - from;
      canvas.drawLine(
        from,
        from + along / along.distance * _Iris.disk * 2,
        gap,
      );
    }
    canvas.restore();
  }

  @override
  bool shouldRepaint(_IrisPainter oldDelegate) =>
      oldDelegate.progress != progress || oldDelegate.backdrop != backdrop;
}

/// 静态的光圈标志（琥珀圆底），用在登录页和关于页。
class IrisMark extends StatelessWidget {
  const IrisMark({super.key, this.dimension = size});

  /// 和开机动画、系统启动屏的圆底一样大。
  static const size = _Iris.base * 2;

  final double dimension;

  @override
  Widget build(BuildContext context) => SizedBox.square(
    dimension: dimension,
    child: CustomPaint(painter: _IrisPainter(kAlwaysDismissedAnimation)),
  );
}
