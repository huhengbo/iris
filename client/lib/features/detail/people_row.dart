import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../app/shell.dart' show wideLayoutBreakpoint;
import '../../app/theme.dart';
import '../../core/widgets/rail_scroller.dart';
import '../../core/widgets/focus_tile.dart';
import '../library/art_image.dart';
import '../library/browse_api.dart';

const _avatar = 64.0;
const _wideAvatar = 88.0;

/// 演职人员：导演、编剧在前，横向滑动，点开看这个人的其他作品。
class PeopleRow extends StatelessWidget {
  const PeopleRow({
    super.key,
    required this.directors,
    this.writers = const [],
    required this.cast,
    required this.padding,
  });

  final List<Credit> directors;
  final List<Credit> writers;
  final List<Credit> cast;

  /// 和正文对齐的左右边距
  final EdgeInsets padding;

  @override
  Widget build(BuildContext context) {
    final people = [
      for (final p in directors) (p, '导演'),
      for (final p in writers) (p, '编剧'),
      for (final p in cast) (p, p.character),
    ];
    final scaler = MediaQuery.textScalerOf(context);
    // 宽屏头像放大，片架才不显得空
    final avatar = MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint
        ? _wideAvatar
        : _avatar;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: padding.copyWith(
            top: CinemaSpacing.section,
            bottom: CinemaSpacing.heading,
          ),
          child: Semantics(
            header: true,
            child: Text(
              '演职人员',
              style: Theme.of(context).textTheme.titleMedium!
                  .copyWith(color: CinemaColors.text0),
            ),
          ),
        ),
        SizedBox(
          // 头像 + 姓名和角色两行，上下给聚焦放大留一点余量
          height: avatar + 8 + scaler.scale(18) + scaler.scale(16) + 16,
          child: RailScroller(
            builder: (context, controller) => ListView.separated(
              controller: controller,
              scrollDirection: Axis.horizontal,
              clipBehavior: Clip.none,
              padding: EdgeInsets.only(left: padding.left, right: padding.left),
              itemCount: people.length,
              separatorBuilder: (_, _) =>
                  const SizedBox(width: CinemaSpacing.heading),
              itemBuilder: (context, i) => _PersonChip(
                person: people[i].$1,
                role: people[i].$2,
                avatar: avatar,
              ),
            ),
          ),
        ),
      ],
    );
  }
}

/// 一位演职人员：64 圆形头像 + 姓名 + 角色。交互同海报卡（[FocusTile]）：
/// 聚焦时头像外一圈琥珀环并放大，姓名加粗。
class _PersonChip extends StatelessWidget {
  const _PersonChip({
    required this.person,
    required this.role,
    required this.avatar,
  });

  final Credit person;
  final String? role;
  final double avatar;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final role = this.role;
    return SizedBox(
      width: avatar + 12,
      child: FocusTile(
        semanticLabel: [person.name, ?role].join('，'),
        onTap: () => context.push('/person/${person.personId}'),
        builder: (context, highlight) => Column(
          children: [
            Container(
              width: avatar,
              height: avatar,
              foregroundDecoration: BoxDecoration(
                shape: BoxShape.circle,
                border: Border.all(
                  color: highlight.focused
                      ? CinemaColors.amber
                      : Colors.transparent,
                  width: 2,
                  strokeAlign: BorderSide.strokeAlignOutside,
                ),
              ),
              child: ClipOval(
                child: ArtImage(
                  art: person.profile,
                  icon: Icons.person_outline,
                ),
              ),
            ),
            const SizedBox(height: 8),
            Text(
              person.name,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: textTheme.bodySmall!.copyWith(
                color: CinemaColors.text0,
                fontWeight: highlight.focused ? FontWeight.w600 : null,
              ),
            ),
            if (role != null)
              Text(
                role,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: textTheme.labelSmall!.copyWith(
                  color: CinemaColors.text2,
                ),
              ),
          ],
        ),
      ),
    );
  }
}
