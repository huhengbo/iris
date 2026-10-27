part of 'detail_page.dart';

class _SeasonBar extends ConsumerStatefulWidget {
  const _SeasonBar({
    required this.itemId,
    required this.seasons,
    required this.selected,
    required this.onSelected,
  });

  final int itemId;
  final List<SeasonSummary> seasons;
  final SeasonSummary selected;
  final ValueChanged<SeasonSummary> onSelected;

  @override
  ConsumerState<_SeasonBar> createState() => _SeasonBarState();
}

class _SeasonBarState extends ConsumerState<_SeasonBar> {
  bool _busy = false;

  /// 整季标记：改完前菜单换成转圈，挡住重复操作；成功后提示一句。
  Future<void> _markSeason(bool watched) async {
    final season = widget.selected;
    setState(() => _busy = true);
    final ok = await markWatched(
      context,
      ref,
      scope: WatchedScope.season,
      id: season.id,
      watched: watched,
      itemId: widget.itemId,
    );
    if (!mounted) return;
    setState(() => _busy = false);
    if (ok) showSnack(context, '${season.label}已标记为${watched ? '已看' : '未看'}');
  }

  Future<void> _editSeason() async {
    final season = widget.selected;
    final name = await showTextPrompt(
      context,
      title: '编辑${season.label}',
      label: '标题',
      initial: season.name ?? '',
      action: '保存',
    );
    if (name == null || name.isEmpty || !mounted) return;
    await _manage(
      () => patchSeasonMetadata(ref.read(apiClientProvider), season.id, {
        'name': name,
      }),
      done: '已保存',
    );
  }

  Future<void> _refreshSeason() => _manage(
    () => refreshSeason(ref.read(apiClientProvider), widget.selected.id),
    done: '已刷新${widget.selected.label}元数据',
  );

  Future<void> _deleteSeason() async {
    final season = widget.selected;
    final confirmed = await showConfirmDialog(
      context,
      title: '删除${season.label}？',
      body: '会删除这一季对应的视频和字幕文件，并从资料库移除。此操作不可恢复。',
      action: '删除',
      destructive: true,
    );
    if (!confirmed || !mounted) return;
    await _manage(
      () => deleteSeason(ref.read(apiClientProvider), season.id),
      done: '删除${season.label}任务已提交',
    );
  }

  /// 管理操作：进行中菜单换成转圈，完成后刷新详情和这一季并提示，失败时提示原因。
  Future<void> _manage(
    Future<void> Function() call, {
    required String done,
  }) async {
    final seasonId = widget.selected.id;
    setState(() => _busy = true);
    String message;
    try {
      await call();
      ref
        ..invalidate(mediaDetailProvider(widget.itemId))
        ..invalidate(seasonProvider(seasonId));
      message = done;
    } catch (error) {
      message = ApiError.from(error).message;
    }
    if (!mounted) return;
    setState(() => _busy = false);
    showSnack(context, message);
  }

  @override
  Widget build(BuildContext context) {
    final seasons = widget.seasons;
    final selected = widget.selected;
    final textTheme = Theme.of(context).textTheme;
    final title = Text(
      selected.label,
      style: textTheme.titleMedium!.copyWith(color: CinemaColors.text0),
    );
    return Row(
      children: [
        if (seasons.length == 1)
          title
        else
          FocusRing(
            radius: CinemaRadius.small,
            child: PopupMenuButton<SeasonSummary>(
              tooltip: '选择季',
              initialValue: selected,
              onSelected: widget.onSelected,
              itemBuilder: (_) => [
                for (final s in seasons)
                  PopupMenuItem(
                    value: s,
                    child: Text('${s.label}（${s.episodeCount} 集）'),
                  ),
              ],
              child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    title,
                    const Icon(Icons.expand_more, color: CinemaColors.text2),
                  ],
                ),
              ),
            ),
          ),
        const Spacer(),
        Text('${selected.episodeCount} 集', style: textTheme.bodySmall),
        PopupMenuButton<String>(
          tooltip: '季操作',
          enabled: !_busy,
          icon: _busy
              ? const SizedBox.square(
                  dimension: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Icon(Icons.more_horiz, color: CinemaColors.text2),
          onSelected: (action) => switch (action) {
            'watched' => _markSeason(true),
            'unwatched' => _markSeason(false),
            'edit' => _editSeason(),
            'refresh' => _refreshSeason(),
            _ => _deleteSeason(),
          },
          itemBuilder: (_) => [
            const PopupMenuItem(value: 'watched', child: Text('整季标记已看')),
            const PopupMenuItem(value: 'unwatched', child: Text('整季标记未看')),
            // 和条目「⋯」同一个顺序，只留这一层适用的项
            if (ref.watch(authControllerProvider).value?.isAdmin == true) ...[
              const PopupMenuDivider(),
              const PopupMenuItem(value: 'edit', child: Text('编辑季')),
              const PopupMenuItem(value: 'refresh', child: Text('刷新季元数据')),
              const PopupMenuDivider(),
              const PopupMenuItem(value: 'delete', child: Text('删除整季')),
            ],
          ],
        ),
      ],
    );
  }
}
