part of 'sync_page.dart';

class _History extends ConsumerWidget {
  const _History();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final jobs = ref.watch(syncJobsProvider);
    final muted = Theme.of(context).textTheme.bodyMedium
        ?.copyWith(color: CinemaColors.text2);
    return switch (jobs) {
      AsyncData(value: final list) =>
        list.where((j) => !j.running).isEmpty
            ? Padding(
                padding: const EdgeInsets.symmetric(vertical: 12),
                child: Text('还没有同步记录', style: muted),
              )
            : Column(
                children: [
                  for (final job in list)
                    if (!job.running) _JobTile(job: job),
                ],
              ),
      AsyncError(:final error) => ErrorRetry(
        message: ApiError.from(error).message,
        onRetry: () => ref.invalidate(syncJobsProvider),
      ),
      _ => const _Loading(),
    };
  }
}

class _JobTile extends StatelessWidget {
  const _JobTile({required this.job});

  final SyncJob job;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final failed = job.status == 'failed';
    final stats = job.stats;
    final details = [
      if (job.error != null) job.error!,
      ...stats.problems,
      if (stats.failed > stats.problems.length)
        '另有 ${stats.failed - stats.problems.length} 个问题未列出',
      if (stats.paused > 0) '因来源限流暂停 ${stats.paused} 次',
    ];
    final summary = [
      _triggerLabel(job.trigger),
      formatAgo(job.startedAt),
      if (job.finishedAt != null)
        '用时 ${_duration(job.finishedAt!.difference(job.startedAt))}',
    ].join(' · ');
    final title = Row(
      children: [
        Icon(
          failed ? Icons.error_outline : Icons.check_circle_outline,
          size: 20,
          color: failed ? CinemaColors.error : CinemaColors.success,
        ),
        const SizedBox(width: 12),
        Expanded(
          child: Text(
            '${job.title} · ${failed ? '失败' : '完成'}',
            style: text.titleSmall,
          ),
        ),
      ],
    );
    final subtitle = Padding(
      padding: const EdgeInsets.only(left: 32, top: 2),
      child: Text(
        '$summary\n${_hasChanges(stats) ? _statsLine(stats) : '没有变化'}',
        style: text.bodySmall?.copyWith(color: CinemaColors.text2),
      ),
    );
    // 和页面其他内容左对齐；有详情的行可展开，遥控器聚焦时描琥珀环
    if (details.isEmpty) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [title, subtitle],
        ),
      );
    }
    return FocusRing(
      child: ExpansionTile(
        tilePadding: EdgeInsets.zero,
        title: title,
        subtitle: subtitle,
        shape: const Border(),
        collapsedShape: const Border(),
        childrenPadding: const EdgeInsets.fromLTRB(32, 0, 0, 12),
        expandedCrossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (final line in details)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Text(
                line,
                style: text.bodyMedium?.copyWith(
                  color: line == job.error ? CinemaColors.error : null,
                ),
              ),
            ),
        ],
      ),
    );
  }
}

/// 同步阶段的中文名，后台任务页的同步任务也用它。
String syncPhaseLabel(String phase) => switch (phase) {
  'preparing' => '准备中',
  'exporting' => '导出目录树',
  'listing' => '列出目录',
  'enriching' => '补全文件信息',
  'recognizing' => '识别文件名',
  'matching' => '匹配元数据',
  'filling' => '补充元数据和图片',
  'scraping' => '获取剧集信息',
  'images' => '下载图片',
  _ => '收尾',
};

/// 进度计数的单位；识别阶段先按目录、再按要读取的 NFO 计数，统称「项」
String _phaseUnit(String phase) => switch (phase) {
  'enriching' => '组目录',
  'matching' => '个作品',
  'filling' => '个条目',
  'scraping' => '季',
  'images' => '张图片',
  'recognizing' => '项',
  _ => '个目录',
};

String _triggerLabel(String trigger) => switch (trigger) {
  'scheduled' => '定时',
  'event' => '来源变动',
  _ => '手动',
};

bool _hasChanges(SyncStats s) =>
    s.added +
        s.removed +
        s.renamed +
        s.restored +
        s.failed +
        s.purged +
        s.matched +
        s.unmatched +
        s.ignored +
        s.waiting >
    0;

String _statsLine(SyncStats s) => [
  if (s.added > 0) '新增 ${s.added}',
  if (s.removed > 0) '失效 ${s.removed}',
  if (s.renamed > 0) '改名 ${s.renamed}',
  if (s.restored > 0) '恢复 ${s.restored}',
  if (s.purged > 0) '删除 ${s.purged}',
  if (s.matched > 0) '匹配 ${s.matched}',
  if (s.unmatched > 0) '未匹配 ${s.unmatched}',
  if (s.ignored > 0) '忽略 ${s.ignored}',
  if (s.waiting > 0) '等待匹配 ${s.waiting}',
  if (s.failed > 0) '问题 ${s.failed}',
].join(' · ');

String _duration(Duration d) {
  if (d.inHours > 0) return '${d.inHours} 小时 ${d.inMinutes % 60} 分';
  if (d.inMinutes > 0) return '${d.inMinutes} 分 ${d.inSeconds % 60} 秒';
  return '${d.inSeconds} 秒';
}

String _hhmm(TimeOfDay t) =>
    '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
