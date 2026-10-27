import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/format.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/focus_ring.dart';
import 'admin_widgets.dart';
import 'maintenance_api.dart';

/// 日志：服务进程最近的日志，新的在前。点一行看完整内容。
class LogsPage extends ConsumerWidget {
  const LogsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final logs = ref.watch(serverLogsProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('日志'),
        actions: [
          IconButton(
            tooltip: '刷新',
            onPressed: () => ref.invalidate(serverLogsProvider),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: AsyncView(
        value: logs,
        onRetry: () => ref.invalidate(serverLogsProvider),
        builder: (items) => items.isEmpty
            ? const EmptyState(
                icon: Icons.article_outlined,
                title: '暂无日志',
                message: '服务启动后的运行日志会显示在这里，重启后清空。',
              )
            : RefreshIndicator(
                onRefresh: () async => ref.refresh(serverLogsProvider.future),
                child: AdminListWidth(
                  top: 8,
                  builder: (padding) => ListView.builder(
                    padding: padding,
                    itemCount: items.length,
                    itemBuilder: (context, index) =>
                        _LogTile(entry: items[index]),
                  ),
                ),
              ),
      ),
    );
  }
}

/// 日志级别的图标和颜色：错误暖红，警告琥珀，其余次级文字色
(IconData, Color) _levelStyle(String level) => switch (level) {
  'error' || 'critical' => (Icons.error_outline, CinemaColors.error),
  'warning' => (Icons.warning_amber_rounded, CinemaColors.amber),
  _ => (Icons.notes, CinemaColors.text2),
};

/// 日志要精确到秒：「1月20日 08:05:09」
String _logTime(DateTime time) =>
    '${formatDateTime(time)}:${time.second.toString().padLeft(2, '0')}';

class _LogTile extends StatelessWidget {
  const _LogTile({required this.entry});

  final ServerLogEntry entry;

  void _open(BuildContext context) => showDialog<void>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text(entry.module.isEmpty ? '日志' : entry.module),
      content: SingleChildScrollView(child: SelectableText(entry.message)),
      actions: [
        TextButton(
          autofocus: true,
          onPressed: () => Navigator.pop(context),
          child: const Text('关闭'),
        ),
      ],
    ),
  );

  @override
  Widget build(BuildContext context) {
    final (icon, color) = _levelStyle(entry.level);
    final at = entry.at;
    return FocusRing(
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(
          horizontal: CinemaSpacing.gutter,
        ),
        leading: Icon(icon, color: color),
        title: Text(
          entry.message,
          maxLines: 3,
          overflow: TextOverflow.ellipsis,
        ),
        subtitle: Text(
          [
            ?(at == null ? null : _logTime(at)),
            entry.module,
          ].where((part) => part.isNotEmpty).join(' · '),
        ),
        onTap: () => _open(context),
      ),
    );
  }
}
