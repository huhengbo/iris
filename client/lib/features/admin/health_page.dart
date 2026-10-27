import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/format.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/focus_ring.dart';
import 'admin_widgets.dart';
import 'health_api.dart';

class HealthPage extends ConsumerWidget {
  const HealthPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = ref.watch(healthReportProvider);
    // 刷新失败时 state 仍带着上一次的结果，继续展示并提示数据已过期
    final stale = state.hasError && state.hasValue;
    return Scaffold(
      appBar: AppBar(
        title: const Text('系统健康'),
        actions: [
          IconButton(
            tooltip: '刷新',
            onPressed: () => ref.invalidate(healthReportProvider),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: AsyncView(
        value: stale ? AsyncData(state.requireValue) : state,
        onRetry: () => ref.invalidate(healthReportProvider),
        builder: (report) => AdminListWidth(
          builder: (padding) => ListView(
            padding: padding,
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(
                  CinemaSpacing.gutter,
                  16,
                  CinemaSpacing.gutter,
                  8,
                ),
                child: Text(
                  '检查于 ${formatDateTime(report.checkedAt)} · 数据库结构 ${report.schemaVersion}',
                  style: Theme.of(context).textTheme.bodySmall
                      ?.copyWith(color: CinemaColors.text2),
                ),
              ),
              if (stale)
                ListTile(
                  key: const ValueKey('health-stale'),
                  contentPadding: const EdgeInsets.symmetric(
                    horizontal: CinemaSpacing.gutter,
                  ),
                  leading: const Icon(
                    Icons.cloud_off,
                    color: CinemaColors.amber,
                  ),
                  title: Text(
                    '刷新失败，以下是 ${formatDateTime(report.checkedAt)} 的结果',
                  ),
                  subtitle: Text(ApiError.from(state.error!).message),
                  trailing: TextButton(
                    onPressed: () => ref.invalidate(healthReportProvider),
                    child: const Text('重试'),
                  ),
                ),
              for (final check in report.checks) _HealthTile(check: check),
            ],
          ),
        ),
      ),
    );
  }
}

class _HealthTile extends StatelessWidget {
  const _HealthTile({required this.check});

  final HealthCheck check;

  @override
  Widget build(BuildContext context) {
    final (icon, color) = switch (check.state) {
      'ok' => (Icons.check_circle, CinemaColors.success),
      'warning' => (Icons.warning_amber_rounded, CinemaColors.amber),
      _ => (Icons.error, CinemaColors.error),
    };
    final tile = ListTile(
      contentPadding: const EdgeInsets.symmetric(
        horizontal: CinemaSpacing.gutter,
      ),
      leading: Icon(icon, color: color),
      title: Text(check.title),
      subtitle: Text(check.message),
      trailing: check.actionPath == null
          ? null
          : const Icon(Icons.chevron_right),
      onTap: check.actionPath == null
          ? null
          : () => context.push(check.actionPath!),
    );
    return check.actionPath == null ? tile : FocusRing(child: tile);
  }
}
