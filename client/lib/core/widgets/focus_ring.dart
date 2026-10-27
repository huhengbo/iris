import 'package:flutter/material.dart';

import '../../app/theme.dart';

/// 子组件里有焦点时描一圈 2px 琥珀环，给 ListTile、InkWell、Chip 这类
/// 主题里配不出聚焦描边的控件用（按钮的描边在 theme.dart）。
/// 和海报卡一样，只在键盘 / 遥控器操作时出现，触屏点按不显示。
class FocusRing extends StatefulWidget {
  const FocusRing({
    super.key,
    required this.child,
    this.radius = CinemaRadius.card,
  });

  final Widget child;
  final double radius;

  @override
  State<FocusRing> createState() => _FocusRingState();
}

class _FocusRingState extends State<FocusRing> {
  bool _focused = false;

  @override
  void initState() {
    super.initState();
    FocusManager.instance.addHighlightModeListener(_onHighlightMode);
  }

  @override
  void dispose() {
    FocusManager.instance.removeHighlightModeListener(_onHighlightMode);
    super.dispose();
  }

  void _onHighlightMode(FocusHighlightMode _) {
    if (_focused) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final ring =
        _focused &&
        FocusManager.instance.highlightMode == FocusHighlightMode.traditional;
    return Focus(
      canRequestFocus: false,
      skipTraversal: true,
      onFocusChange: (v) {
        if (v != _focused) setState(() => _focused = v);
      },
      // 装饰层常驻，只换边框颜色：按聚焦与否增删一层会改变树深度，
      // 子树（ListTile、InkWell）随之重建，焦点被首项 autofocus 抢回。
      child: DecoratedBox(
        position: DecorationPosition.foreground,
        decoration: BoxDecoration(
          border: Border.all(
            color: ring ? CinemaColors.amber : Colors.transparent,
            width: 2,
          ),
          borderRadius: BorderRadius.circular(widget.radius),
        ),
        child: widget.child,
      ),
    );
  }
}
