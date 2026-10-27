part of 'pending_page.dart';

class _FilesTab extends ConsumerStatefulWidget {
  const _FilesTab({required this.status});

  /// unplaced：没有归入条目；ignored：被识别为附加内容
  final String status;

  @override
  ConsumerState<_FilesTab> createState() => _FilesTabState();
}

class _FilesTabState extends ConsumerState<_FilesTab>
    with _PagedTab<_FilesTab> {
  final Set<int> selected = {};
  bool batchBusy = false;

  /// 按原因筛选；为空表示全部
  String? reason;

  Future<void> _pick(PendingFile file) async {
    final item = file.item;
    final pick = await pickMetadata(
      context,
      title: '指定「${file.name}」',
      kind: file.kind,
      libraryId: file.libraryId,
      initialQuery: item?.title ?? '',
      suggestions: [?item?.candidate],
      needEpisode: file.kind == 'series',
    );
    if (pick == null || !mounted) return;
    await runBusy(
      file.id,
      () => _assign(
        context,
        ref,
        pick,
        () => assignFile(
          ref.read(apiClientProvider),
          file.id,
          pick.remoteId,
          providerId: pick.providerId,
          season: pick.season,
          episode: pick.episode,
        ),
      ),
    );
  }

  Future<void> _pickBatch(List<PendingFile> files) async {
    if (files.isEmpty) return;
    final kinds = files.map((file) => file.kind).toSet();
    if (kinds.length != 1) {
      showSnack(context, '批量指定需要选择相同类型的文件');
      return;
    }
    final first = files.first;
    final pick = await pickMetadata(
      context,
      title: '批量指定 ${files.length} 个文件',
      kind: first.kind,
      libraryId: first.libraryId,
      initialQuery: first.item?.title ?? '',
      suggestions: [?first.item?.candidate],
      needEpisode: first.kind == 'series',
    );
    if (pick == null || !mounted) return;
    setState(() => batchBusy = true);
    try {
      final report = await assignFilesBatch(
        ref.read(apiClientProvider),
        files.map((file) => file.id).toList(),
        pick.remoteId,
        providerId: pick.providerId,
        season: pick.season,
        episode: pick.episode,
      );
      _invalidatePending(ref);
      if (mounted) {
        setState(() {
          batchBusy = false;
          selected
            ..clear()
            ..addAll(
              report.results
                  .where((result) => !result.succeeded)
                  .map((result) => result.id),
            );
        });
        _showBatchReport(context, report, action: '批量指定');
      }
    } catch (e) {
      if (mounted) {
        setState(() => batchBusy = false);
        showApiErrorSnack(context, e);
      }
    }
  }

  Future<void> _restore(PendingFile file) async {
    await runBusy(file.id, () async {
      try {
        await restoreFile(ref.read(apiClientProvider), file.id);
        _invalidatePending(ref);
        if (mounted) showSnack(context, '已恢复「${file.name}」，请在未归入列表中继续处理');
      } catch (error) {
        if (mounted) showApiErrorSnack(context, error);
      }
    });
  }

  Future<void> _delete(PendingFile file) async {
    final confirmed = await _confirmDelete(
      context,
      '删除「${file.name}」？',
      '会删除来源上的「${file.path}」，操作不可恢复。',
    );
    if (!confirmed || !mounted) return;
    await runBusy(
      file.id,
      () => _deletePending(context, mediaFileIds: [file.id]),
    );
  }

  Future<void> _deleteBatch(List<int> ids) async {
    if (ids.isEmpty) return;
    final confirmed = await _confirmDelete(
      context,
      '删除 ${ids.length} 个视频？',
      '会从来源上删除选中的视频，操作不可恢复。',
    );
    if (!confirmed || !mounted) return;
    setState(() => batchBusy = true);
    await _deletePending(context, mediaFileIds: ids);
    if (mounted) {
      setState(() {
        batchBusy = false;
        selected.removeAll(ids);
      });
    }
  }

  Future<void> _stateBatch(
    List<PendingFile> files, {
    required bool restore,
  }) async {
    if (files.isEmpty) return;
    setState(() => batchBusy = true);
    try {
      final ids = files.map((file) => file.id).toList();
      final report = restore
          ? await restoreFilesBatch(ref.read(apiClientProvider), ids)
          : await ignoreFilesBatch(ref.read(apiClientProvider), ids);
      _invalidatePending(ref);
      if (mounted) {
        setState(() {
          batchBusy = false;
          selected
            ..clear()
            ..addAll(
              report.results
                  .where((result) => !result.succeeded)
                  .map((result) => result.id),
            );
        });
        _showBatchReport(context, report, action: restore ? '批量恢复' : '批量忽略');
      }
    } catch (error) {
      if (mounted) {
        setState(() => batchBusy = false);
        showApiErrorSnack(context, error);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final ignored = widget.status == 'ignored';
    final provider = pendingFilesProvider((
      status: widget.status,
      reason: reason,
      offset: offset,
    ));
    return AsyncView(
      value: ref.watch(provider),
      onRetry: () => ref.invalidate(provider),
      builder: (page) {
        backIfEmpty(page.items.isEmpty);
        if (page.total == 0 && reason == null) {
          return EmptyState(
            icon: Icons.check_circle_outline,
            title: ignored ? '没有被忽略的视频' : '没有未归入的视频',
            message: ignored
                ? '预告片、花絮等附加内容不入库，会列在这里；误判的可以恢复。'
                : '无法识别季集号、或从条目中拆出的视频会列在这里。',
          );
        }
        final muted = Theme.of(context).textTheme.bodySmall
            ?.copyWith(color: CinemaColors.text2);
        final pageIds = page.items.map((file) => file.id).toSet();
        final allSelected =
            pageIds.isNotEmpty && pageIds.every(selected.contains);
        return Column(
          children: [
            if (page.reasons.length > 1 || reason != null)
              _ReasonFilter(
                reasons: page.reasons,
                selected: reason,
                onChanged: (value) => setState(() {
                  reason = value;
                  offset = 0;
                  selected.clear();
                }),
              ),
            if (page.total == 0)
              const Expanded(
                child: EmptyState(
                  icon: Icons.filter_alt_off_outlined,
                  title: '没有符合该原因的视频',
                  message: '换一个原因，或选择「全部」。',
                ),
              ),
            if (page.total > 0 && selected.isNotEmpty)
              _BatchToolbar(
                count: selected.length,
                allSelected: allSelected,
                busy: batchBusy,
                onSelectAll: () => setState(() {
                  if (allSelected) {
                    selected.removeAll(pageIds);
                  } else {
                    selected.addAll(pageIds);
                  }
                }),
                onClear: () => setState(selected.clear),
                onAssign: () => ignored
                    ? _stateBatch(
                        page.items
                            .where((file) => selected.contains(file.id))
                            .toList(),
                        restore: true,
                      )
                    : _pickBatch(
                        page.items
                            .where((file) => selected.contains(file.id))
                            .toList(),
                      ),
                assignLabel: ignored ? '批量恢复' : '批量指定',
                onSecondary: ignored
                    ? null
                    : () => _stateBatch(
                        page.items
                            .where((file) => selected.contains(file.id))
                            .toList(),
                        restore: false,
                      ),
                secondaryLabel: ignored ? null : '批量忽略',
                onDelete: () =>
                    _deleteBatch(pageIds.where(selected.contains).toList()),
              ),
            if (page.total > 0)
              Expanded(
                child: ListView(
                  padding: const EdgeInsets.only(bottom: 24),
                  children: [
                    for (final file in page.items)
                      _PendingRow(
                        title: file.name,
                        lines: [
                          Text(file.path, style: muted),
                          Text(
                            '${file.libraryName} · ${formatSize(file.size)}'
                            '${file.item == null ? '' : ' · 所在作品：${file.item!.title}'}',
                            style: muted,
                          ),
                          Text(file.reason),
                        ],
                        action: ignored ? '恢复' : '指定',
                        busy: batchBusy || busy.contains(file.id),
                        selected: selected.contains(file.id),
                        onSelected: (value) => setState(() {
                          value
                              ? selected.add(file.id)
                              : selected.remove(file.id);
                        }),
                        onAction: () => ignored ? _restore(file) : _pick(file),
                        onDelete: () => _delete(file),
                      ),
                    Pager(
                      offset: offset,
                      total: page.total,
                      onChanged: (value) => setState(() {
                        selected.clear();
                        offset = value;
                      }),
                    ),
                  ],
                ),
              ),
          ],
        );
      },
    );
  }
}

/// 按原因筛选的单选标签；「全部」清除筛选
class _ReasonFilter extends StatelessWidget {
  const _ReasonFilter({
    required this.reasons,
    required this.selected,
    required this.onChanged,
  });

  final List<String> reasons;
  final String? selected;
  final ValueChanged<String?> onChanged;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
      child: Align(
        alignment: Alignment.centerLeft,
        child: Wrap(
          spacing: 8,
          runSpacing: 4,
          children: [
            ChoiceChip(
              label: const Text('全部'),
              selected: selected == null,
              onSelected: (_) => onChanged(null),
            ),
            for (final reason in reasons)
              ChoiceChip(
                label: Text(reason),
                selected: selected == reason,
                onSelected: (_) => onChanged(reason),
              ),
          ],
        ),
      ),
    );
  }
}
