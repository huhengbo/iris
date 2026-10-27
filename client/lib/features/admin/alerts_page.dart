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

/// 警报：只列还没处理的，处理后从列表里去掉。
class AlertsPage extends ConsumerStatefulWidget {
  const AlertsPage({super.key});

  @override
  ConsumerState<AlertsPage> createState() => _AlertsPageState();
}

class _AlertsPageState extends ConsumerState<AlertsPage> {
  /// 正在标记的警报
  final _resolving = <int>{};

  Future<void> _resolve(AdminAlert alert) async {
    setState(() => _resolving.add(alert.id));
    try {
      await resolveAlert(ref.read(apiClientProvider), alert.id);
      ref.invalidate(activeAlertsProvider);
      await ref.read(activeAlertsProvider.future);
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    }
    if (mounted) setState(() => _resolving.remove(alert.id));
  }

  @override
  Widget build(BuildContext context) {
    final alerts = ref.watch(activeAlertsProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('警报'),
        actions: [
          IconButton(
            tooltip: '刷新',
            onPressed: () => ref.invalidate(activeAlertsProvider),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: AsyncView(
        value: alerts,
        onRetry: () => ref.invalidate(activeAlertsProvider),
        builder: (items) => items.isEmpty
            ? const EmptyState(
                icon: Icons.notifications_none_outlined,
                title: '没有待处理的警报',
                message: '同步、账号和维护出现异常时会在这里提醒。',
              )
            : RefreshIndicator(
                onRefresh: () async => ref.refresh(activeAlertsProvider.future),
                child: AdminListWidth(
                  top: 8,
                  builder: (padding) => ListView.builder(
                    padding: padding,
                    itemCount: items.length,
                    itemBuilder: (context, index) {
                      final alert = items[index];
                      return _AlertTile(
                        alert: alert,
                        busy: _resolving.contains(alert.id),
                        onResolve: () => _resolve(alert),
                      );
                    },
                  ),
                ),
              ),
      ),
    );
  }
}

/// 警报级别的图标和颜色：错误用暖红，警告用琥珀，提示用次级文字色
(IconData, Color) alertLevelStyle(String level) => switch (level) {
  'error' => (Icons.error_outline, CinemaColors.error),
  'warning' => (Icons.warning_amber_rounded, CinemaColors.amber),
  _ => (Icons.info_outline, CinemaColors.text2),
};

class _AlertTile extends StatelessWidget {
  const _AlertTile({
    required this.alert,
    required this.busy,
    required this.onResolve,
  });

  final AdminAlert alert;
  final bool busy;
  final VoidCallback onResolve;

  @override
  Widget build(BuildContext context) {
    final (icon, color) = alertLevelStyle(alert.level);
    final created = alert.createdAt;
    return FocusRing(
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(
          horizontal: CinemaSpacing.gutter,
        ),
        leading: Icon(icon, color: color),
        title: Text(alert.message),
        subtitle: created == null ? null : Text(formatAgo(created)),
        trailing: busy
            ? const BusyIcon()
            : TextButton(onPressed: onResolve, child: const Text('标记已处理')),
      ),
    );
  }
}
