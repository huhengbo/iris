import 'package:flutter/material.dart';

import '../../app/theme.dart';

/// 分组列表的节标题：text2 小标题，读屏当作标题。管理首页、个人设置、
/// 管理页里的各节都用它，见 DESIGN.md「个人设置」「管理首页」。
///
/// 默认自带左右 gutter，给直接放在铺满的 ListView 里、下面是 ListTile 的
/// 情况用；内容已经让出 gutter 时传 `inset: false`。
class SectionTitle extends StatelessWidget {
  const SectionTitle(
    this.title, {
    super.key,
    this.first = false,
    this.inset = true,
  });

  final String title;

  /// 页面里的第一节，上方不留区块间距
  final bool first;
  final bool inset;

  @override
  Widget build(BuildContext context) {
    final side = inset ? CinemaSpacing.gutter : 0.0;
    return Padding(
      padding: EdgeInsets.fromLTRB(
        side,
        first ? 8 : CinemaSpacing.section,
        side,
        4,
      ),
      child: Semantics(
        header: true,
        child: Text(
          title,
          style: Theme.of(context).textTheme.labelLarge!
              .copyWith(color: CinemaColors.text2),
        ),
      ),
    );
  }
}
