import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/providers.dart';
import '../../core/format.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/snack.dart';
import 'admin_widgets.dart';
import 'sync_page.dart' show syncPhaseLabel;
import 'tasks_api.dart';

class TasksPage extends ConsumerStatefulWidget {
  const TasksPage({super.key});

  @override
  ConsumerState<TasksPage> createState() => _TasksPageState();
}

class _TasksPageState extends ConsumerState<TasksPage> {
  Timer? _poll;

  @override
  void initState() {
    super.initState();
    // 只在有任务进行中时刷新，都结束了就停下，不一直请求
    _poll = Timer.periodic(const Duration(seconds: 2), (_) {
      final tasks = ref.read(adminTasksProvider).value;
      if (tasks != null && tasks.any((task) => task.active)) {
        ref.invalidate(adminTasksProvider);
      }
    });
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final tasks = ref.watch(adminTasksProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('后台任务'),
        actions: [
          IconButton(
            tooltip: '刷新',
            onPressed: () => ref.invalidate(adminTasksProvider),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: AsyncView(
        value: tasks,
        onRetry: () => ref.invalidate(adminTasksProvider),
        builder: (items) => items.isEmpty
            ? const EmptyState(
                icon: Icons.checklist_outlined,
                title: '还没有后台任务',
                message: '批量处理、同步、备份和删除会在这里留下记录。',
              )
            : RefreshIndicator(
                onRefresh: () async => ref.refresh(adminTasksProvider.future),
                child: AdminListWidth(
                  top: 8,
                  builder: (padding) => ListView.separated(
                    padding: padding,
                    itemCount: items.length,
                    separatorBuilder: (_, _) => const Divider(height: 1),
                    itemBuilder: (context, index) =>
                        _TaskTile(task: items[index]),
                  ),
                ),
              ),
      ),
    );
  }
}

class _TaskTile extends ConsumerStatefulWidget {
  const _TaskTile({required this.task});

  final AdminTask task;

  @override
  ConsumerState<_TaskTile> createState() => _TaskTileState();
}

class _TaskTileState extends ConsumerState<_TaskTile> {
  // 取消/重试请求进行中，按钮换成转圈并禁用，防止重复提交
  bool _busy = false;

  Future<void> _act(Future<void> Function() request, String done) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await request();
      if (mounted) showSnack(context, done);
      ref.invalidate(adminTasksProvider);
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    }
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) {
    final task = widget.task;
    final progress = task.progressTotal > 0
        ? '${task.progressDone} / ${task.progressTotal}'
        : null;
    final finished = task.finishedAt;
    final subtitle = [
      if (task.active) ...[
        task.kind == 'sync'
            ? syncPhaseLabel(task.phase)
            : _phaseLabel(task.phase),
        ?progress,
        '${formatAgo(task.startedAt)} 开始',
      ] else
        '${formatAgo(finished ?? task.startedAt)} 结束',
      ?task.error,
    ].join(' · ');
    final api = ref.read(apiClientProvider);
    return FocusRing(
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(
          horizontal: CinemaSpacing.gutter,
        ),
        title: Text(_kindLabel(task.kind)),
        subtitle: Text(subtitle, maxLines: 3, overflow: TextOverflow.ellipsis),
        // 同步任务的详情（统计、问题文件、待确认的删除）在同步页
        onTap: task.kind == 'sync'
            ? () => context.push('/settings/status/sync')
            : null,
        leading: Icon(
          _statusIcon(task.status),
          color: _statusColor(task.status),
        ),
        trailing: _busy
            ? const SizedBox.square(
                dimension: 20,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            : task.active
            ? TextButton(
                onPressed: task.status == 'cancel_requested'
                    ? null
                    : () => _act(() => cancelAdminTask(api, task.id), '已请求取消'),
                child: const Text('取消'),
              )
            : task.status == 'failed' || task.status == 'cancelled'
            ? TextButton(
                onPressed: () =>
                    _act(() => retryAdminTask(api, task.id), '已重新开始'),
                child: const Text('重试'),
              )
            : Text(_statusLabel(task.status)),
      ),
    );
  }
}

String _kindLabel(String kind) => switch (kind) {
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
  'scheduled:cleanup_ai_chats' => '定时清理 AI 对话',
  'scheduled:refresh_metadata' => '定时刷新元数据',
  'scheduled:download_subtitles' => '定时下载字幕',
  'scheduled:empty_recycle_bin' => '定时清空回收站',
  'scheduled:check_plugin_updates' => '定时检查插件更新',
  _ => kind,
};

/// 非同步任务的阶段名；同步任务用同步页的 [syncPhaseLabel]
String _phaseLabel(String phase) => switch (phase) {
  'starting' || 'preparing' => '准备中',
  'done' => '收尾',
  'waiting' => '等待重试',
  'resuming' => '继续中',
  'writing' => '写入备份',
  'restoring' => '恢复数据',
  'planning' => '统计要处理的文件',
  'deleting_cloud' => '删除来源文件',
  'placing' => '更新文件信息',
  'scraping' => '获取元数据',
  'download' => '下载中',
  'verify' => '校验中',
  'extract' => '解压中',
  'dependencies' => '安装依赖中',
  'start' => '启动中',
  _ => '处理中',
};

String _statusLabel(String status) => switch (status) {
  'succeeded' => '成功',
  'failed' => '失败',
  'cancel_requested' => '取消中',
  'cancelled' => '已取消',
  _ => '进行中',
};

IconData _statusIcon(String status) => switch (status) {
  'succeeded' => Icons.check_circle_outline,
  'failed' => Icons.error_outline,
  'cancelled' || 'cancel_requested' => Icons.cancel_outlined,
  _ => Icons.sync,
};

// 已取消是中性结果，不用琥珀色（琥珀只留给主操作和「暂停」）
Color _statusColor(String status) => switch (status) {
  'succeeded' => CinemaColors.success,
  'failed' => CinemaColors.error,
  'cancelled' || 'cancel_requested' => CinemaColors.text2,
  _ => CinemaColors.text1,
};
