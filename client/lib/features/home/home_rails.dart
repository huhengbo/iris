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

/// 继续观看（Web 为最近播放）：横版剧照，剧集写明播到哪一集，点开进详情接着看。
/// 看到一半的在剧照底部画进度条，并写「剩 N 分钟」。
class _RecentCard extends StatelessWidget {
  const _RecentCard({required this.recent});

  final RecentPlay recent;

  static const textLines = [20.0, 18.0];

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final media = recent.item;
    final episode = recent.episode;
    final heroTag = 'recent:${media.id}';
    final progress = recent.progress;
    final note = [
      if (episode == null) ...[
        if (media.year != null) '${media.year}',
        '电影',
      ] else
        formatEpisode(episode.seasonNumber, episode.number),
      if (progress != null) '剩 ${progress.remainingMinutes} 分钟',
    ].join(' · ');
    final art = [
      recent.still,
      media.backdrop,
      media.poster,
    ].firstWhere((a) => !a.isEmpty, orElse: () => Artwork.none);
    return FocusTile(
      semanticLabel: '${media.title}，$note',
      focusScale: 1.05,
      onTap: () => openDetail(context, media, heroTag),
      builder: (context, highlight) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          AspectRatio(
            aspectRatio: 16 / 9,
            child: ArtFrame(
              highlight: highlight,
              radius: CinemaRadius.poster,
              child: Stack(
                fit: StackFit.expand,
                children: [
                  ArtHero(
                    tag: heroTag,
                    child: ArtImage(art: art),
                  ),
                  if (progress != null)
                    Positioned(
                      left: 8,
                      right: 8,
                      bottom: 8,
                      child: ProgressLine(value: progress.fraction),
                    ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 8),
          Text(
            media.title,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: textTheme.bodySmall!.copyWith(
              color: highlight.focused
                  ? CinemaColors.text0
                  : CinemaColors.text1,
              fontWeight: highlight.focused ? FontWeight.w600 : null,
            ),
          ),
          Text(
            note,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: textTheme.labelSmall!.copyWith(color: CinemaColors.text2),
          ),
        ],
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
      child: EmptyState(
        icon: Icons.video_library_outlined,
        title: '片库里还没有影片',
        message: '扫描资料库后影片会出现在这里；家庭成员的账号需要管理员开放资料库',
      ),
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
