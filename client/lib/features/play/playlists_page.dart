import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/snack.dart';
import '../../core/widgets/text_prompt.dart';
import '../auth/auth_controller.dart';
import '../../core/format.dart';
import 'play_api.dart';
import 'playlists_api.dart';
import 'play_launcher.dart';

/// 自己建的列表能改；管理员能改全部。别人共享的列表只能看和播放。
bool _editable(WidgetRef ref, Map<String, dynamic> playlist) {
  final user = ref.watch(authControllerProvider).value;
  return user != null && (user.isAdmin || playlist['user_id'] == user.id);
}

/// 新建或改名后刷新列表；失败时提示。返回是否成功。
Future<bool> _save(
  BuildContext context,
  WidgetRef ref,
  Future<void> Function() call,
) async {
  try {
    await call();
    ref.invalidate(playlistsProvider);
    return true;
  } catch (error) {
    if (context.mounted) showApiErrorSnack(context, error);
    return false;
  }
}

Future<void> _rename(
  BuildContext context,
  WidgetRef ref,
  Map<String, dynamic> playlist,
) async {
  final id = playlist['id'] as int;
  final title = await showTextPrompt(
    context,
    title: '重命名播放列表',
    label: '名称',
    initial: playlist['title'] as String? ?? '',
    action: '保存',
  );
  if (title == null || title.isEmpty || !context.mounted) return;
  final ok = await _save(context, ref, () {
    return ref.read(playlistsApiProvider).rename(id, title);
  });
  if (ok) ref.invalidate(playlistDetailProvider(id));
}

class PlaylistsPage extends ConsumerWidget {
  const PlaylistsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final playlists = ref.watch(playlistsProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('播放列表'),
        actions: [
          IconButton(
            tooltip: '新建播放列表',
            onPressed: () => _create(context, ref),
            icon: const Icon(Icons.add),
          ),
        ],
      ),
      body: AsyncView(
        value: playlists,
        onRetry: () => ref.invalidate(playlistsProvider),
        builder: (items) => items.isEmpty
            ? const EmptyState(
                icon: Icons.playlist_play,
                title: '还没有播放列表',
                message: '在电影或剧集详情页点「加入播放列表」，之后按顺序连续播放。',
              )
            : ListView.builder(
                padding: const EdgeInsets.only(bottom: 24),
                itemCount: items.length,
                itemBuilder: (context, index) {
                  final playlist = items[index] as Map<String, dynamic>;
                  final id = playlist['id'] as int;
                  return FocusRing(
                    child: ListTile(
                      leading: const Icon(Icons.playlist_play),
                      title: Text(playlist['title'] as String? ?? '未命名列表'),
                      subtitle:
                          playlist['shared'] == 1 || playlist['shared'] == true
                          ? const Text('共享列表')
                          : null,
                      trailing: _editable(ref, playlist)
                          ? PopupMenuButton<String>(
                              tooltip: '管理列表',
                              icon: const Icon(Icons.more_horiz),
                              onSelected: (action) => action == 'rename'
                                  ? _rename(context, ref, playlist)
                                  : _delete(context, ref, playlist),
                              itemBuilder: (_) => const [
                                PopupMenuItem(
                                  value: 'rename',
                                  child: Text('重命名'),
                                ),
                                PopupMenuItem(
                                  value: 'delete',
                                  child: Text('删除'),
                                ),
                              ],
                            )
                          : const Icon(Icons.chevron_right),
                      onTap: () => context.push('/me/playlists/$id'),
                    ),
                  );
                },
              ),
      ),
    );
  }

  Future<void> _create(BuildContext context, WidgetRef ref) async {
    final title = await showTextPrompt(
      context,
      title: '新建播放列表',
      label: '名称',
      action: '创建',
    );
    if (title == null || title.isEmpty || !context.mounted) return;
    await _save(context, ref, () {
      return ref.read(playlistsApiProvider).create(title);
    });
  }

  Future<void> _delete(
    BuildContext context,
    WidgetRef ref,
    Map<String, dynamic> playlist,
  ) async {
    final confirmed = await showConfirmDialog(
      context,
      title: '删除「${playlist['title'] ?? '未命名列表'}」？',
      body: '只删除这个列表，不影响里面的影片和观看记录。',
      action: '删除',
      destructive: true,
    );
    if (!confirmed || !context.mounted) return;
    final ok = await _save(context, ref, () {
      return ref.read(playlistsApiProvider).delete(playlist['id']);
    });
    if (ok && context.mounted) showSnack(context, '已删除播放列表');
  }
}

class PlaylistDetailPage extends ConsumerStatefulWidget {
  const PlaylistDetailPage({super.key, required this.playlistId});

  final int playlistId;

  @override
  ConsumerState<PlaylistDetailPage> createState() => _PlaylistDetailPageState();
}

enum _EntryAction { moveUp, moveDown, remove }

class _PlaylistDetailPageState extends ConsumerState<PlaylistDetailPage> {
  /// 调整顺序或移除后先按新顺序显示，等服务端确认；失败时恢复。
  /// 不为空就是正在保存，期间上移、下移、移除和拖动都不可用，免得互相覆盖。
  List<dynamic>? _pending;

  int get _id => widget.playlistId;
  bool get _saving => _pending != null;

  @override
  Widget build(BuildContext context) {
    final value = ref.watch(playlistDetailProvider(_id));
    final playlist =
        value.value?['playlist'] as Map<String, dynamic>? ?? const {};
    final editable = playlist.isNotEmpty && _editable(ref, playlist);
    return Scaffold(
      appBar: AppBar(
        title: Text(playlist['title'] as String? ?? '播放列表'),
        actions: [
          if (editable)
            IconButton(
              tooltip: '重命名',
              icon: const Icon(Icons.edit_outlined),
              onPressed: () => _rename(context, ref, playlist),
            ),
        ],
      ),
      body: AsyncView(
        value: value,
        onRetry: () => ref.invalidate(playlistDetailProvider(_id)),
        builder: (data) {
          final items =
              _pending ?? (data['items'] as List<dynamic>?) ?? const [];
          if (items.isEmpty) {
            return const EmptyState(
              icon: Icons.playlist_play,
              title: '列表还是空的',
              message: '在电影或剧集详情页点「加入播放列表」。',
            );
          }
          final header = Padding(
            padding: const EdgeInsets.fromLTRB(
              CinemaSpacing.gutter,
              8,
              CinemaSpacing.gutter,
              8,
            ),
            child: Row(
              children: [
                FilledButton.icon(
                  autofocus: true,
                  onPressed: () =>
                      _play(items.cast<Map<String, dynamic>>().first),
                  icon: const Icon(Icons.play_arrow_rounded),
                  label: const Text('播放全部'),
                ),
                const SizedBox(width: 16),
                Expanded(
                  child: Text(
                    !editable
                        ? '${items.length} 项'
                        : _saving
                        ? '${items.length} 项 · 正在保存顺序'
                        : '${items.length} 项 · 拖动可调整顺序',
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ),
              ],
            ),
          );
          final tiles = [
            for (final (index, raw) in items.indexed)
              _tile(raw as Map<String, dynamic>, index, items, editable),
          ];
          if (!editable) {
            return ListView(
              padding: const EdgeInsets.only(bottom: 24),
              children: [header, ...tiles],
            );
          }
          // 拖动手柄只是快捷方式，每行「⋯」菜单里的上移、下移让键盘和遥控器也能排序
          return ReorderableListView(
            padding: const EdgeInsets.only(bottom: 24),
            header: header,
            buildDefaultDragHandles: false,
            onReorderItem: (from, to) {
              if (_saving) return;
              final reordered = [...items];
              reordered.insert(to, reordered.removeAt(from));
              _update(reordered);
            },
            children: tiles,
          );
        },
      ),
    );
  }

  Widget _tile(
    Map<String, dynamic> item,
    int index,
    List<dynamic> items,
    bool editable,
  ) {
    final playable = item['version_id'] != null;
    final detail = _episodeLabel(item);
    final title = item['title'] as String? ?? '未知条目';
    return Material(
      key: ValueKey(item['playable_id']),
      // 拖动时这一行浮起来，要有自己的底色
      color: CinemaColors.bg,
      child: FocusRing(
        child: ListTile(
          leading: SizedBox(
            width: 28,
            child: Center(child: Text('${index + 1}')),
          ),
          title: Text(title),
          subtitle: !playable
              ? const Text('没有可播放的文件')
              : detail == null
              ? null
              : Text(detail),
          enabled: playable,
          onTap: playable ? () => _play(item) : null,
          trailing: editable
              ? Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    PopupMenuButton<_EntryAction>(
                      tooltip: '管理 $title',
                      icon: const Icon(Icons.more_horiz),
                      onSelected: (action) =>
                          _onAction(action, item, index, items),
                      itemBuilder: (_) => [
                        PopupMenuItem(
                          value: _EntryAction.moveUp,
                          enabled: index > 0 && !_saving,
                          child: const Text('上移'),
                        ),
                        PopupMenuItem(
                          value: _EntryAction.moveDown,
                          enabled: index < items.length - 1 && !_saving,
                          child: const Text('下移'),
                        ),
                        PopupMenuItem(
                          value: _EntryAction.remove,
                          enabled: !_saving,
                          child: const Text('移出列表'),
                        ),
                      ],
                    ),
                    if (items.length > 1)
                      ReorderableDragStartListener(
                        index: index,
                        enabled: !_saving,
                        child: Tooltip(
                          message: _saving ? '正在保存顺序' : '拖动排序',
                          child: Padding(
                            padding: const EdgeInsets.all(8),
                            child: Icon(
                              Icons.drag_handle,
                              color: _saving
                                  ? CinemaColors.text2.withValues(alpha: .4)
                                  : CinemaColors.text2,
                            ),
                          ),
                        ),
                      ),
                  ],
                )
              : null,
        ),
      ),
    );
  }

  Future<void> _onAction(
    _EntryAction action,
    Map<String, dynamic> item,
    int index,
    List<dynamic> items,
  ) async {
    if (_saving) return;
    Future<void> move(int to) {
      final reordered = [...items];
      reordered.insert(to, reordered.removeAt(index));
      return _update(reordered);
    }

    switch (action) {
      case _EntryAction.moveUp:
        await move(index - 1);
      case _EntryAction.moveDown:
        await move(index + 1);
      case _EntryAction.remove:
        final confirmed = await showConfirmDialog(
          context,
          title: '移出「${item['title'] ?? '未知条目'}」？',
          body: '只从这个列表里移除，不影响影片本身和观看记录。',
          action: '移出',
          destructive: true,
        );
        if (!confirmed || !mounted || _saving) return;
        await _update([...items]..removeAt(index));
    }
  }

  /// 剧集的一集写成「第 1 季 第 3 集 · 集名」；电影没有这一行。
  String? _episodeLabel(Map<String, dynamic> item) {
    final number = item['episode_number'] as int?;
    if (number == null) return null;
    final season = item['season_number'] as int?;
    final name = item['episode_name'] as String?;
    return [
      season != null ? formatEpisode(season, number) : '第 $number 集',
      if (name != null && name.isNotEmpty) name,
    ].join(' · ');
  }

  /// 从这一项开始播放，播完由内置播放器接着放列表里的下一项。
  Future<void> _play(Map<String, dynamic> item) {
    final itemId = item['item_id'] as int;
    final episodeId = item['episode_id'] as int?;
    return play(
      context,
      PlayRequest(
        itemId: itemId,
        target: episodeId != null
            ? PlayTarget.episode(episodeId)
            : PlayTarget.item(itemId),
        subtitle: [
          item['title'] as String? ?? '未知条目',
          ?_episodeLabel(item),
        ].join(' · '),
        playlistId: _id,
      ),
    );
  }

  Future<void> _update(List<dynamic> items) async {
    setState(() => _pending = items);
    try {
      await ref.read(playlistsApiProvider).setItems(_id, [
        for (final raw in items) (raw as Map<String, dynamic>)['playable_id'],
      ]);
      ref.invalidate(playlistDetailProvider(_id));
      await ref.read(playlistDetailProvider(_id).future);
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    } finally {
      if (mounted) setState(() => _pending = null);
    }
  }
}
