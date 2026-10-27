import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../core/widgets/picker_sheet.dart';
import '../../core/widgets/text_prompt.dart';
import '../auth/auth_controller.dart';
import 'playlists_api.dart';

/// 详情页操作行里的「加入播放列表」，所有用户都有。
class PlaylistButton extends ConsumerWidget {
  const PlaylistButton({
    super.key,
    required this.itemId,
    this.episodeId,
    this.size = 44,
  });

  final int itemId;

  /// 单集页只加这一集
  final int? episodeId;
  final double size;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return IconButton.filledTonal(
      tooltip: '加入播放列表',
      iconSize: 24,
      style: IconButton.styleFrom(fixedSize: Size.square(size)),
      onPressed: () =>
          addToPlaylist(context, ref, itemId: itemId, episodeId: episodeId),
      icon: const Icon(Icons.playlist_add),
    );
  }
}

/// 加入播放列表：电影加整部，剧集按季和集的顺序加全部集，给了 [episodeId] 时只加这一集。
/// 只列出自己能改的列表（自己建的；管理员可以改全部），也可以当场新建一个。
Future<void> addToPlaylist(
  BuildContext context,
  WidgetRef ref, {
  required int itemId,
  int? episodeId,
}) async {
  final api = ref.read(playlistsApiProvider);
  final user = ref.read(authControllerProvider).value;
  final messenger = ScaffoldMessenger.of(context);
  // 加入后对话框和详情页可能都已关掉，提示挂在进来时的 ScaffoldMessenger 上
  void toast(String message) => messenger
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(message)));
  try {
    final playlists = [
      for (final raw in await api.list())
        if (raw case final Map<String, dynamic> playlist
            when user != null &&
                (user.isAdmin || playlist['user_id'] == user.id))
          playlist,
    ];
    if (!context.mounted) return;
    var id = await showPickerSheet<int>(
      context,
      builder: (context) => Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const PickerHeader(title: '加入播放列表'),
          for (final (i, playlist) in playlists.indexed)
            PickerOption(
              title: playlist['title'] as String? ?? '未命名列表',
              icon: Icons.playlist_play,
              autofocus: i == 0,
              onTap: () => Navigator.pop(context, playlist['id'] as int),
            ),
          PickerOption(
            title: '新建播放列表',
            icon: Icons.add,
            autofocus: playlists.isEmpty,
            // 0 表示新建
            onTap: () => Navigator.pop(context, 0),
          ),
        ],
      ),
    );
    if (id == null || !context.mounted) return;
    if (id == 0) {
      final title = await showTextPrompt(
        context,
        title: '新建播放列表',
        label: '名称',
        action: '创建',
      );
      if (title == null || title.isEmpty || !context.mounted) return;
      id = await api.create(title);
    }
    await api.addItem(id, itemId: itemId, episodeId: episodeId);
    ref
      ..invalidate(playlistsProvider)
      ..invalidate(playlistDetailProvider(id));
    toast('已加入播放列表');
  } catch (error) {
    toast(ApiError.from(error).message);
  }
}
