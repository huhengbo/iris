import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../core/format.dart';
import '../features/admin/summary_api.dart';
import '../features/admin/tasks_api.dart';
import '../features/auth/account_switcher.dart' show AccountAvatar;
import 'theme.dart';

/// 顶栏的活动下拉（仅管理员）：正在播放的会话和运行中的后台任务，
/// 数据复用管理接口。底部一项进「状态 › 仪表盘」。
class ActivityMenu extends ConsumerWidget {
  const ActivityMenu({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final activity = ref.watch(adminActivityProvider);
    final tasks = ref.watch(adminTasksProvider);
    final playing = activity.value?.nowPlaying ?? const <PlayActivity>[];
    final running = [
      for (final task in tasks.value ?? const <AdminTask>[])
        if (task.active) task,
    ];
    final busy = playing.isNotEmpty || running.isNotEmpty;
    return PopupMenuButton<void>(
      tooltip: '活动',
      icon: Badge(
        isLabelVisible: busy,
        backgroundColor: CinemaColors.amber,
        child: const Icon(Icons.bolt_outlined),
      ),
      constraints: const BoxConstraints(minWidth: 320, maxWidth: 360),
      itemBuilder: (context) => [
        PopupMenuItem<void>(
          enabled: false,
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxHeight: 360),
            child: SizedBox(
              width: 300,
              child: SingleChildScrollView(
                child: _Panel(
                  playing: playing,
                  running: running,
                  loading: activity.isLoading || tasks.isLoading,
                  failed: activity.hasError || tasks.hasError,
                ),
              ),
            ),
          ),
        ),
        const PopupMenuDivider(),
        PopupMenuItem<void>(
          onTap: () => context.push('/settings/status/dashboard'),
          child: const Row(
            children: [
              Icon(Icons.dashboard_outlined, size: 18),
              SizedBox(width: 12),
              Text('查看仪表盘'),
            ],
          ),
        ),
      ],
    );
  }
}

class _Panel extends StatelessWidget {
  const _Panel({
    required this.playing,
    required this.running,
    required this.loading,
    required this.failed,
  });

  final List<PlayActivity> playing;
  final List<AdminTask> running;
  final bool loading;
  final bool failed;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    if (playing.isEmpty && running.isEmpty) {
      return Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Text(
          failed
              ? '活动加载失败，稍后再试'
              : loading
              ? '正在加载…'
              : '现在没有正在播放的内容，也没有运行中的任务',
          style: text.bodySmall!.copyWith(
            color: failed ? CinemaColors.error : CinemaColors.text2,
          ),
        ),
      );
    }
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (playing.isNotEmpty) ...[
          _Heading('正在播放'),
          for (final play in playing) _PlayingRow(play: play),
        ],
        if (running.isNotEmpty) ...[
          _Heading('后台任务'),
          for (final task in running) _TaskRow(task: task),
        ],
      ],
    );
  }
}

class _Heading extends StatelessWidget {
  const _Heading(this.title);

  final String title;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(0, 8, 0, 4),
    child: Text(
      title,
      style: Theme.of(context).textTheme.labelLarge!
          .copyWith(color: CinemaColors.text2),
    ),
  );
}

/// 一条正在播放：首字头像 + 片名（剧集带季集）+「用户 · 进度」。
class _PlayingRow extends StatelessWidget {
  const _PlayingRow({required this.play});

  final PlayActivity play;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final position = play.position;
    final duration = play.duration;
    final hasProgress = position != null && duration != null && duration > 0;
    final detail = [
      play.username,
      if (hasProgress)
        '${formatClock(Duration(seconds: position))} / '
            '${formatClock(Duration(seconds: duration))}',
    ].join(' · ');
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          AccountAvatar(username: play.username, radius: 14),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  play.label,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: text.bodySmall!.copyWith(color: CinemaColors.text0),
                ),
                Text(
                  detail,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: text.bodySmall!.copyWith(color: CinemaColors.text2),
                ),
                if (hasProgress)
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: LinearProgressIndicator(
                      value: (position / duration).clamp(0.0, 1.0),
                      minHeight: 3,
                      color: CinemaColors.amber,
                      backgroundColor: CinemaColors.surface3,
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// 一条运行中的后台任务：类型 + 进度。
class _TaskRow extends StatelessWidget {
  const _TaskRow({required this.task});

  final AdminTask task;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final progress = task.progressTotal > 0
        ? '${task.progressDone} / ${task.progressTotal}'
        : null;
    final detail = [
      ?progress,
      if (task.status == 'cancel_requested') '正在取消',
    ].join(' · ');
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        children: [
          const Icon(Icons.sync, size: 18, color: CinemaColors.text2),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  taskKindLabel(task.kind),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: text.bodySmall!.copyWith(color: CinemaColors.text0),
                ),
                if (detail.isNotEmpty)
                  Text(
                    detail,
                    style: text.bodySmall!.copyWith(color: CinemaColors.text2),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// 后台任务的类型名。和「后台任务」页（`features/admin/tasks_page.dart`）
/// 的 `_kindLabel` 是同一套名字，那边是私有的，这里按显示需要再列一份。
String taskKindLabel(String kind) => switch (kind) {
  'sync' => '同步',
  'backup_create' => '创建备份',
  'backup_restore' => '恢复备份',
  'delete_media' => '删除媒体',
  'delete_season' => '删除整季',
  'delete_episode' => '删除单集',
  'delete_pending' => '删除待处理',
  'refresh_metadata' => '刷新元数据',
  'refresh_library_metadata' => '刷新资料库元数据',
  'refresh_season' => '刷新整季',
  'refresh_episode' => '刷新单集',
  'download_subtitles' => '下载字幕',
  'reenrich_item' => '重新读取文件',
  'merge_items' => '合并条目',
  'unmatch_item' => '取消匹配',
  'batch_items' => '批量编辑条目',
  'batch_assign_work' => '批量指定作品',
  'batch_rematch_work' => '批量重新匹配',
  'batch_assign_file' => '批量指定文件',
  'batch_ignore_file' => '批量忽略文件',
  'batch_restore_file' => '批量恢复文件',
  'empty_recycle_bin' => '清空回收站',
  'cleanup_images' => '清理图片',
  'plugin:install' => '安装插件',
  'plugin:upgrade' => '升级插件',
  'plugin:reinstall' => '重装插件',
  'plugin:dependencies' => '安装插件依赖',
  'scheduled:backup' => '定时备份',
  'scheduled:optimize_database' => '定时优化数据库',
  'scheduled:vacuum' => '定时压缩数据库',
  'scheduled:cleanup_images' => '定时清理图片',
  'scheduled:cleanup_sessions' => '定时清理登录会话',
  'scheduled:cleanup_play_events' => '定时清理播放记录',
  'scheduled:refresh_metadata' => '定时刷新元数据',
  'scheduled:download_subtitles' => '定时下载字幕',
  'scheduled:empty_recycle_bin' => '定时清空回收站',
  'scheduled:check_plugin_updates' => '定时检查插件更新',
  _ => kind,
};
