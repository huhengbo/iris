part of 'episode_detail_page.dart';

/// 本季其他集：横向剧照条，打开时滚到当前集。卡片和海报卡同一套交互
/// （[FocusTile] + [ArtFrame]）：已看压暗 + 右上琥珀勾，当前这一集标「当前」。
class _SeasonStrip extends StatefulWidget {
  const _SeasonStrip({
    required this.season,
    required this.current,
    required this.padding,
    required this.onTap,
  });

  final SeasonDetail season;
  final int current;
  final EdgeInsets padding;
  final ValueChanged<Episode> onTap;

  @override
  State<_SeasonStrip> createState() => _SeasonStripState();
}

class _SeasonStripState extends State<_SeasonStrip> {
  static const _width = 200.0;
  static const _gap = CinemaSpacing.heading;

  // 让当前集前面露出半张，看得出还能往回滑
  late final _controller = ScrollController(
    initialScrollOffset: math.max(0, (widget.current - 0.5) * (_width + _gap)),
  );

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final scaler = MediaQuery.textScalerOf(context);
    final episodes = widget.season.episodes;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: widget.padding.copyWith(
            top: CinemaSpacing.section,
            bottom: CinemaSpacing.heading,
          ),
          child: Semantics(
            header: true,
            child: Text(
              '${seasonLabel(widget.season.number)} · 共 ${episodes.length} 集',
              style: textTheme.titleMedium!.copyWith(color: CinemaColors.text0),
            ),
          ),
        ),
        SizedBox(
          // 剧照 + 8 + 两行字，按系统字号算，放大字号不溢出
          height: _width * 9 / 16 + 8 + scaler.scale(18) + scaler.scale(20) + 4,
          child: RailScroller(
            controller: _controller,
            builder: (context, controller) => ListView.separated(
              controller: controller,
              scrollDirection: Axis.horizontal,
              // 银幕阴影和聚焦放大不要被裁掉
              clipBehavior: Clip.none,
              padding: EdgeInsets.only(
                left: widget.padding.left,
                right: widget.padding.left,
              ),
              itemCount: episodes.length,
              separatorBuilder: (_, _) => const SizedBox(width: _gap),
              itemBuilder: (context, i) => _StripCard(
                episode: episodes[i],
                width: _width,
                current: i == widget.current,
                onTap: () => widget.onTap(episodes[i]),
              ),
            ),
          ),
        ),
      ],
    );
  }
}

class _StripCard extends StatelessWidget {
  const _StripCard({
    required this.episode,
    required this.width,
    required this.current,
    required this.onTap,
  });

  final Episode episode;
  final double width;
  final bool current;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final progress = episode.progress;
    final name = episode.name;
    final number = '第 ${episode.number} 集';
    return SizedBox(
      width: width,
      child: FocusTile(
        focusScale: 1.05,
        semanticLabel: [
          if (current) '当前',
          number,
          if (name != null && name.isNotEmpty) name,
          if (episode.watched) '已看',
        ].join('，'),
        // 已经在这一集了，点了不用再跳
        onTap: current ? () {} : onTap,
        builder: (context, highlight) => Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            AspectRatio(
              aspectRatio: 16 / 9,
              child: ArtFrame(
                highlight: highlight,
                radius: CinemaRadius.card,
                dimmed: episode.watched && !current,
                badge: episode.watched ? const WatchedBadge() : null,
                child: Stack(
                  fit: StackFit.expand,
                  children: [
                    ArtImage(art: episode.still),
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
              current ? '当前 · $number' : number,
              style: textTheme.labelSmall!.copyWith(
                color: current ? CinemaColors.text0 : CinemaColors.text2,
                fontWeight: current ? FontWeight.w600 : null,
              ),
            ),
            if (name != null && name.isNotEmpty)
              Text(
                name,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: textTheme.bodySmall!.copyWith(
                  color: highlight.focused || current
                      ? CinemaColors.text0
                      : episode.watched
                      ? CinemaColors.text2
                      : CinemaColors.text1,
                  fontWeight: FontWeight.w600,
                ),
              ),
          ],
        ),
      ),
    );
  }
}
