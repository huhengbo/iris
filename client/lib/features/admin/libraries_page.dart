import 'package:flutter/foundation.dart' show listEquals;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/api/paging.dart';
import '../../core/providers.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/dropdown_field.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/section_title.dart';
import '../../core/widgets/snack.dart';
import '../library/art_image.dart';
import '../library/browse_api.dart' show librariesProvider;
import 'admin_widgets.dart';
import 'libraries_api.dart';
import 'library_advanced_api.dart';
import 'library_downloaders.dart';
import 'library_downloaders_api.dart';
import 'resources_api.dart';

part 'library_cover.dart';
part 'library_dialog.dart';
part 'library_edit_advanced.dart';
part 'library_folder_pickers.dart';

const _maxNameLength = 64;

/// 管理后台的资料库页：列表、排序、新建编辑删除，以及单个资料库的同步入口。
class LibrariesPage extends ConsumerStatefulWidget {
  const LibrariesPage({super.key});

  @override
  ConsumerState<LibrariesPage> createState() => _LibrariesPageState();
}

class _LibrariesPageState extends ConsumerState<LibrariesPage> {
  /// 调整顺序时先按新顺序显示，等服务端确认后再换回服务端数据。
  /// 不为空时表示还在保存，期间不能再排序，免得两次请求交错
  List<AdminLibrary>? _reordered;

  @override
  Widget build(BuildContext context) {
    final libraries = ref.watch(adminLibrariesProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('资料库'),
        actions: [
          // 遥控器没法下拉刷新
          IconButton(
            tooltip: '刷新',
            onPressed: () => ref.invalidate(adminLibrariesProvider),
            icon: const Icon(Icons.refresh),
          ),
          IconButton(
            tooltip: '同步',
            onPressed: () => context.push('/settings/status/sync'),
            icon: const Icon(Icons.sync),
          ),
        ],
      ),
      floatingActionButton: FocusRing(
        radius: CinemaRadius.fab,
        child: FloatingActionButton.extended(
          onPressed: () => _edit(null),
          icon: const Icon(Icons.add),
          label: const Text('新建资料库'),
        ),
      ),
      body: AsyncView(
        value: libraries,
        onRetry: () => ref.invalidate(adminLibrariesProvider),
        builder: (list) {
          final shown = _reordered ?? list;
          if (shown.isEmpty) {
            return const EmptyState(
              icon: Icons.video_library_outlined,
              title: '还没有资料库',
              message: '资料库对应资源来源里的一个或多个目录，同步后其中的视频会出现在影视墙。',
            );
          }
          return RefreshIndicator(
            onRefresh: () => ref.refresh(adminLibrariesProvider.future),
            child: AdminListWidth(
              bottom: 88,
              builder: (padding) => ReorderableListView.builder(
                padding: padding,
                buildDefaultDragHandles: false,
                itemCount: shown.length,
                onReorderItem: (from, to) => _move(shown, from, to),
                itemBuilder: (context, index) => _LibraryTile(
                  key: ValueKey(shown[index].id),
                  library: shown[index],
                  index: index,
                  count: shown.length,
                  reordering: _reordered != null,
                  onAction: (action) => _run(shown, index, action),
                ),
              ),
            ),
          );
        },
      ),
    );
  }

  Future<void> _edit(AdminLibrary? library) async {
    final saved = await showDialog<bool>(
      context: context,
      builder: (_) => LibraryDialog(library: library),
    );
    if (saved == true) ref.invalidate(adminLibrariesProvider);
  }

  Future<void> _move(List<AdminLibrary> list, int from, int to) async {
    if (from == to || _reordered != null) return;
    final next = [...list];
    next.insert(to, next.removeAt(from));
    setState(() => _reordered = next);
    try {
      await reorderLibraries(ref.read(apiClientProvider), [
        for (final library in next) library.id,
      ]);
      final _ = await ref.refresh(adminLibrariesProvider.future);
    } catch (e) {
      if (mounted) showApiErrorSnack(context, e);
    }
    if (mounted) setState(() => _reordered = null);
  }

  Future<void> _run(
    List<AdminLibrary> list,
    int index,
    _LibraryAction action,
  ) async {
    final library = list[index];
    switch (action) {
      case _LibraryAction.moveUp:
        return _move(list, index, index - 1);
      case _LibraryAction.moveDown:
        return _move(list, index, index + 1);
      case _LibraryAction.edit:
        return _edit(library);
      case _LibraryAction.advanced:
        if (mounted) {
          GoRouter.of(context).push(
            '/settings/manage/libraries/${library.id}?name=${Uri.encodeComponent(library.name)}',
          );
        }
        return;
      case _LibraryAction.sync:
      case _LibraryAction.delete:
    }
    final dio = ref.read(apiClientProvider);
    final router = GoRouter.maybeOf(context);
    try {
      if (action == _LibraryAction.sync) {
        await startSync(dio, libraryId: library.id);
        if (mounted) {
          showSnack(
            context,
            '已开始同步「${library.name}」',
            action: router == null
                ? null
                : SnackBarAction(
                    label: '查看',
                    onPressed: () => router.push('/settings/status/sync'),
                  ),
          );
        }
        return;
      }
      final confirmed = await showConfirmDialog(
        context,
        title: '删除资料库「${library.name}」？',
        body: '资料库和它的文件记录会从本系统删除，存储上的文件不受影响。',
        action: '删除',
        destructive: true,
      );
      if (!confirmed) return;
      await deleteLibrary(dio, library.id);
      ref.invalidate(adminLibrariesProvider);
      if (mounted) showSnack(context, '已删除资料库「${library.name}」');
    } catch (e) {
      if (mounted) showApiErrorSnack(context, e);
    }
  }
}

enum _LibraryAction { sync, edit, advanced, moveUp, moveDown, delete }

class _LibraryTile extends StatelessWidget {
  const _LibraryTile({
    super.key,
    required this.library,
    required this.index,
    required this.count,
    required this.reordering,
    required this.onAction,
  });

  final AdminLibrary library;
  final int index;
  final int count;

  /// 正在保存新顺序：上移下移和拖动暂时不可用
  final bool reordering;
  final ValueChanged<_LibraryAction> onAction;

  @override
  Widget build(BuildContext context) {
    final facts = [
      libraryKindLabel(library.kind),
      '${library.videoCount} 个视频',
      if (library.missingCount > 0) '${library.missingCount} 个已失效',
    ];
    return Material(
      color: CinemaColors.bg,
      child: FocusRing(
        child: ListTile(
          leading: CircleAvatar(
            child: Icon(
              library.kind == 'series'
                  ? Icons.tv_outlined
                  : Icons.movie_outlined,
            ),
          ),
          title: Text(
            library.name,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          subtitle: Text(
            '${facts.join(' · ')}\n'
            '${library.folders.map(_folderLabel).join('、')}',
            maxLines: 3,
            overflow: TextOverflow.ellipsis,
          ),
          isThreeLine: true,
          onTap: () => onAction(_LibraryAction.edit),
          trailing: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              // 拖动之外，菜单里的上移下移让键盘和遥控器也能排序
              PopupMenuButton<_LibraryAction>(
                tooltip: '管理 ${library.name}',
                onSelected: onAction,
                itemBuilder: (_) => [
                  const PopupMenuItem(
                    value: _LibraryAction.sync,
                    child: Text('同步此资料库'),
                  ),
                  const PopupMenuItem(
                    value: _LibraryAction.edit,
                    child: Text('编辑'),
                  ),
                  const PopupMenuItem(
                    value: _LibraryAction.advanced,
                    child: Text('统计'),
                  ),
                  if (index > 0)
                    PopupMenuItem(
                      value: _LibraryAction.moveUp,
                      enabled: !reordering,
                      child: const Text('上移'),
                    ),
                  if (index < count - 1)
                    PopupMenuItem(
                      value: _LibraryAction.moveDown,
                      enabled: !reordering,
                      child: const Text('下移'),
                    ),
                  const PopupMenuItem(
                    value: _LibraryAction.delete,
                    child: Text('删除'),
                  ),
                ],
              ),
              if (count > 1)
                ReorderableDragStartListener(
                  index: index,
                  enabled: !reordering,
                  child: Tooltip(
                    message: reordering ? '正在保存顺序' : '拖动排序',
                    child: Padding(
                      padding: const EdgeInsets.all(8),
                      child: Icon(
                        Icons.drag_handle,
                        color: reordering
                            ? CinemaColors.text2.withValues(alpha: .4)
                            : CinemaColors.text2,
                      ),
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}
