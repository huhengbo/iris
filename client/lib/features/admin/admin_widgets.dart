import 'package:flutter/material.dart';

import 'catalog_api.dart';

/// 管理页内容的最大宽度，和设置页、同步页的表单一致。
const adminContentWidth = 640.0;

/// 设置页里卡片网格（插件）用的更宽的内容宽度，够排 3 列。
const adminGridWidth = 960.0;

/// 管理页的列表：宽屏上内容靠左、最大宽 [maxWidth]，和页面标题左对齐（照搬 Plex，
/// 不居中，右边留白）。用内边距实现，滚动区域仍占满整页，鼠标在右侧空白处也能滚动。
class AdminListWidth extends StatelessWidget {
  const AdminListWidth({
    super.key,
    required this.builder,
    this.top = 0,
    this.bottom = 24,
    this.maxWidth = adminContentWidth,
  });

  final Widget Function(EdgeInsets padding) builder;
  final double top;
  final double bottom;
  final double maxWidth;

  @override
  Widget build(BuildContext context) => LayoutBuilder(
    builder: (context, constraints) {
      final width = constraints.maxWidth;
      final right = width > maxWidth ? width - maxWidth : 0.0;
      return builder(EdgeInsets.fromLTRB(0, top, right, bottom));
    },
  );
}

/// 列表底部的翻页：上一页、页码、下一页。只有一页时不显示。
class Pager extends StatelessWidget {
  const Pager({
    super.key,
    required this.offset,
    required this.total,
    required this.onChanged,
  });

  final int offset;
  final int total;
  final ValueChanged<int> onChanged;

  @override
  Widget build(BuildContext context) {
    if (total <= adminPageSize) return const SizedBox.shrink();
    final page = offset ~/ adminPageSize + 1;
    final pages = (total + adminPageSize - 1) ~/ adminPageSize;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 12),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          IconButton(
            tooltip: '上一页',
            onPressed: page > 1
                ? () => onChanged(offset - adminPageSize)
                : null,
            icon: const Icon(Icons.chevron_left),
          ),
          const SizedBox(width: 8),
          Text('第 $page / $pages 页 · 共 $total 项'),
          const SizedBox(width: 8),
          IconButton(
            tooltip: '下一页',
            onPressed: page < pages
                ? () => onChanged(offset + adminPageSize)
                : null,
            icon: const Icon(Icons.chevron_right),
          ),
        ],
      ),
    );
  }
}
