part of 'item_admin_page.dart';

class _Header extends StatelessWidget {
  const _Header({required this.detail});

  final AdminItemDetail detail;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final muted = text.bodySmall?.copyWith(color: CinemaColors.text2);
    final item = detail.item;
    final meta = [
      libraryKindLabel(item.kind),
      item.libraryName,
      if (item.year != null) '${item.year}',
      if (detail.runtime != null) '${detail.runtime} 分钟',
      if (detail.rating != null && detail.rating! > 0)
        detail.rating!.toStringAsFixed(1),
    ].join(' · ');
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(CinemaRadius.small),
          child: SizedBox(
            width: 96,
            height: 144,
            child: ArtImage(art: item.poster),
          ),
        ),
        const SizedBox(width: 16),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            spacing: 4,
            children: [
              Text(item.title, style: text.titleLarge),
              if (item.originalTitle != null &&
                  item.originalTitle != item.title)
                Text(item.originalTitle!, style: text.bodyMedium),
              Text(meta, style: muted),
              if (detail.genres.isNotEmpty)
                Text(detail.genres.join(' / '), style: muted),
              Text('元数据来自${detail.providerName}', style: muted),
              if (detail.externalUrl != null)
                SelectableText(detail.externalUrl!, style: muted),
              Text('信息更新于 ${formatDateTime(detail.scrapedAt)}', style: muted),
            ],
          ),
        ),
      ],
    );
  }
}

class _Actions extends StatelessWidget {
  const _Actions({
    required this.busy,
    required this.onRefresh,
    required this.onRematch,
    required this.onReenrich,
    required this.onSyncDirs,
  });

  final String? busy;
  final VoidCallback onRefresh;
  final ValueChanged<_Rematch> onRematch;
  final VoidCallback onReenrich;
  final VoidCallback onSyncDirs;

  @override
  Widget build(BuildContext context) {
    final enabled = busy == null;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            OutlinedButton.icon(
              onPressed: enabled ? onRefresh : null,
              icon: Icon(ItemAction.refresh.icon),
              label: Text(ItemAction.refresh.label),
            ),
            MenuAnchor(
              menuChildren: [
                MenuItemButton(
                  onPressed: () => onRematch(_Rematch.pick),
                  child: const Text('改为其他条目'),
                ),
                MenuItemButton(
                  onPressed: () => onRematch(_Rematch.auto),
                  child: const Text('重新自动匹配'),
                ),
              ],
              builder: (context, controller, _) => OutlinedButton.icon(
                onPressed: enabled
                    ? () => controller.isOpen
                          ? controller.close()
                          : controller.open()
                    : null,
                icon: Icon(ItemAction.rematch.icon),
                label: Text(ItemAction.rematch.label),
              ),
            ),
            OutlinedButton.icon(
              onPressed: enabled ? onReenrich : null,
              icon: Icon(ItemAction.reenrich.icon),
              label: Text(ItemAction.reenrich.label),
            ),
            OutlinedButton.icon(
              onPressed: enabled ? onSyncDirs : null,
              icon: Icon(ItemAction.sync.icon),
              label: Text(ItemAction.sync.label),
            ),
          ],
        ),
        if (busy != null)
          Padding(
            padding: const EdgeInsets.only(top: 12),
            child: Semantics(
              liveRegion: true,
              child: Row(
                children: [
                  const SizedBox.square(
                    dimension: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                  const SizedBox(width: 8),
                  Text('正在$busy…'),
                ],
              ),
            ),
          ),
      ],
    );
  }
}

class _MetadataSection extends StatelessWidget {
  const _MetadataSection({
    required this.fields,
    required this.enabled,
    required this.onEdit,
    required this.onUnlock,
  });

  final List<MetadataField> fields;
  final bool enabled;
  final ValueChanged<MetadataField> onEdit;
  final ValueChanged<MetadataField> onUnlock;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SectionTitle('元数据来源与锁定', inset: false),
        for (final field in fields)
          ListTile(
            contentPadding: EdgeInsets.zero,
            dense: true,
            title: Text(field.label),
            subtitle: Text(
              [
                field.value?.isNotEmpty == true ? field.value! : '未设置',
                field.sourceLabel,
                '更新于 ${formatDateTime(field.updatedAt)}',
              ].join(' · '),
              maxLines: field.field == 'overview' ? 3 : 1,
              overflow: TextOverflow.ellipsis,
              style: text.bodySmall?.copyWith(color: CinemaColors.text2),
            ),
            leading: Icon(field.locked ? Icons.lock_outline : Icons.public),
            trailing: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (field.locked)
                  IconButton(
                    tooltip: '解锁${field.label}，恢复自动更新',
                    onPressed: enabled ? () => onUnlock(field) : null,
                    icon: const Icon(Icons.lock_open_outlined),
                  ),
                IconButton(
                  tooltip: '编辑${field.label}',
                  onPressed: enabled ? () => onEdit(field) : null,
                  icon: const Icon(Icons.edit_outlined),
                ),
              ],
            ),
          ),
      ],
    );
  }
}

class _ArtworkSection extends StatelessWidget {
  const _ArtworkSection({required this.enabled, required this.onSelect});

  final bool enabled;
  final Future<void> Function(String kind, String label) onSelect;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      const SectionTitle('图片', inset: false),
      Wrap(
        spacing: 8,
        runSpacing: 8,
        children: [
          for (final entry in const {
            'poster': '海报',
            'backdrop': '背景图',
            'thumb': '横版图',
            'logo': 'Logo',
          }.entries)
            OutlinedButton.icon(
              onPressed: enabled
                  ? () => onSelect(entry.key, entry.value)
                  : null,
              icon: const Icon(Icons.image_outlined),
              label: Text(entry.value),
            ),
        ],
      ),
    ],
  );
}

class _WorkRow extends StatelessWidget {
  const _WorkRow({required this.work});

  final ItemWork work;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          const Icon(
            Icons.folder_outlined,
            size: 20,
            color: CinemaColors.text2,
          ),
          const SizedBox(width: 12),
          Expanded(child: Text(work.path)),
          if (work.manual) const _Tag('手动指定'),
        ],
      ),
    );
  }
}

class _VersionRow extends StatelessWidget {
  const _VersionRow({
    required this.version,
    required this.enabled,
    required this.onReassign,
    required this.onDetach,
  });

  final ItemVersion version;
  final bool enabled;
  final VoidCallback onReassign;
  final VoidCallback onDetach;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final muted = text.bodySmall?.copyWith(color: CinemaColors.text2);
    final episode = version.season == null || version.episode == null
        ? null
        : formatEpisode(version.season!, version.episode!);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              spacing: 2,
              children: [
                Text(
                  [?episode, version.label].join(' · '),
                  style: text.titleSmall,
                ),
                Text(version.path, style: muted),
                if (version.missing || version.manual)
                  Wrap(
                    spacing: 6,
                    children: [
                      if (version.missing)
                        const _Tag('来源上已找不到', color: CinemaColors.error),
                      if (version.manual) const _Tag('手动指定'),
                    ],
                  ),
              ],
            ),
          ),
          MenuAnchor(
            menuChildren: [
              MenuItemButton(onPressed: onReassign, child: const Text('改挂')),
              MenuItemButton(onPressed: onDetach, child: const Text('拆出')),
            ],
            builder: (context, controller, _) => IconButton(
              tooltip: '${version.name} 的操作',
              onPressed: enabled
                  ? () => controller.isOpen
                        ? controller.close()
                        : controller.open()
                  : null,
              icon: const Icon(Icons.more_vert),
            ),
          ),
        ],
      ),
    );
  }
}

/// 失效、手动指定这类状态只用文字，不做徽章。
class _Tag extends StatelessWidget {
  const _Tag(this.label, {this.color = CinemaColors.text2});

  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) => Text(
    label,
    style: Theme.of(context).textTheme.labelSmall?.copyWith(color: color),
  );
}
