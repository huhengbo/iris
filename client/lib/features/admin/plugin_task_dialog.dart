import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/providers.dart';
import 'plugin_catalog_api.dart';
import 'tasks_api.dart';

String _pluginPhaseLabel(String phase) => switch (phase) {
  'download' => '下载中…',
  'verify' => '校验中…',
  'extract' => '解压中…',
  'dependencies' => '安装依赖中…',
  'start' => '启动中…',
  'starting' || 'preparing' => '准备中…',
  'done' => '完成中…',
  _ => '处理中…',
};

/// 弹出插件任务进度对话框，轮询后台任务状态直至完成或失败
Future<bool> showPluginTaskProgressDialog(
  BuildContext context, {
  required WidgetRef ref,
  required int taskId,
  required String title,
}) async {
  final result = await showDialog<bool>(
    context: context,
    barrierDismissible: false,
    builder: (context) =>
        _PluginTaskDialog(ref: ref, taskId: taskId, title: title),
  );
  return result ?? false;
}

class _PluginTaskDialog extends StatefulWidget {
  const _PluginTaskDialog({
    required this.ref,
    required this.taskId,
    required this.title,
  });

  final WidgetRef ref;
  final int taskId;
  final String title;

  @override
  State<_PluginTaskDialog> createState() => _PluginTaskDialogState();
}

class _PluginTaskDialogState extends State<_PluginTaskDialog> {
  Timer? _timer;
  AdminTask? _task;
  String? _error;
  bool _finished = false;

  @override
  void initState() {
    super.initState();
    _poll();
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _poll() async {
    try {
      final dio = widget.ref.read(apiClientProvider);
      final response = await dio.get<Map<String, dynamic>>(
        '$tasksPath/${widget.taskId}',
      );
      final task = AdminTask.fromJson(response.data!);
      if (!mounted) return;
      setState(() => _task = task);

      if (task.status == 'succeeded' || task.status == 'completed') {
        _finished = true;
        _timer?.cancel();
        Navigator.of(context).pop(true);
      } else if (task.status == 'failed' || task.status == 'cancelled') {
        _finished = true;
        _timer?.cancel();
        setState(() {
          _error = pluginTaskErrorMessage(task);
        });
      } else {
        _timer = Timer(const Duration(seconds: 1), _poll);
      }
    } catch (e) {
      if (!mounted) return;
      _finished = true;
      _timer?.cancel();
      setState(() {
        _error = '查询任务状态失败: $e';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final task = _task;
    final isError = _error != null;

    return AlertDialog(
      title: Text(widget.title),
      content: ConstrainedBox(
        constraints: const BoxConstraints(minWidth: 280),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (isError) ...[
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Icon(Icons.error_outline, color: CinemaColors.error),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Text(
                      _error!,
                      style: Theme.of(context).textTheme.bodyMedium
                          ?.copyWith(color: CinemaColors.error),
                    ),
                  ),
                ],
              ),
            ] else ...[
              Text(
                task != null ? _pluginPhaseLabel(task.phase) : '正在启动任务…',
                style: Theme.of(context).textTheme.bodyMedium,
              ),
              const SizedBox(height: 16),
              if (task != null && task.progressTotal > 0)
                LinearProgressIndicator(
                  value: task.progressDone / task.progressTotal,
                  color: CinemaColors.amber,
                )
              else
                const LinearProgressIndicator(color: CinemaColors.amber),
            ],
          ],
        ),
      ),
      actions: [
        if (_finished && isError)
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text('关闭'),
          ),
      ],
    );
  }
}
