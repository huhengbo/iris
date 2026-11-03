part of 'pending_page.dart';

class _WorkTile extends StatelessWidget {
  const _WorkTile({
    required this.work,
    required this.busy,
    required this.selected,
    required this.onSelected,
    required this.onPick,
    required this.onDelete,
    required this.analyzing,
    required this.onAdopt,
    required this.onIgnore,
    required this.onReanalyze,
  });

  final PendingWork work;
  final bool busy;
  final bool selected;
  final ValueChanged<bool> onSelected;
  final VoidCallback onPick;
  final ValueChanged<String> onDelete;

  /// 后台任务会为这个作品生成 AI 建议（已配置后台任务模型）
  final bool analyzing;
  final VoidCallback onAdopt;
  final VoidCallback onIgnore;
  final VoidCallback onReanalyze;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final muted = text.bodySmall?.copyWith(color: CinemaColors.text2);
    final title = work.title == null
        ? work.path.substring(work.path.lastIndexOf('/') + 1)
        : work.year == null
        ? work.title!
        : '${work.title}（${work.year}）';
    return _PendingRow(
      title: title,
      lines: [
        Text(work.path, style: muted),
        Text(
          '${work.libraryName} · ${work.fileCount} 个视频'
          '${work.candidates.isEmpty ? '' : ' · ${work.candidates.length} 个候选'}',
          style: muted,
        ),
        if (work.reason != null) Text(work.reason!, style: text.bodyMedium),
        _SuggestionLine(
          key: ValueKey(work.id),
          work: work,
          analyzing: analyzing,
          busy: busy,
          onAdopt: onAdopt,
          onPickOther: onPick,
          onIgnore: onIgnore,
          onReanalyze: onReanalyze,
        ),
      ],
      action: '指定',
      busy: busy,
      selected: selected,
      onSelected: onSelected,
      onAction: onPick,
      onDelete: () => onDelete(title),
    );
  }
}

/// 待处理列表的一行：标题、若干说明行，右侧一个操作按钮和删除。
class _PendingRow extends StatelessWidget {
  const _PendingRow({
    required this.title,
    required this.lines,
    required this.action,
    required this.busy,
    this.selected = false,
    this.onSelected,
    required this.onAction,
    required this.onDelete,
  });

  final String title;
  final List<Widget> lines;
  final String action;
  final bool busy;
  final bool selected;
  final ValueChanged<bool>? onSelected;
  final VoidCallback onAction;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 12, 12, 12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (onSelected != null)
            Checkbox(
              value: selected,
              onChanged: (value) => onSelected!(value ?? false),
            ),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              spacing: 2,
              children: [
                Text(title, style: Theme.of(context).textTheme.titleSmall),
                ...lines,
              ],
            ),
          ),
          const SizedBox(width: 12),
          busy
              ? const Padding(
                  padding: EdgeInsets.all(12),
                  child: SizedBox.square(
                    dimension: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                )
              : OutlinedButton(
                  onPressed: onAction,
                  child: Text(action, semanticsLabel: '$action $title'),
                ),
          IconButton(
            tooltip: '删除',
            onPressed: busy ? null : onDelete,
            icon: const Icon(Icons.delete_outline),
          ),
        ],
      ),
    );
  }
}

class _BatchToolbar extends StatelessWidget {
  const _BatchToolbar({
    required this.count,
    required this.allSelected,
    required this.busy,
    required this.onSelectAll,
    required this.onClear,
    this.onRematch,
    required this.onAssign,
    this.assignLabel = '批量指定',
    this.onSecondary,
    this.secondaryLabel,
    required this.onDelete,
  });

  final int count;
  final bool allSelected;
  final bool busy;
  final VoidCallback onSelectAll;
  final VoidCallback onClear;
  final VoidCallback? onRematch;
  final VoidCallback onAssign;
  final String assignLabel;
  final VoidCallback? onSecondary;
  final String? secondaryLabel;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    return Material(
      color: Theme.of(context).colorScheme.surfaceContainerHighest,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 4, 8, 4),
        child: Wrap(
          alignment: WrapAlignment.end,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 8),
              child: Text('已选 $count 项'),
            ),
            TextButton(
              onPressed: busy ? null : onSelectAll,
              child: Text(allSelected ? '取消全选' : '全选本页'),
            ),
            TextButton(
              onPressed: busy ? null : onClear,
              child: const Text('清除'),
            ),
            if (onRematch != null)
              IconButton(
                tooltip: '批量重新匹配',
                onPressed: busy ? null : onRematch,
                icon: const Icon(Icons.refresh),
              ),
            IconButton(
              tooltip: '批量删除',
              onPressed: busy ? null : onDelete,
              icon: const Icon(Icons.delete_outline),
            ),
            if (onSecondary != null)
              TextButton(
                onPressed: busy ? null : onSecondary,
                child: Text(secondaryLabel!),
              ),
            FilledButton.icon(
              onPressed: busy ? null : onAssign,
              icon: busy
                  ? const SizedBox.square(
                      dimension: 16,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Icon(Icons.playlist_add_check),
              label: Text(assignLabel),
            ),
          ],
        ),
      ),
    );
  }
}
