import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/format.dart';
import '../../core/providers.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/section_title.dart';
import '../../core/widgets/snack.dart';
import 'admin_widgets.dart';
import 'busy_icon.dart';
import 'maintenance_api.dart';

/// 回收站：来源上已经找不到的文件，本地还留着记录，按资料库分组。
/// 清理只删本地记录和图片缓存，可以清理全部或单个资料库。
class RecycleBinPage extends ConsumerStatefulWidget {
  const RecycleBinPage({super.key});

  @override
  ConsumerState<RecycleBinPage> createState() => _RecycleBinPageState();
}

class _RecycleBinPageState extends ConsumerState<RecycleBinPage> {
  /// 正在清理：0 为全部，其余为资料库 ID；清理期间其他清理按钮都禁用
  int? _purging;

  Future<void> _purge(List<RecycledFile> files, {int? libraryId}) async {
    final scope = libraryId == null
        ? files
        : files.where((f) => f.libraryId == libraryId).toList();
    final name = libraryId == null ? '回收站' : '「${scope.first.libraryName}」的回收站';
    final confirmed = await showConfirmDialog(
      context,
      title: '清空$name？',
      body:
          '将删除 ${scope.length} 个文件的本地记录和图片缓存，'
          '只剩这些文件的条目会从片库里消失。不会删除来源上的文件。',
      action: '清空',
      destructive: true,
    );
    if (!confirmed || !mounted) return;
    setState(() => _purging = libraryId ?? 0);
    try {
      final deleted = await emptyRecycleBin(
        ref.read(apiClientProvider),
        libraryId: libraryId,
      );
      if (mounted) showSnack(context, '已清理 $deleted 个视频的记录');
      ref.invalidate(recycleBinProvider);
      await ref.read(recycleBinProvider.future);
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    }
    if (mounted) setState(() => _purging = null);
  }

  @override
  Widget build(BuildContext context) {
    final files = ref.watch(recycleBinProvider);
    final items = files.value ?? const <RecycledFile>[];
    return Scaffold(
      appBar: AppBar(
        title: const Text('回收站'),
        actions: [
          IconButton(
            tooltip: '刷新',
            onPressed: () => ref.invalidate(recycleBinProvider),
            icon: const Icon(Icons.refresh),
          ),
          if (items.isNotEmpty)
            _purging == 0
                ? const BusyIcon()
                : IconButton(
                    tooltip: '清空回收站',
                    onPressed: _purging == null ? () => _purge(items) : null,
                    icon: const Icon(Icons.delete_sweep_outlined),
                  ),
        ],
      ),
      body: AsyncView(
        value: files,
        onRetry: () => ref.invalidate(recycleBinProvider),
        builder: (items) => items.isEmpty
            ? const EmptyState(
                icon: Icons.delete_outline,
                title: '回收站是空的',
                message: '同步时发现来源上不见了的文件会先放在这里，确认后再清理本地记录。',
              )
            : RefreshIndicator(
                onRefresh: () async => ref.refresh(recycleBinProvider.future),
                child: AdminListWidth(
                  builder: (padding) => ListView(
                    padding: padding,
                    children: [
                      for (final (index, group) in _byLibrary(
                        items,
                      ).indexed) ...[
                        _LibraryHeader(
                          name: group.first.libraryName,
                          first: index == 0,
                          purging: _purging == group.first.libraryId,
                          onPurge: _purging == null
                              ? () => _purge(
                                  items,
                                  libraryId: group.first.libraryId,
                                )
                              : null,
                        ),
                        for (final file in group) _FileTile(file: file),
                      ],
                    ],
                  ),
                ),
              ),
      ),
    );
  }
}

/// 按资料库分组，保持接口给的顺序（最近消失的在前）
List<List<RecycledFile>> _byLibrary(List<RecycledFile> files) {
  final groups = <int, List<RecycledFile>>{};
  for (final file in files) {
    groups.putIfAbsent(file.libraryId, () => []).add(file);
  }
  return groups.values.toList();
}

class _LibraryHeader extends StatelessWidget {
  const _LibraryHeader({
    required this.name,
    required this.first,
    required this.purging,
    required this.onPurge,
  });

  final String name;
  final bool first;
  final bool purging;
  final VoidCallback? onPurge;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(right: CinemaSpacing.gutter - 12),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        Expanded(child: SectionTitle(name, first: first)),
        TextButton(
          onPressed: onPurge,
          child: Text(purging ? '正在清理…' : '清空此资料库'),
        ),
      ],
    ),
  );
}

class _FileTile extends StatelessWidget {
  const _FileTile({required this.file});

  final RecycledFile file;

  @override
  Widget build(BuildContext context) {
    final missing = file.missingAt;
    final detail = [
      if (file.title == null) '没归入条目',
      formatSize(file.size),
      if (missing != null) '${formatAgo(missing)} 移入',
    ];
    return FocusRing(
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(
          horizontal: CinemaSpacing.gutter,
        ),
        leading: Icon(
          file.mediaItemId == null
              ? Icons.insert_drive_file_outlined
              : Icons.movie_outlined,
        ),
        title: Text(
          file.title ?? file.fileName,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(file.relPath, maxLines: 1, overflow: TextOverflow.ellipsis),
            Text(detail.join(' · ')),
          ],
        ),
      ),
    );
  }
}
