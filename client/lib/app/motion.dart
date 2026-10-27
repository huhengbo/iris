import 'package:flutter/material.dart';

/// 动效令牌，取值见 DESIGN.md「动效」一节。
abstract final class Motion {
  static const press = Duration(milliseconds: 120);

  /// 聚焦反馈要跟得上遥控器方向键的连续输入，不能更长。
  static const focus = Duration(milliseconds: 180);
  static const short = Duration(milliseconds: 200);
  static const medium = Duration(milliseconds: 320);
  static const long = Duration(milliseconds: 450);

  /// 快出慢停，用于页面、弹层和共享元素。
  static const emphasized = Cubic(0.32, 0.72, 0, 1);

  /// 按下的一瞬间：立刻响应，配 [press]。
  static const pressCurve = Curves.easeOut;

  /// 系统开启「减少动态效果」时只保留淡入淡出。
  static bool reduced(BuildContext context) =>
      MediaQuery.maybeDisableAnimationsOf(context) ?? false;
}

/// 页面切换：淡入加 4% 横向位移；减少动态效果时只淡入。
class CinemaPageTransitionsBuilder extends PageTransitionsBuilder {
  const CinemaPageTransitionsBuilder();

  @override
  Duration get transitionDuration => Motion.medium;

  @override
  Duration get reverseTransitionDuration => Motion.medium;

  @override
  Widget buildTransitions<T>(
    PageRoute<T> route,
    BuildContext context,
    Animation<double> animation,
    Animation<double> secondaryAnimation,
    Widget child,
  ) {
    final curved = CurvedAnimation(
      parent: animation,
      curve: Motion.emphasized,
      reverseCurve: Motion.emphasized.flipped,
    );
    final faded = FadeTransition(opacity: curved, child: child);
    if (Motion.reduced(context)) return faded;
    return SlideTransition(
      position: Tween(
        begin: const Offset(0.04, 0),
        end: Offset.zero,
      ).animate(curved),
      child: faded,
    );
  }
}

/// 按压反馈：按下缩到 0.97，松开回弹。只负责视觉，点击仍由子组件处理。
class PressScale extends StatefulWidget {
  const PressScale({super.key, required this.child, this.enabled = true});

  final Widget child;
  final bool enabled;

  @override
  State<PressScale> createState() => _PressScaleState();
}

class _PressScaleState extends State<PressScale> {
  bool _pressed = false;

  void _set(bool value) {
    if (_pressed != value) setState(() => _pressed = value);
  }

  @override
  Widget build(BuildContext context) {
    final active = widget.enabled && !Motion.reduced(context);
    return Listener(
      onPointerDown: active ? (_) => _set(true) : null,
      onPointerUp: (_) => _set(false),
      onPointerCancel: (_) => _set(false),
      child: AnimatedScale(
        scale: active && _pressed ? 0.97 : 1,
        duration: _pressed ? Motion.press : Motion.short,
        curve: _pressed ? Motion.pressCurve : Motion.emphasized,
        child: widget.child,
      ),
    );
  }
}
