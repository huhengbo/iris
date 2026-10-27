import 'package:flutter/material.dart';

import '../../app/shell.dart';
import '../../app/tv.dart';
import '../../app/theme.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/focus_tile.dart';
import 'art_image.dart';
import 'browse_api.dart';

/// 资料库「分类」标签：类型卡片墙，点一张切到「资料库」标签并带上类型筛选。
class LibraryCategoriesTab extends StatelessWidget {
  const LibraryCategoriesTab({
    super.key,
    required this.detail,
    required this.onOpenGenre,
  });

  final LibraryDetail detail;
  final ValueChanged<String> onOpenGenre;

  @override
  Widget build(BuildContext context) {
    final categories = detail.categories;
    if (categories.isEmpty) {
      return const EmptyState(
        icon: Icons.category_outlined,
        title: '还没有分类',
        message: '条目带上类型后，会按类型出现在这里',
      );
    }
    final wide = MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint;
    final gutter = CinemaSpacing.gutterFor(wide: wide);
    return LayoutBuilder(
      builder: (context, constraints) {
        final columns = constraints.maxWidth >= 1000
            ? 5
            : constraints.maxWidth >= 700
            ? 3
            : 2;
        return GridView.builder(
          padding: EdgeInsets.fromLTRB(gutter, 16, gutter, 32),
          gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
            crossAxisCount: columns,
            mainAxisSpacing: 12,
            crossAxisSpacing: 12,
            childAspectRatio: 16 / 10,
          ),
          itemCount: categories.length,
          itemBuilder: (context, index) => _CategoryTile(
            category: categories[index],
            autofocus: index == 0 && isTelevision(context),
            onTap: () => onOpenGenre(categories[index].name),
          ),
        );
      },
    );
  }
}

class _CategoryTile extends StatelessWidget {
  const _CategoryTile({
    required this.category,
    required this.autofocus,
    required this.onTap,
  });

  final CategoryCard category;
  final bool autofocus;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return FocusTile(
      autofocus: autofocus,
      semanticLabel: '${category.name}，${category.itemCount} 部',
      onTap: onTap,
      builder: (context, highlight) => ClipRRect(
        borderRadius: BorderRadius.circular(CinemaRadius.card),
        child: Stack(
          fit: StackFit.expand,
          children: [
            ArtImage(art: category.image, icon: Icons.category_outlined),
            // 图上的文字要有底，亮剧照上也读得清
            const DecoratedBox(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [Colors.transparent, CinemaColors.scrim],
                ),
              ),
            ),
            Positioned(
              left: 14,
              right: 14,
              bottom: 12,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    category.name,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: Theme.of(context).textTheme.titleMedium!.copyWith(
                      color: highlight.focused
                          ? CinemaColors.amber
                          : CinemaColors.text0,
                    ),
                  ),
                  Text(
                    '${category.itemCount} 部',
                    style: Theme.of(context).textTheme.bodySmall!
                        .copyWith(color: CinemaColors.text1),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
