import 'package:flutter/material.dart';

import '../../app/motion.dart';
import '../../app/theme.dart';

/// 简介类长文本：默认收起成几行，点按展开或收起。
class ExpandableText extends StatefulWidget {
  const ExpandableText(this.text, {super.key, this.maxLines = 2, this.style});

  final String text;
  final int maxLines;
  final TextStyle? style;

  @override
  State<ExpandableText> createState() => _ExpandableTextState();
}

class _ExpandableTextState extends State<ExpandableText> {
  bool _expanded = false;
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
    final reduced = Motion.reduced(context);
    // 文字常压在剧照上，默认的焦点淡底看不出来；键盘、遥控器聚焦时
    // 和按钮一样套琥珀描边，往外让一点免得贴着字
    final ring =
        _focused &&
        FocusManager.instance.highlightMode == FocusHighlightMode.traditional;
    return InkWell(
      borderRadius: BorderRadius.circular(CinemaRadius.small),
      onTap: () => setState(() => _expanded = !_expanded),
      onFocusChange: (focused) => setState(() => _focused = focused),
      child: Stack(
        clipBehavior: Clip.none,
        children: [
          Semantics(
            hint: _expanded ? '收起' : '展开全文',
            child: AnimatedSize(
              duration: reduced ? Duration.zero : Motion.short,
              curve: Motion.emphasized,
              alignment: Alignment.topLeft,
              child: Text(
                widget.text,
                maxLines: _expanded ? null : widget.maxLines,
                overflow: _expanded ? null : TextOverflow.ellipsis,
                style: widget.style,
              ),
            ),
          ),
          Positioned.fill(
            left: -8,
            top: -4,
            right: -8,
            bottom: -4,
            child: IgnorePointer(
              child: AnimatedContainer(
                duration: reduced ? Duration.zero : Motion.focus,
                curve: Motion.emphasized,
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(CinemaRadius.card),
                  border: Border.all(
                    color: ring ? CinemaColors.amber : Colors.transparent,
                    width: 2,
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
