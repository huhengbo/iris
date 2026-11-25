part of 'home_page.dart';

class _SectionHeader extends StatelessWidget {
  const _SectionHeader({
    required this.title,
    required this.metrics,
    required this.afterRail,
    this.onAll,
  });

  final String title;
  final _Metrics metrics;

  /// 上一块是片架：它下方的留白算进区块间距
  final bool afterRail;

  /// 为空时不显示「全部」
  final VoidCallback? onAll;

  @override
  Widget build(BuildContext context) {
    final safe = MediaQuery.paddingOf(context);
    return SliverPadding(
      padding: EdgeInsets.fromLTRB(
        metrics.gutter + safe.left,
        CinemaSpacing.section -
            _Metrics.headerSlack -
            (afterRail ? _Metrics.railBottom : 0),
        metrics.gutter - 8 + safe.right,
        0,
      ),
      sliver: SliverToBoxAdapter(
        // 没有「全部」按钮时也保持同样的行高
        child: ConstrainedBox(
          constraints: const BoxConstraints(minHeight: _Metrics.headerHeight),
          child: Row(
            children: [
              Expanded(
                child: Semantics(
                  header: true,
                  child: Text(
                    title,
                    style: Theme.of(context).textTheme.titleMedium!
                        .copyWith(color: CinemaColors.text0),
                  ),
                ),
              ),
              if (onAll != null)
                TextButton(
                  onPressed: onAll,
                  style: TextButton.styleFrom(
                    padding: const EdgeInsets.symmetric(horizontal: 8),
                    textStyle: Theme.of(context).textTheme.labelMedium,
                  ),
                  child: const Row(
                    children: [Text('全部'), Icon(Icons.chevron_right, size: 18)],
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

/// 横向片架。上下留出空间给聚焦放大和阴影，不裁切。
class _Rail extends StatelessWidget {
  const _Rail({
    required this.metrics,
    required this.height,
    required this.children,
  });

  final _Metrics metrics;
  final double height;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final safe = MediaQuery.paddingOf(context);
    return SizedBox(
      height: height,
      child: RailScroller(
        builder: (context, controller) => ListView.separated(
          controller: controller,
          scrollDirection: Axis.horizontal,
          clipBehavior: Clip.none,
          // 首尾停在安全区内（TV 过扫描），中间照样滑到屏幕边
          padding: EdgeInsets.fromLTRB(
            metrics.gutter + safe.left,
            _Metrics.railTop,
            metrics.gutter + safe.right,
            _Metrics.railBottom,
          ),
          itemCount: children.length,
          separatorBuilder: (_, _) => SizedBox(width: metrics.railGap),
          itemBuilder: (_, i) => children[i],
        ),
      ),
    );
  }
}

class _EmptyHome extends StatelessWidget {
  const _EmptyHome();

  @override
  Widget build(BuildContext context) {
    return const SafeArea(
      child: EmptyState(icon: Icons.video_library_outlined, title: '片库里还没有影片'),
    );
  }
}

class _HomeSkeleton extends StatelessWidget {
  const _HomeSkeleton();

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final m = _Metrics(constraints.maxWidth);
        Widget bar(double width) => Padding(
          padding: EdgeInsets.fromLTRB(
            m.gutter,
            CinemaSpacing.section,
            0,
            CinemaSpacing.heading,
          ),
          child: SizedBox(
            width: width,
            height: 16,
            child: Skeleton(
              borderRadius: BorderRadius.circular(CinemaRadius.tiny),
            ),
          ),
        );
        Widget row(double width, double height, double radius) => SizedBox(
          height: height,
          child: ListView.separated(
            scrollDirection: Axis.horizontal,
            physics: const NeverScrollableScrollPhysics(),
            padding: EdgeInsets.symmetric(horizontal: m.gutter),
            itemCount: 6,
            separatorBuilder: (_, _) => SizedBox(width: m.railGap),
            itemBuilder: (_, _) => SizedBox(
              width: width,
              child: Skeleton(borderRadius: BorderRadius.circular(radius)),
            ),
          ),
        );
        return Semantics(
          label: '正在加载',
          child: ListView(
            physics: const NeverScrollableScrollPhysics(),
            padding: EdgeInsets.zero,
            children: [
              bar(120),
              row(m.posterWidth, m.posterWidth * 3 / 2, CinemaRadius.poster),
              bar(96),
              row(m.posterWidth, m.posterWidth * 3 / 2, CinemaRadius.poster),
            ],
          ),
        );
      },
    );
  }
}
