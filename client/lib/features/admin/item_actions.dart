import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/providers.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/picker_sheet.dart';
import '../../core/widgets/snack.dart';
import '../auth/auth_controller.dart';
import '../detail/media_actions.dart';
import '../detail/media_info_view.dart';
import '../library/browse_api.dart';
import '../library/collections_page.dart';
import '../play/playlist_actions.dart';
import '../subtitles/edit_subtitles_dialog.dart';
import 'catalog_api.dart';
import 'tasks_api.dart';

enum ItemAction {
  favorite(Icons.favorite_border, '收藏'),
  unfavorite(Icons.favorite, '取消收藏'),
  watchlist(Icons.bookmark_border, '加入稍后观看'),
  unwatchlist(Icons.bookmark, '移出稍后观看'),
  rematch(Icons.find_replace, '修正匹配'),
  unmatch(Icons.link_off, '取消匹配'),
  refresh(Icons.refresh, '刷新元数据'),
  reenrich(Icons.fact_check_outlined, '重新读取文件'),
  editSubtitles(Icons.subtitles_outlined, '编辑字幕'),
  info(Icons.info_outline, '获取信息'),
  sync(Icons.sync, '同步此目录'),
  addToPlaylist(Icons.playlist_add, '加入播放列表'),
  addToCollection(Icons.collections_bookmark_outlined, '加入合集'),
  delete(Icons.delete_outline, '删除');

  const ItemAction(this.icon, this.label);

  final IconData icon;
  final String label;
}

// 详情页「⋯」菜单和条目管理页执行同一个操作时用同一套提示，两边说法一致。

const refreshedMessage = '已刷新元数据';
const syncStartedMessage = '已开始同步此目录，可在「同步」页查看进度';

String reenrichMessage(({int updated, int missing}) result) {
  if (result.updated == 0 && result.missing == 0) return '文件信息没有变化';
  return [
    if (result.updated > 0) '更新了 ${result.updated} 个文件',
    if (result.missing > 0) '${result.missing} 个文件在来源上已找不到',
  ].join('，');
}

/// 删除条目前的确认，详情页和条目管理页共用。会同时清理来源上的文件夹，必须说清楚。
Future<bool> confirmDeleteItem(BuildContext context, String title) =>
    showConfirmDialog(
      context,
      title: '删除「$title」？',
      body: '会删除整部电影或剧集在来源上的文件夹，连同视频、字幕和其他文件一起删除，操作不可恢复。',
      action: ItemAction.delete.label,
      destructive: true,
    );

/// 操作进行中的小对话框：不可关闭，挡住重复点击，[task] 结束（成功或失败）后自动关掉。
Future<T> runWithProgress<T>(
  BuildContext context,
  String message,
  Future<T> Function() task,
) async {
  final navigator = Navigator.of(context, rootNavigator: true);
  var open = true;
  unawaited(
    showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (_) => _ProgressDialog(message),
    ).whenComplete(() => open = false),
  );
  try {
    return await task();
  } finally {
    if (open && navigator.mounted) navigator.pop();
  }
}

class _ProgressDialog extends StatelessWidget {
  const _ProgressDialog(this.message);

  final String message;

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: false,
      child: Dialog(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 20),
          child: Semantics(
            liveRegion: true,
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                const SizedBox.square(
                  dimension: 20,
                  child: CircularProgressIndicator(strokeWidth: 2.5),
                ),
                const SizedBox(width: 16),
                Flexible(child: Text(message)),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// 删除并清理来源上的文件：提交删除任务后等后台任务结束，期间显示进度，结束后用 SnackBar
/// 写结果。返回是否已经删除；失败或还没删完时返回 false，调用方不要离开页面。
Future<bool> runDeleteTask(
  BuildContext context,
  Dio dio,
  Future<int?> Function() submit,
) async {
  final ({bool done, String message}) outcome;
  try {
    outcome = await runWithProgress(context, '正在删除并清理来源上的文件…', () async {
      final taskId = await submit();
      if (taskId == null) {
        return (done: true, message: '已提交删除任务，来源上的文件会在后台清理');
      }
      final task = await waitAdminTask(dio, taskId, attempts: 30);
      if (task.active) {
        return (done: false, message: '删除仍在后台进行，可以在后台任务里查看进度');
      }
      return task.status == 'succeeded'
          ? (done: true, message: '已删除，来源上的文件已清理')
          : (
              done: false,
              message: '删除没有完成：${task.error ?? '任务已取消'}。可以在后台任务里重试',
            );
    });
  } catch (error) {
    if (context.mounted) showApiErrorSnack(context, error);
    return false;
  }
  if (context.mounted) showSnack(context, outcome.message);
  return outcome.done;
}

/// 条目「⋯」菜单：所有用户都有收藏、稍后观看（[withWatchlist] 时）、加入播放列表、
/// 获取信息；管理员另有修正匹配、取消匹配、刷新元数据、重新读取文件、编辑字幕、
/// 同步此目录、加入合集和删除，按 Plex 的顺序用分隔线分组。
/// 季菜单和单集菜单各自只保留适用的项，顺序与此一致。
Future<ItemAction?> showItemActionsMenu(
  BuildContext context, {
  required String title,
  required MediaDetail detail,
  required bool admin,
  bool withWatchlist = false,
}) {
  final movie = detail.card.kind == MediaKind.movie;
  final common = [
    detail.favorite ? ItemAction.unfavorite : ItemAction.favorite,
    if (withWatchlist)
      detail.watchlisted ? ItemAction.unwatchlist : ItemAction.watchlist,
    ItemAction.addToPlaylist,
    ItemAction.info,
  ];
  final management = [
    ItemAction.rematch,
    ItemAction.unmatch,
    ItemAction.refresh,
    ItemAction.reenrich,
    if (movie) ItemAction.editSubtitles,
    ItemAction.sync,
    ItemAction.addToCollection,
  ];
  final groups = [
    common,
    if (admin) management,
    if (admin) [ItemAction.delete],
  ];
  return showPickerSheet<ItemAction>(
    context,
    builder: (context) => Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        PickerHeader(title: title),
        for (final (group, actions) in groups.indexed) ...[
          if (group > 0) const Divider(height: 17, color: CinemaColors.line),
          for (final (i, action) in actions.indexed)
            PickerOption(
              icon: action.icon,
              title: action.label,
              autofocus: group == 0 && i == 0,
              onTap: () => Navigator.pop(context, action),
            ),
        ],
      ],
    ),
  );
}

/// 弹出操作菜单并执行选中的操作，海报卡和详情页共用。
/// [detail] 为空时先取一次详情（菜单里的收藏、稍后观看状态以它为准）。
/// 条目被取消匹配或删除后，[inDetail] 时离开详情页回到上一页（没有时回到所在资料库）。
Future<void> showItemActions(
  BuildContext context,
  WidgetRef ref,
  MediaCard item, {
  MediaDetail? detail,
  bool inDetail = false,
  bool withWatchlist = false,
}) async {
  final media = detail ?? await _loadDetail(context, ref, item);
  if (media == null || !context.mounted) return;
  // 等登录用户就绪：菜单按角色过滤，读的时候可能还没加载完
  final admin =
      (await ref.read(authControllerProvider.future))?.isAdmin ?? false;
  if (!context.mounted) return;
  final action = await showItemActionsMenu(
    context,
    title: item.title,
    detail: media,
    admin: admin,
    withWatchlist: withWatchlist,
  );
  if (action == null || !context.mounted) return;
  final dio = ref.read(apiClientProvider);
  // 先提示再刷新：刷新列表时海报卡可能被重建，之后就拿不到它的 context 了
  void toast(String message) {
    if (context.mounted) showSnack(context, message);
  }

  void invalidate() => ref
    ..invalidate(mediaDetailProvider(item.id))
    ..invalidate(homeFeedProvider)
    ..invalidate(libraryItemsProvider)
    ..invalidate(searchResultsProvider);
  void leave() {
    if (!inDetail || !context.mounted) return;
    context.canPop() ? context.pop() : context.go('/library/${item.libraryId}');
  }

  Future<T> progress<T>(Future<T> Function() task) =>
      runWithProgress(context, '正在${action.label}…', task);

  try {
    switch (action) {
      case ItemAction.favorite || ItemAction.unfavorite:
        final favorite = action == ItemAction.favorite;
        await setFavorite(ref, item, favorite: favorite);
        toast(favorite ? '已收藏' : '已取消收藏');
      case ItemAction.watchlist || ItemAction.unwatchlist:
        final watchlisted = action == ItemAction.watchlist;
        await setWatchlisted(ref, item.id, watchlisted: watchlisted);
        toast(watchlisted ? '已加入稍后观看' : '已移出稍后观看');
      case ItemAction.rematch:
        await context.push('/settings/manage/items/${item.id}');
      case ItemAction.editSubtitles:
        final loaded = await progress<MediaDetail>(
          () => fetchMediaDetail(dio, ref.read(apiBaseUrlProvider), item.id),
        );
        if (!context.mounted) return;
        await showEditSubtitlesDialog(
          context,
          title: item.title,
          versions: loaded.versions,
          onChanged: invalidate,
        );
      case ItemAction.unmatch:
        if (!await showConfirmDialog(
              context,
              title: '取消「${item.title}」的匹配？',
              body: '只移除元数据匹配，来源上的文件和版本会保留，并回到未匹配列表。',
              action: '取消匹配',
            ) ||
            !context.mounted) {
          return;
        }
        await progress(() => unmatchItem(dio, item.id));
        toast('已取消匹配，作品已进入未匹配列表');
        invalidate();
        leave();
      case ItemAction.refresh:
        await progress(() => refreshItem(dio, item.id));
        toast(refreshedMessage);
        invalidate();
        ref.invalidate(seasonProvider);
      case ItemAction.reenrich:
        final result = await progress(() => reenrichItem(dio, item.id));
        toast(reenrichMessage(result));
        invalidate();
      case ItemAction.sync:
        await progress(() => syncItemDirs(dio, item.id));
        toast(syncStartedMessage);
      case ItemAction.info:
        if (admin) {
          final info = await progress(() => itemInfo(dio, item.id));
          if (context.mounted) await _showInfo(context, item.title, info);
        } else {
          await _showMediaInfo(context, media);
        }
      case ItemAction.addToPlaylist:
        await addToPlaylist(context, ref, itemId: item.id);
      case ItemAction.addToCollection:
        await _addToCollection(context, ref, item, toast);
      case ItemAction.delete:
        if (!await confirmDeleteItem(context, item.title) || !context.mounted) {
          return;
        }
        final deleted = await runDeleteTask(
          context,
          dio,
          () => deleteItem(dio, item.id),
        );
        if (!deleted) return;
        invalidate();
        leave();
    }
  } catch (error) {
    toast(ApiError.from(error).message);
  }
}

/// 菜单里的收藏和稍后观看状态来自详情；没有时现取一次，失败就提示且不打开菜单。
Future<MediaDetail?> _loadDetail(
  BuildContext context,
  WidgetRef ref,
  MediaCard item,
) async {
  try {
    return await ref.read(mediaDetailProvider(item.id).future);
  } catch (error) {
    if (context.mounted) showApiErrorSnack(context, error);
    return null;
  }
}

String _subtitleLabel(MediaSubtitle subtitle) {
  final name = languageName(subtitle.language) ?? subtitle.label;
  return subtitle.isOnline ? '$name · 在线' : name;
}

/// 获取信息（普通用户）：只读查看版本、画面、音频和字幕。流信息由内置播放器
/// 播过之后才有（ADR 0019），没有时说明一句，不显示空标题。
Future<void> _showMediaInfo(BuildContext context, MediaDetail detail) {
  final textTheme = Theme.of(context).textTheme;
  Widget heading(String text) => Padding(
    padding: const EdgeInsets.only(top: 12, bottom: 4),
    child: Text(text, style: textTheme.titleSmall),
  );
  Widget note(String text) => Padding(
    padding: const EdgeInsets.only(top: 2),
    child: Text(text, style: textTheme.bodySmall),
  );
  final versions = detail.versions;
  return showDialog<void>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text('「${detail.card.title}」信息'),
      content: SizedBox(
        width: 520,
        child: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              heading('版本 ${versions.length} 个'),
              if (versions.isEmpty) note('还没有可播放的版本'),
              for (final version in versions) ...[
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(
                    version.label,
                    style: textTheme.bodyMedium!.copyWith(
                      color: CinemaColors.text0,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
                if (version.media case final info?) ...[
                  if (videoSummary(info) case final video when video.isNotEmpty)
                    note('画面：$video'),
                  if (info.audio.isNotEmpty)
                    note('音频：${info.audio.map(audioSummary).join('、')}'),
                  if (info.subtitles.isNotEmpty)
                    note(
                      '内封字幕：${info.subtitles.map(embeddedSubtitleSummary).join('、')}',
                    ),
                ] else
                  note('播放一次后显示画面、音频和内封字幕'),
                if (version.subtitles.isNotEmpty)
                  note(
                    '外挂字幕：${version.subtitles.map(_subtitleLabel).join('、')}',
                  ),
              ],
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          autofocus: true,
          onPressed: () => Navigator.pop(context),
          child: const Text('关闭'),
        ),
      ],
    ),
  );
}

/// 获取信息（管理员）：列出每个版本的文件路径、大小和状态，以及外挂字幕。
Future<void> _showInfo(
  BuildContext context,
  String title,
  Map<String, dynamic> info,
) {
  final versions = (info['versions'] as List<dynamic>?) ?? const [];
  final subtitles = (info['subtitles'] as List<dynamic>?) ?? const [];
  final textTheme = Theme.of(context).textTheme;
  Widget heading(String text) => Padding(
    padding: const EdgeInsets.only(top: 12, bottom: 4),
    child: Text(text, style: textTheme.titleSmall),
  );
  Widget file(String name, String? detail) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 4),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(name, style: textTheme.bodyMedium),
        if (detail != null) SelectableText(detail, style: textTheme.bodySmall),
      ],
    ),
  );
  return showDialog<void>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text('「$title」信息'),
      content: SizedBox(
        width: 520,
        child: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              heading('版本 ${versions.length} 个'),
              for (final raw in versions)
                if (raw case final Map<String, dynamic> v)
                  file(
                    [
                      v['label'] as String? ?? '未命名版本',
                      formatSize(v['size'] as int?),
                      if (v['state'] != 'active') '已失效',
                    ].join(' · '),
                    v['rel_path'] as String?,
                  ),
              heading('外挂字幕 ${subtitles.length} 条'),
              for (final raw in subtitles)
                if (raw case final Map<String, dynamic> s)
                  file(
                    s['label'] as String? ?? s['language'] as String? ?? '字幕',
                    s['rel_path'] as String?,
                  ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          autofocus: true,
          onPressed: () => Navigator.pop(context),
          child: const Text('关闭'),
        ),
      ],
    ),
  );
}

/// 加入合集：只能选同一资料库里的合集。
Future<void> _addToCollection(
  BuildContext context,
  WidgetRef ref,
  MediaCard item,
  void Function(String) toast,
) async {
  final dio = ref.read(apiClientProvider);
  final collections = await libraryCollectionChoices(dio, item.libraryId);
  if (!context.mounted) return;
  if (collections.isEmpty) {
    toast('这个资料库还没有合集，可以在资料库的「合集」里新建');
    return;
  }
  final picked = await showChoicePicker<Map<String, dynamic>>(
    context,
    title: '加入合集',
    subtitle: item.title,
    choices: [
      for (final collection in collections)
        (
          value: collection,
          label: collection['title'] as String? ?? '未命名合集',
          subtitle: null,
        ),
    ],
    current: null,
  );
  if (picked == null || !context.mounted) return;
  final collection = picked.$1;
  await runWithProgress(
    context,
    '正在加入合集…',
    () => addItemToCollection(dio, collection['id'] as int, item.id),
  );
  toast('已加入「${collection['title'] ?? '合集'}」');
  ref.invalidate(collectionDetailProvider(collection['id'] as int));
}
