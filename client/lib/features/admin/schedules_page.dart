import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/format.dart';
import '../../core/providers.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/snack.dart';
import 'admin_widgets.dart';
import 'busy_icon.dart';
import 'maintenance_api.dart';
import 'tasks_api.dart';

/// 计划任务：定期执行的维护任务，可以开关，也可以立即运行一次。
class SchedulesPage extends ConsumerStatefulWidget {
  const SchedulesPage({super.key});

  @override
  ConsumerState<SchedulesPage> createState() => _SchedulesPageState();
}

class _SchedulesPageState extends ConsumerState<SchedulesPage> {
  /// 正在切换开关的任务
  final _toggling = <String>{};

  /// 正在运行的任务
  final _running = <String>{};

  Future<void> _toggle(ScheduledTask task, bool enabled) async {
    setState(() => _toggling.add(task.key));
    try {
      await setScheduledTaskEnabled(
        ref.read(apiClientProvider),
        task.key,
        enabled,
      );
      ref.invalidate(scheduledTasksProvider);
      await ref.read(scheduledTasksProvider.future);
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    }
    if (mounted) setState(() => _toggling.remove(task.key));
  }

  Future<void> _run(ScheduledTask task) async {
    final name = scheduledTaskLabel(task.key);
    setState(() => _running.add(task.key));
    try {
      final dio = ref.read(apiClientProvider);
      final id = await runScheduledTask(dio, task.key);
      // 等后台任务结束再提示结果
      final result = await waitAdminTask(dio, id);
      if (mounted) {
        showSnack(context, switch (result.status) {
          'succeeded' => '「$name」已完成',
          'failed' => '「$name」失败：${result.error ?? '原因未知'}',
          _ => '「$name」已开始，可在后台任务里查看进度',
        });
      }
      ref.invalidate(scheduledTasksProvider);
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    }
    if (mounted) setState(() => _running.remove(task.key));
  }

  @override
  Widget build(BuildContext context) {
    final tasks = ref.watch(scheduledTasksProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('计划任务'),
        actions: [
          IconButton(
            tooltip: '刷新',
            onPressed: () => ref.invalidate(scheduledTasksProvider),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: AsyncView(
        value: tasks,
        onRetry: () => ref.invalidate(scheduledTasksProvider),
        builder: (items) => items.isEmpty
            ? const EmptyState(
                icon: Icons.schedule_outlined,
                title: '还没有计划任务',
                message: '升级服务端后会自动加入备份、清理等维护任务。',
              )
            : RefreshIndicator(
                onRefresh: () async =>
                    ref.refresh(scheduledTasksProvider.future),
                child: AdminListWidth(
                  top: 8,
                  builder: (padding) => ListView.builder(
                    padding: padding,
                    itemCount: items.length,
                    itemBuilder: (context, index) {
                      final task = items[index];
                      return _ScheduleTile(
                        task: task,
                        toggling: _toggling.contains(task.key),
                        running: _running.contains(task.key),
                        onToggle: (value) => _toggle(task, value),
                        onRun: () => _run(task),
                      );
                    },
                  ),
                ),
              ),
      ),
    );
  }
}

class _ScheduleTile extends StatelessWidget {
  const _ScheduleTile({
    required this.task,
    required this.toggling,
    required this.running,
    required this.onToggle,
    required this.onRun,
  });

  final ScheduledTask task;
  final bool toggling;
  final bool running;
  final ValueChanged<bool> onToggle;
  final VoidCallback onRun;

  @override
  Widget build(BuildContext context) {
    final last = task.lastRunAt;
    final next = task.nextRunAt;
    final detail = [
      task.enabled ? scheduleIntervalLabel(task.interval) : '已停用',
      last == null ? '还没运行过' : '上次 ${formatAgo(last)}',
      if (task.enabled)
        next == null || next.isBefore(DateTime.now())
            ? '即将运行'
            : '下次 ${formatDateTime(next)}',
    ];
    return FocusRing(
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(
          horizontal: CinemaSpacing.gutter,
        ),
        leading: const Icon(Icons.schedule_outlined),
        title: Text(scheduledTaskLabel(task.key)),
        subtitle: Text(detail.join(' · ')),
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            running
                ? const BusyIcon()
                : IconButton(
                    tooltip: '立即运行',
                    onPressed: onRun,
                    icon: const Icon(Icons.play_arrow_outlined),
                  ),
            Switch(value: task.enabled, onChanged: toggling ? null : onToggle),
          ],
        ),
      ),
    );
  }
}
