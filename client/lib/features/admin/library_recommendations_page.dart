import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/providers.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/snack.dart';
import '../library/browse_api.dart';
import 'admin_widgets.dart';
import 'library_advanced_api.dart';

/// 「管理推荐」：列出资料库的所有推荐行，两个开关决定出现在首页还是「推荐」标签，
/// 拖动或上移下移调整顺序，保存后首页和推荐标签同时生效。
class LibraryRecommendationsPage extends ConsumerStatefulWidget {
  const LibraryRecommendationsPage({
    super.key,
    required this.id,
    required this.name,
  });

  final int id;
  final String name;

  @override
  ConsumerState<LibraryRecommendationsPage> createState() =>
      _LibraryRecommendationsPageState();
}

class _LibraryRecommendationsPageState
    extends ConsumerState<LibraryRecommendationsPage> {
  /// 本地改动，保存前不写服务端；为 null 时用接口返回的列表
  List<AdminHub>? _rows;
  bool _dirty = false;
  bool _saving = false;

  void _change(List<AdminHub> rows) {
    setState(() {
      _rows = rows;
      _dirty = true;
    });
  }

  void _toggle(List<AdminHub> rows, int index, {bool? home, bool? library}) {
    if (index < 0 || index >= rows.length) return;
    _change([
      for (final (i, row) in rows.indexed)
        i == index ? row.copyWith(showHome: home, showLibrary: library) : row,
    ]);
  }

  void _move(List<AdminHub> rows, int from, int to) {
    if (from == to || from < 0 || to < 0 || to >= rows.length) return;
    final next = [...rows];
    next.insert(to, next.removeAt(from));
    _change(next);
  }

  Future<void> _save() async {
    final rows = _rows;
    if (rows == null || _saving) return;
    setState(() => _saving = true);
    try {
      final saved = await saveLibraryHubs(
        ref.read(apiClientProvider),
        widget.id,
        rows,
      );
      if (!mounted) return;
      setState(() {
        _rows = saved;
        _dirty = false;
        _saving = false;
      });
      // 首页和推荐标签都重新取一次，保存后马上生效
      ref.invalidate(homeFeedProvider);
      ref.invalidate(libraryHubsProvider(widget.id));
      ref.invalidate(libraryHubsAdminProvider(widget.id));
      if (mounted) showSnack(context, '已保存推荐行');
    } catch (error) {
      if (!mounted) return;
      setState(() => _saving = false);
      showApiErrorSnack(context, error);
    }
  }

  @override
  Widget build(BuildContext context) {
    final value = ref.watch(libraryHubsAdminProvider(widget.id));
    return PopScope(
      canPop: !_dirty || _saving,
      onPopInvokedWithResult: (didPop, _) {
        // 有未保存的改动时返回先确认，避免误丢排序
        if (didPop || _saving) return;
        _confirmDiscard();
      },
      child: Scaffold(
        appBar: AppBar(
          title: Text('${widget.name} · 管理推荐'),
          actions: [
            TextButton(
              onPressed: _dirty && !_saving ? _save : null,
              child: _saving
                  ? const SizedBox.square(
                      dimension: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Text('保存'),
            ),
          ],
        ),
        body: AdminListWidth(
          builder: (padding) => AsyncView(
            value: value,
            onRetry: () => ref.invalidate(libraryHubsAdminProvider(widget.id)),
            builder: (rows) {
              final shown = _rows ?? rows;
              if (shown.isEmpty) {
                return const EmptyState(
                  icon: Icons.auto_awesome_outlined,
                  title: '没有推荐行',
                  message: '资料库里有条目后，继续观看、最近添加和类型行会出现在这里',
                );
              }
              return Column(
                children: [
                  Padding(
                    padding: EdgeInsets.fromLTRB(
                      padding.left + CinemaSpacing.gutter,
                      12,
                      padding.right + CinemaSpacing.gutter,
                      4,
                    ),
                    child: Text(
                      '开关决定这一行出现在首页还是「推荐」标签；从上到下就是显示顺序，保存后生效。',
                      style: Theme.of(context).textTheme.bodySmall!
                          .copyWith(color: CinemaColors.text2),
                    ),
                  ),
                  Expanded(
                    child: ReorderableListView.builder(
                      padding: EdgeInsets.fromLTRB(
                        padding.left + CinemaSpacing.gutter,
                        0,
                        padding.right + CinemaSpacing.gutter,
                        padding.bottom,
                      ),
                      buildDefaultDragHandles: false,
                      itemCount: shown.length,
                      onReorderItem: _saving
                          ? (_, _) {}
                          : (from, to) => _move(shown, from, to),
                      itemBuilder: (context, index) => _HubTile(
                        key: ValueKey(
                          '${shown[index].kind}:${shown[index].arg ?? ''}',
                        ),
                        hub: shown[index],
                        index: index,
                        count: shown.length,
                        enabled: !_saving,
                        onHome: (value) => _toggle(shown, index, home: value),
                        onLibrary: (value) =>
                            _toggle(shown, index, library: value),
                        onMove: (to) => _move(shown, index, to),
                      ),
                    ),
                  ),
                ],
              );
            },
          ),
        ),
      ),
    );
  }

  Future<void> _confirmDiscard() async {
    final leave = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('放弃未保存的改动？'),
        content: const Text('排序和开关还没有保存。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('继续编辑'),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text('放弃'),
          ),
        ],
      ),
    );
    if (leave == true && mounted) Navigator.of(context).pop();
  }
}

class _HubTile extends StatelessWidget {
  const _HubTile({
    super.key,
    required this.hub,
    required this.index,
    required this.count,
    required this.enabled,
    required this.onHome,
    required this.onLibrary,
    required this.onMove,
  });

  final AdminHub hub;
  final int index;
  final int count;
  final bool enabled;
  final ValueChanged<bool> onHome;
  final ValueChanged<bool> onLibrary;
  final ValueChanged<int> onMove;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Material(
        color: CinemaColors.surface1,
        borderRadius: BorderRadius.circular(CinemaRadius.card),
        child: FocusRing(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(8, 8, 8, 4),
            child: Column(
              children: [
                Row(
                  children: [
                    ReorderableDragStartListener(
                      index: index,
                      enabled: enabled,
                      child: const Tooltip(
                        message: '拖动排序',
                        child: Padding(
                          padding: EdgeInsets.all(8),
                          child: Icon(
                            Icons.drag_handle,
                            color: CinemaColors.text2,
                          ),
                        ),
                      ),
                    ),
                    Expanded(
                      child: Text(
                        hub.title,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: Theme.of(context).textTheme.bodyMedium,
                      ),
                    ),
                    IconButton(
                      tooltip: '上移',
                      onPressed: enabled && index > 0
                          ? () => onMove(index - 1)
                          : null,
                      icon: const Icon(Icons.keyboard_arrow_up),
                    ),
                    IconButton(
                      tooltip: '下移',
                      onPressed: enabled && index < count - 1
                          ? () => onMove(index + 1)
                          : null,
                      icon: const Icon(Icons.keyboard_arrow_down),
                    ),
                  ],
                ),
                Row(
                  children: [
                    Expanded(
                      child: SwitchListTile(
                        dense: true,
                        contentPadding: const EdgeInsets.symmetric(
                          horizontal: 8,
                        ),
                        title: const Text('在首页显示'),
                        value: hub.showHome,
                        onChanged: enabled ? onHome : null,
                      ),
                    ),
                    Expanded(
                      child: SwitchListTile(
                        dense: true,
                        contentPadding: const EdgeInsets.symmetric(
                          horizontal: 8,
                        ),
                        title: const Text('在推荐标签显示'),
                        value: hub.showLibrary,
                        onChanged: enabled ? onLibrary : null,
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
