import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../app/motion.dart';
import '../../app/theme.dart';

/// 标签 + 值 + 行尾复制按钮。复制后 2 秒内图标换成勾、标签换成琥珀「已复制」。
/// 复制要在用户点按里直接完成，Safari 才允许写剪贴板。
class CopyRow extends StatefulWidget {
  const CopyRow({
    super.key,
    required this.label,
    required this.value,
    required this.tooltip,
    this.autofocus = false,
  });

  final String label;
  final String value;

  /// 复制按钮的提示，如「复制视频链接」
  final String tooltip;
  final bool autofocus;

  @override
  State<CopyRow> createState() => CopyRowStateX();
}

class CopyRowStateX extends State<CopyRow> {
  bool _copied = false;
  Timer? _reset;

  @override
  void dispose() {
    _reset?.cancel();
    super.dispose();
  }

  Future<void> _copy() async {
    await Clipboard.setData(ClipboardData(text: widget.value));
    if (!mounted) return;
    setState(() => _copied = true);
    _reset?.cancel();
    _reset = Timer(const Duration(seconds: 2), () {
      if (mounted) setState(() => _copied = false);
    });
  }

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // 复制后标签换成「已复制」，看得见，不只靠 tooltip
                Text(
                  _copied ? '已复制' : widget.label,
                  style: textTheme.labelSmall!.copyWith(
                    color: _copied ? CinemaColors.amber : null,
                  ),
                ),
                Text(
                  widget.value,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: textTheme.bodySmall!.copyWith(
                    color: CinemaColors.text1,
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          IconButton(
            autofocus: widget.autofocus,
            tooltip: _copied ? '已复制' : widget.tooltip,
            onPressed: _copy,
            icon: AnimatedSwitcher(
              duration: Motion.short,
              child: _copied
                  ? const Icon(
                      Icons.check,
                      key: ValueKey(true),
                      color: CinemaColors.amber,
                    )
                  : const Icon(Icons.copy, key: ValueKey(false)),
            ),
          ),
        ],
      ),
    );
  }
}
