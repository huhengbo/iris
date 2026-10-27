import 'package:flutter/material.dart';

import '../../app/motion.dart';

/// 可点击的图片卡片（海报、剧照）的交互外壳：触屏按压缩放、鼠标悬停、
/// 键盘和遥控器聚焦都在这里处理，外观交给 [builder]。
/// 聚焦态只在键盘/遥控器操作时出现，触屏点击不会留下聚焦环。
class FocusTile extends StatefulWidget {
  const FocusTile({
    super.key,
    required this.onTap,
    required this.semanticLabel,
    required this.builder,
    this.focusScale = 1.08,
    this.autofocus = false,
    this.onLongPress,
  });

  final VoidCallback onTap;
  final String semanticLabel;
  final double focusScale;
  final bool autofocus;
  final VoidCallback? onLongPress;
  final Widget Function(BuildContext context, TileHighlight highlight) builder;

  @override
  State<FocusTile> createState() => _FocusTileState();
}

class TileHighlight {
  const TileHighlight({required this.focused, required this.hovered});

  final bool focused;
  final bool hovered;

  bool get any => focused || hovered;
}

class _FocusTileState extends State<FocusTile> {
  bool _focused = false;
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    final reduced = Motion.reduced(context);
    return FocusableActionDetector(
      autofocus: widget.autofocus,
      mouseCursor: SystemMouseCursors.click,
      onShowFocusHighlight: (v) => setState(() => _focused = v),
      onShowHoverHighlight: (v) => setState(() => _hovered = v),
      actions: {
        ActivateIntent: CallbackAction<ActivateIntent>(
          onInvoke: (_) {
            widget.onTap();
            return null;
          },
        ),
        // Web 上回车发的是 ButtonActivateIntent，空格和 TV 的确定键才是 ActivateIntent
        ButtonActivateIntent: CallbackAction<ButtonActivateIntent>(
          onInvoke: (_) {
            widget.onTap();
            return null;
          },
        ),
      },
      child: Semantics(
        button: true,
        label: widget.semanticLabel,
        excludeSemantics: true,
        child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTap: widget.onTap,
          onLongPress: widget.onLongPress,
          child: PressScale(
            child: AnimatedScale(
              scale: _focused && !reduced ? widget.focusScale : 1,
              duration: reduced ? Duration.zero : Motion.focus,
              curve: Motion.emphasized,
              child: widget.builder(
                context,
                TileHighlight(focused: _focused, hovered: _hovered),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
