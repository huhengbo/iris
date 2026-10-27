import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/format.dart';
import '../../core/providers.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/snack.dart';
import '../admin/catalog_api.dart';
import '../admin/item_actions.dart';
import '../library/browse_api.dart';

/// 单集的称呼：知道季号时写「第 N 季 第 N 集」，否则「第 N 集」。
String _episodeLabel(WidgetRef ref, int seasonId, Episode episode) {
  final season = ref.read(seasonProvider(seasonId)).value?.number;
  return season == null
      ? '第 ${episode.number} 集'
      : formatEpisode(season, episode.number);
}

/// 管理员编辑单集的标题和简介，保存后刷新所在的季。剧集详情页的分集行和单集详情页共用。
Future<void> editEpisode(
  BuildContext context,
  WidgetRef ref, {
  required Episode episode,
  required int seasonId,
}) async {
  final values = await showDialog<Map<String, String>>(
    context: context,
    builder: (_) => _EditDialog(
      episode: episode,
      label: _episodeLabel(ref, seasonId, episode),
    ),
  );
  if (values == null || !context.mounted) return;
  final ok = await _run(context, ref, seasonId, '正在保存…', () {
    return patchEpisodeMetadata(
      ref.read(apiClientProvider),
      episode.id,
      values,
    );
  });
  if (ok && context.mounted) showSnack(context, '已保存');
}

/// 管理员从元数据插件重新拉取单集信息，完成后刷新所在的季。
Future<void> refreshEpisodeMetadata(
  BuildContext context,
  WidgetRef ref, {
  required int episodeId,
  required int seasonId,
}) async {
  final ok = await _run(
    context,
    ref,
    seasonId,
    '正在${ItemAction.refresh.label}…',
    () => refreshEpisode(ref.read(apiClientProvider), episodeId),
  );
  if (ok && context.mounted) showSnack(context, refreshedMessage);
}

/// 删除单集前二次确认；服务端在后台清理对应的来源文件后才从资料库移除，
/// 这里等后台任务结束再刷新。删除成功后连剧集详情一起刷新：集数和接着看的一集都可能变。
/// 返回是否已删除，调用方据此决定是否离开单集页。
Future<bool> deleteEpisodeFromDetail(
  BuildContext context,
  WidgetRef ref, {
  required Episode episode,
  required int itemId,
  required int seasonId,
}) async {
  final confirmed = await showConfirmDialog(
    context,
    title: '删除「${episode.name ?? _episodeLabel(ref, seasonId, episode)}」？',
    body: '会删除这一集在来源上的视频和字幕文件，并从资料库移除，操作不可恢复。',
    action: ItemAction.delete.label,
    destructive: true,
  );
  if (!confirmed || !context.mounted) return false;
  final dio = ref.read(apiClientProvider);
  final deleted = await runDeleteTask(
    context,
    dio,
    () => deleteEpisode(dio, episode.id),
  );
  if (!deleted) return false;
  ref
    ..invalidate(seasonProvider(seasonId))
    ..invalidate(mediaDetailProvider(itemId));
  return true;
}

Future<bool> _run(
  BuildContext context,
  WidgetRef ref,
  int seasonId,
  String progress,
  Future<void> Function() call,
) async {
  try {
    await runWithProgress(context, progress, call);
    ref.invalidate(seasonProvider(seasonId));
    return true;
  } catch (error) {
    if (context.mounted) showApiErrorSnack(context, error);
    return false;
  }
}

class _EditDialog extends StatefulWidget {
  const _EditDialog({required this.episode, required this.label});

  final Episode episode;
  final String label;

  @override
  State<_EditDialog> createState() => _EditDialogState();
}

class _EditDialogState extends State<_EditDialog> {
  // 由对话框自己释放：关闭动画期间输入框还在用
  late final _name = TextEditingController(text: widget.episode.name ?? '');
  late final _overview = TextEditingController(
    text: widget.episode.overview ?? '',
  );

  @override
  void dispose() {
    _name.dispose();
    _overview.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      // 输入法占去半个屏幕时（TV、横屏）标题和输入框一起滚动
      scrollable: true,
      title: Text('编辑${widget.label}'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          TextField(
            controller: _name,
            autofocus: true,
            decoration: const InputDecoration(labelText: '标题'),
          ),
          TextField(
            controller: _overview,
            minLines: 2,
            maxLines: 4,
            decoration: const InputDecoration(labelText: '简介'),
          ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('取消'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(context, {
            'name': _name.text.trim(),
            'overview': _overview.text.trim(),
          }),
          child: const Text('保存'),
        ),
      ],
    );
  }
}
