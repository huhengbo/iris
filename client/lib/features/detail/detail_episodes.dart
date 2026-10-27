part of 'detail_page.dart';

/// 一季的分集。切换季时单独加载，失败只影响这一块。
/// 宽屏（含 TV）是横向的 16:9 剧照卡片片架（照搬 Plex），手机是纵向列表。
class _Episodes extends ConsumerWidget {
  const _Episodes({
    required this.itemId,
    required this.seasonId,
    required this.next,
    required this.firstSeason,
    required this.padding,
    required this.onPlay,
  });

  final int itemId;
  final int seasonId;

  /// 接着看的一集；整部看完时为空
  final EpisodeRef? next;

  /// 这一季是不是整部剧的第一季
  final bool firstSeason;

  /// 正文的左右边距；片架从这里起头，但滑到右边缘
  final EdgeInsets padding;
  final ValueChanged<Episode> onPlay;

  /// 接着看的一集标「正在看」。没开始看的剧接着看的是第一集，不标。
  bool _watching(List<Episode> episodes, int i) {
    final next = this.next;
    if (next == null || episodes[i].id != next.id) return false;
    return next.progress != null || !(firstSeason && i == 0);
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final season = ref.watch(seasonProvider(seasonId));
    final wide = MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint;
    // 分集行自带 8 的内边距，往外让 8 让缩略图和正文对齐
    final listPadding = padding.copyWith(
      left: padding.left - 8,
      right: padding.right - 8,
      top: CinemaSpacing.heading - 8,
    );
    // 改已看标记后重新加载时保留旧列表，不闪骨架
    return switch (season) {
      AsyncValue(:final value?) when wide => SliverToBoxAdapter(
        child: Padding(
          padding: EdgeInsets.only(top: CinemaSpacing.heading),
          child: _EpisodeRail(
            // 换季时重建，滚动位置回到接着看的那一集
            key: ValueKey(seasonId),
            itemId: itemId,
            seasonId: seasonId,
            episodes: value.episodes,
            watching: [
              for (var i = 0; i < value.episodes.length; i++)
                _watching(value.episodes, i),
            ],
            padding: padding,
            onPlay: onPlay,
          ),
        ),
      ),
      AsyncValue(:final value?) => SliverPadding(
        padding: listPadding,
        sliver: SliverList.builder(
          itemCount: value.episodes.length,
          itemBuilder: (context, i) => _EpisodeRow(
            itemId: itemId,
            seasonId: seasonId,
            episode: value.episodes[i],
            watching: _watching(value.episodes, i),
            onTap: () => onPlay(value.episodes[i]),
          ),
        ),
      ),
      AsyncError(:final error) => SliverPadding(
        padding: listPadding,
        sliver: SliverToBoxAdapter(
          child: ErrorRetry(
            message: ApiError.from(error).message,
            onRetry: () => ref.invalidate(seasonProvider(seasonId)),
          ),
        ),
      ),
      _ => SliverPadding(
        padding: listPadding,
        sliver: SliverList.builder(
          itemCount: 3,
          itemBuilder: (_, _) => const _EpisodeSkeleton(),
        ),
      ),
    };
  }
}

/// 宽屏的分集片架：16:9 剧照 + 「N. 片名」+ 时长 / 版本数 + 两行简介，行尾是已看切换和管理员菜单。
/// 卡片交互同海报卡（[FocusTile] + [ArtFrame]）：已看压暗 + 右上琥珀勾，接着看的一集标「正在看」。
class _EpisodeRail extends StatefulWidget {
  const _EpisodeRail({
    super.key,
    required this.itemId,
    required this.seasonId,
    required this.episodes,
    required this.watching,
    required this.padding,
    required this.onPlay,
  });

  final int itemId;
  final int seasonId;
  final List<Episode> episodes;

  /// 和 [episodes] 一一对应：这一集是不是接着看的
  final List<bool> watching;
  final EdgeInsets padding;
  final ValueChanged<Episode> onPlay;

  @override
  State<_EpisodeRail> createState() => _EpisodeRailState();
}

class _EpisodeRailState extends State<_EpisodeRail> {
  static const _cardWidth = 300.0;
  static const _gap = CinemaSpacing.heading + 4;

  // 让接着看的那一集前面露出半张，看得出还能往回滑
  late final _controller = ScrollController(
    initialScrollOffset: math.max(
      0,
      (widget.watching.indexOf(true) - 0.5) * (_cardWidth + _gap),
    ),
  );

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final scaler = MediaQuery.textScalerOf(context);
    // 剧照 + 8 + 片名行（和 40 的图标按钮取高）+ 状态行 + 两行简介，按系统字号算，放大字号不溢出
    final height =
        _cardWidth * 9 / 16 +
        8 +
        math.max(scaler.scale(22), 40) +
        scaler.scale(18) +
        scaler.scale(17) * 2 +
        8;
    return SizedBox(
      height: height,
      child: RailScroller(
        controller: _controller,
        builder: (context, controller) => ListView.separated(
          controller: controller,
          scrollDirection: Axis.horizontal,
          // 银幕阴影和聚焦放大不要被裁掉
          clipBehavior: Clip.none,
          padding: EdgeInsets.only(
            left: widget.padding.left,
            right: widget.padding.right,
          ),
          itemCount: widget.episodes.length,
          separatorBuilder: (_, _) => const SizedBox(width: _gap),
          itemBuilder: (context, i) => _EpisodeCard(
            width: _cardWidth,
            itemId: widget.itemId,
            seasonId: widget.seasonId,
            episode: widget.episodes[i],
            watching: widget.watching[i],
            onTap: () => widget.onPlay(widget.episodes[i]),
          ),
        ),
      ),
    );
  }
}

class _EpisodeCard extends StatelessWidget {
  const _EpisodeCard({
    required this.width,
    required this.itemId,
    required this.seasonId,
    required this.episode,
    required this.watching,
    required this.onTap,
  });

  final double width;
  final int itemId;
  final int seasonId;
  final Episode episode;
  final bool watching;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final progress = episode.progress;
    final title = episode.name;
    final fallback = '第 ${episode.number} 集';
    final name = title == null || title == fallback
        ? fallback
        : '${episode.number}. $title';
    final status = [
      if (progress != null)
        '剩 ${progress.remainingMinutes} 分钟'
      else if (episode.runtime case final runtime?)
        formatMinutes(runtime),
      if (episode.versions.length > 1) '${episode.versions.length} 个版本',
    ].join(' · ');
    return SizedBox(
      width: width,
      child: FocusTile(
        focusScale: 1.05,
        semanticLabel: [
          if (watching) '正在看',
          name,
          if (episode.watched) '已看',
        ].join('，'),
        onTap: onTap,
        builder: (context, highlight) => Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            AspectRatio(
              aspectRatio: 16 / 9,
              child: ArtFrame(
                highlight: highlight,
                radius: CinemaRadius.card,
                dimmed: episode.watched,
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
            Row(
              children: [
                Expanded(
                  child: Text(
                    name,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                    style: textTheme.bodyMedium!.copyWith(
                      // 看过的集退后一层
                      color: highlight.focused
                          ? CinemaColors.text0
                          : episode.watched
                          ? CinemaColors.text2
                          : CinemaColors.text1,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
                _EpisodeActions(
                  itemId: itemId,
                  seasonId: seasonId,
                  episode: episode,
                ),
              ],
            ),
            if (watching || status.isNotEmpty)
              Text.rich(
                TextSpan(
                  children: [
                    if (watching)
                      const TextSpan(
                        text: '正在看',
                        style: TextStyle(
                          color: CinemaColors.amber,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    if (watching && status.isNotEmpty)
                      const TextSpan(text: ' · '),
                    TextSpan(text: status),
                  ],
                ),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: textTheme.bodySmall,
              ),
            if (episode.overview != null)
              Text(
                episode.overview!,
                maxLines: 2,
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

class _EpisodeRow extends ConsumerStatefulWidget {
  const _EpisodeRow({
    required this.itemId,
    required this.seasonId,
    required this.episode,
    required this.watching,
    required this.onTap,
  });

  final int itemId;
  final int seasonId;
  final Episode episode;
  final bool watching;
  final VoidCallback onTap;

  @override
  ConsumerState<_EpisodeRow> createState() => _EpisodeRowState();
}

class _EpisodeRowState extends ConsumerState<_EpisodeRow> {
  bool _focused = false;

  @override
  Widget build(BuildContext context) {
    final episode = widget.episode;
    final textTheme = Theme.of(context).textTheme;
    // 和海报卡一样，琥珀环只在键盘 / 遥控器操作时出现，触屏点按不显示
    final ring =
        _focused &&
        FocusManager.instance.highlightMode == FocusHighlightMode.traditional;
    final progress = episode.progress;
    final status = [
      if (progress != null)
        '剩 ${progress.remainingMinutes} 分钟'
      else if (episode.runtime case final runtime?)
        formatMinutes(runtime),
      if (episode.versions.length > 1) '${episode.versions.length} 个版本',
    ].join(' · ');
    final fallback = '第 ${episode.number} 集';
    final title = episode.name;
    final name = title == null || title == fallback
        ? fallback
        : '${episode.number}. $title';
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Material(
        color: widget.watching ? CinemaColors.surface1 : Colors.transparent,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(CinemaRadius.card),
          side: ring
              ? const BorderSide(color: CinemaColors.amber, width: 2)
              : BorderSide.none,
        ),
        child: InkWell(
          borderRadius: BorderRadius.circular(CinemaRadius.card),
          onTap: widget.onTap,
          onFocusChange: (v) => setState(() => _focused = v),
          child: Padding(
            padding: const EdgeInsets.all(8),
            child: Row(
              children: [
                SizedBox(
                  width: 112,
                  height: 63,
                  child: ClipRRect(
                    borderRadius: BorderRadius.circular(CinemaRadius.card),
                    child: Stack(
                      fit: StackFit.expand,
                      children: [
                        ArtImage(art: episode.still),
                        if (progress != null)
                          Positioned(
                            left: 6,
                            right: 6,
                            bottom: 6,
                            child: ProgressLine(value: progress.fraction),
                          ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: textTheme.bodyMedium!.copyWith(
                          // 看过的集退后一层
                          color: episode.watched
                              ? CinemaColors.text2
                              : CinemaColors.text1,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      if (widget.watching || status.isNotEmpty)
                        Text.rich(
                          TextSpan(
                            children: [
                              if (widget.watching)
                                const TextSpan(
                                  text: '正在看',
                                  style: TextStyle(
                                    color: CinemaColors.amber,
                                    fontWeight: FontWeight.w600,
                                  ),
                                ),
                              if (widget.watching && status.isNotEmpty)
                                const TextSpan(text: ' · '),
                              TextSpan(text: status),
                            ],
                          ),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: textTheme.bodySmall,
                        ),
                      if (episode.overview != null)
                        Text(
                          episode.overview!,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          style: textTheme.labelSmall!.copyWith(
                            color: CinemaColors.text2,
                          ),
                        ),
                    ],
                  ),
                ),
                const SizedBox(width: 4),
                _EpisodeActions(
                  itemId: widget.itemId,
                  seasonId: widget.seasonId,
                  episode: episode,
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// 一集的行尾操作：已看切换，管理员另有「⋯」。分集列表行和宽屏的横向卡片共用。
class _EpisodeActions extends ConsumerStatefulWidget {
  const _EpisodeActions({
    required this.itemId,
    required this.seasonId,
    required this.episode,
  });

  final int itemId;
  final int seasonId;
  final Episode episode;

  @override
  ConsumerState<_EpisodeActions> createState() => _EpisodeActionsState();
}

class _EpisodeActionsState extends ConsumerState<_EpisodeActions> {
  bool _busy = false;

  Future<void> _toggleWatched() async {
    setState(() => _busy = true);
    await markWatched(
      context,
      ref,
      scope: WatchedScope.episode,
      id: widget.episode.id,
      watched: !widget.episode.watched,
      itemId: widget.itemId,
    );
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) {
    final episode = widget.episode;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        IconButton(
          tooltip: episode.watched ? '标记未看' : '标记已看',
          isSelected: episode.watched,
          onPressed: _busy ? () {} : _toggleWatched,
          icon: _busy
              ? const SizedBox.square(
                  dimension: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Icon(
                  Icons.check_circle_outline,
                  color: CinemaColors.text2,
                ),
          selectedIcon: _busy
              ? null
              : const Icon(Icons.check_circle, color: CinemaColors.amber),
        ),
        if (ref.watch(authControllerProvider).value?.isAdmin == true)
          PopupMenuButton<String>(
            tooltip: '管理单集',
            onSelected: (action) => switch (action) {
              'edit' => editEpisode(
                context,
                ref,
                episode: widget.episode,
                seasonId: widget.seasonId,
              ),
              'delete' => deleteEpisodeFromDetail(
                context,
                ref,
                episode: widget.episode,
                itemId: widget.itemId,
                seasonId: widget.seasonId,
              ),
              'subtitles' => showEditSubtitlesDialog(
                context,
                title: widget.episode.name ?? '第 ${widget.episode.number} 集',
                versions: widget.episode.versions,
                onChanged: () {
                  ref
                    ..invalidate(seasonProvider(widget.seasonId))
                    ..invalidate(mediaDetailProvider(widget.itemId));
                },
              ),
              _ => refreshEpisodeMetadata(
                context,
                ref,
                episodeId: widget.episode.id,
                seasonId: widget.seasonId,
              ),
            },
            // 和条目「⋯」同一个顺序：编辑、编辑字幕、刷新元数据、删除
            itemBuilder: (_) => const [
              PopupMenuItem(value: 'edit', child: Text('编辑')),
              PopupMenuItem(value: 'subtitles', child: Text('编辑字幕')),
              PopupMenuItem(value: 'refresh', child: Text('刷新元数据')),
              PopupMenuDivider(),
              PopupMenuItem(value: 'delete', child: Text('删除单集')),
            ],
          ),
      ],
    );
  }
}

class _EpisodeSkeleton extends StatelessWidget {
  const _EpisodeSkeleton();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(10),
      child: Row(
        children: [
          SizedBox(
            width: 112,
            height: 63,
            child: Skeleton(
              borderRadius: BorderRadius.circular(CinemaRadius.card),
            ),
          ),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                FractionallySizedBox(
                  widthFactor: 0.6,
                  child: SizedBox(
                    height: 14,
                    child: Skeleton(
                      borderRadius: BorderRadius.circular(CinemaRadius.tiny),
                    ),
                  ),
                ),
                const SizedBox(height: 8),
                FractionallySizedBox(
                  widthFactor: 0.3,
                  child: SizedBox(
                    height: 12,
                    child: Skeleton(
                      borderRadius: BorderRadius.circular(CinemaRadius.tiny),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
