part of 'sync_page.dart';

/// 定时同步，平时设好就不用动，默认收起，标题下概括当前设置。
class _AutoSyncSection extends ConsumerWidget {
  const _AutoSyncSection();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final text = Theme.of(context).textTheme;
    final times = ref.watch(syncScheduleProvider).value?.times;
    final summary = switch (times) {
      null => null,
      [] => '未设置定时同步',
      final times => '每天 ${times.join('、')} 同步',
    };
    return FocusRing(
      child: ExpansionTile(
        tilePadding: EdgeInsets.zero,
        childrenPadding: const EdgeInsets.only(top: 8),
        // 去掉展开时上下的分隔线，和页面其他分节一致
        shape: const Border(),
        collapsedShape: const Border(),
        expandedCrossAxisAlignment: CrossAxisAlignment.stretch,
        // 和页面其他节标题同一级：text2 小标题
        title: Semantics(
          header: true,
          child: Text(
            '自动同步',
            style: text.labelLarge?.copyWith(color: CinemaColors.text2),
          ),
        ),
        subtitle: summary == null
            ? null
            : Text(
                summary,
                style: text.bodySmall?.copyWith(color: CinemaColors.text2),
              ),
        children: const [_ScheduleSection()],
      ),
    );
  }
}

class _ScheduleSection extends ConsumerStatefulWidget {
  const _ScheduleSection();

  @override
  ConsumerState<_ScheduleSection> createState() => _ScheduleSectionState();
}

class _ScheduleSectionState extends ConsumerState<_ScheduleSection> {
  bool _saving = false;

  Future<void> _save(List<String> times) async {
    if (_saving) return;
    setState(() => _saving = true);
    try {
      await saveSyncSchedule(ref.read(apiClientProvider), times);
      final _ = await ref.refresh(syncScheduleProvider.future);
    } catch (e) {
      if (mounted) showApiErrorSnack(context, e);
    }
    if (mounted) setState(() => _saving = false);
  }

  Future<void> _add(SyncScheduleConfig config) async {
    final picked = await showTimePicker(
      context: context,
      initialTime: const TimeOfDay(hour: 3, minute: 0),
      helpText: '每天在这个时间同步（${config.timezone}）',
      builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context).copyWith(alwaysUse24HourFormat: true),
        child: child!,
      ),
    );
    if (picked == null) return;
    final time = _hhmm(picked);
    if (config.times.contains(time)) return;
    await _save([...config.times, time]);
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final muted = text.bodySmall?.copyWith(color: CinemaColors.text2);
    final schedule = ref.watch(syncScheduleProvider);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text('定时同步', style: text.titleSmall),
        const SizedBox(height: CinemaSpacing.heading),
        switch (schedule) {
          AsyncData(value: final config) => Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final time in config.times)
                    InputChip(
                      label: Text(time),
                      onDeleted: _saving
                          ? null
                          : () => _save([
                              for (final t in config.times)
                                if (t != time) t,
                            ]),
                      deleteButtonTooltipMessage: '删除 $time',
                    ),
                  ActionChip(
                    avatar: const Icon(Icons.add, size: 18),
                    label: const Text('添加时间'),
                    onPressed: _saving ? null : () => _add(config),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              Text(
                config.times.isEmpty
                    ? '未设置，只在手动触发时同步。'
                    : '每天按 ${config.timezone} 时区同步全部资料库'
                          '${config.nextRunAt == null ? '' : '，下次 ${formatDateTime(config.nextRunAt!)}'}。'
                          '服务停机错过的时间点，启动后补跑一次。',
                style: muted,
              ),
            ],
          ),
          AsyncError(:final error) => ErrorRetry(
            message: ApiError.from(error).message,
            onRetry: () => ref.invalidate(syncScheduleProvider),
          ),
          _ => const _Loading(),
        },
      ],
    );
  }
}

/// 成批失效的文件没有自动删除，在这里逐个资料库确认。没有时不占位置。
class _HeldDeletions extends ConsumerStatefulWidget {
  const _HeldDeletions();

  @override
  ConsumerState<_HeldDeletions> createState() => _HeldDeletionsState();
}

class _HeldDeletionsState extends ConsumerState<_HeldDeletions> {
  int? _confirming;

  Future<void> _confirm(HeldDeletion held) async {
    if (_confirming != null) return;
    final ok = await showConfirmDialog(
      context,
      title: '删除「${held.libraryName}」里 ${held.due} 个失效的视频？',
      body:
          '这些文件在来源上已经找不到，超过了保留天数。确认后从库里删除它们的条目和观看记录，'
          '来源上的文件不受影响。如果只是目录被误移走，请先移回原处再同步。',
      action: '删除',
      destructive: true,
    );
    if (!ok || !mounted) return;
    setState(() => _confirming = held.libraryId);
    try {
      final deleted = await confirmHeldDeletion(
        ref.read(apiClientProvider),
        held.libraryId,
      );
      if (mounted) showSnack(context, '已删除 $deleted 个视频');
      ref.invalidate(adminLibrariesProvider);
    } catch (e) {
      if (mounted) showApiErrorSnack(context, e);
    }
    ref.invalidate(heldDeletionsProvider);
    if (mounted) setState(() => _confirming = null);
  }

  @override
  Widget build(BuildContext context) {
    final list = ref.watch(heldDeletionsProvider).value ?? const [];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final held in list) ...[
          const SizedBox(height: 16),
          Notice(
            icon: Icons.delete_sweep_outlined,
            color: CinemaColors.error,
            title: '「${held.libraryName}」有 ${held.due} 个视频等待确认删除',
            body:
                '共 ${held.total} 个视频，一次失效这么多更可能是目录被误移走，'
                '所以同步没有自动删除。',
          ),
          Align(
            alignment: Alignment.centerRight,
            child: TextButton(
              onPressed: _confirming == null ? () => _confirm(held) : null,
              child: Text(_confirming == held.libraryId ? '正在删除…' : '确认删除'),
            ),
          ),
        ],
      ],
    );
  }
}
