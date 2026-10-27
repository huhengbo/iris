import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/format.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/snack.dart';
import '../admin/admin_widgets.dart' show AdminListWidth;
import 'devices_api.dart';

/// 「账户 › 设备与会话」：登录过的 Iris 客户端和第三方客户端（Infuse、网易爆米花等）。
/// 撤销前先确认，撤销后令牌立即失效。
class DevicesPage extends ConsumerWidget {
  const DevicesPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final devices = ref.watch(myDevicesProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('设备与会话')),
      body: AsyncView(
        value: devices,
        onRetry: () => ref.invalidate(myDevicesProvider),
        builder: (items) => items.isEmpty
            ? const EmptyState(
                icon: Icons.devices_other,
                title: '没有登录中的设备',
                message: '用这个账号登录过的设备会出现在这里，可以随时撤销。',
              )
            : AdminListWidth(
                builder: (padding) => ListView.builder(
                  padding: padding,
                  itemCount: items.length,
                  itemBuilder: (context, index) => _DeviceTile(
                    session: items[index],
                    onRevoked: () => ref.invalidate(myDevicesProvider),
                  ),
                ),
              ),
      ),
    );
  }
}

class _DeviceTile extends ConsumerStatefulWidget {
  const _DeviceTile({required this.session, required this.onRevoked});

  final DeviceSession session;
  final VoidCallback onRevoked;

  @override
  ConsumerState<_DeviceTile> createState() => _DeviceTileState();
}

class _DeviceTileState extends ConsumerState<_DeviceTile> {
  bool _revoking = false;

  Future<void> _revoke() async {
    if (_revoking) return;
    final name = widget.session.name;
    final confirmed = await showConfirmDialog(
      context,
      title: '撤销「$name」的登录？',
      body: widget.session.thirdParty
          ? '这台第三方客户端会立即退出登录，需要重新添加服务器并登录。'
          : '这台设备会立即退出登录，需要重新输入密码。',
      action: '撤销',
      destructive: true,
    );
    if (!confirmed || !mounted) return;
    setState(() => _revoking = true);
    try {
      await ref.read(devicesApiProvider).revoke(widget.session.id);
      if (!mounted) return;
      showSnack(context, '已撤销「$name」的登录');
      widget.onRevoked();
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    } finally {
      if (mounted) setState(() => _revoking = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = widget.session;
    final seen = formatIsoAgo(session.lastSeenAt);
    final client = session.clientName;
    final platform = session.platform;
    final version = session.clientVersion;
    final detail = [
      if (session.thirdParty) '第三方客户端',
      if (client != null && client.isNotEmpty && client != session.deviceName)
        client,
      if (platform != null && platform.isNotEmpty) platform,
      if (version != null && version.isNotEmpty) version,
      if (seen.isNotEmpty) '最近活动 $seen',
    ].join(' · ');
    return FocusRing(
      child: ListTile(
        leading: Icon(
          session.thirdParty ? Icons.play_circle_outline : Icons.devices_other,
        ),
        title: Text(session.name, maxLines: 1, overflow: TextOverflow.ellipsis),
        subtitle: detail.isEmpty ? null : Text(detail),
        trailing: _revoking
            ? const Padding(
                padding: EdgeInsets.all(12),
                child: SizedBox.square(
                  dimension: 20,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
              )
            : IconButton(
                tooltip: '撤销登录',
                onPressed: _revoke,
                icon: const Icon(Icons.logout),
              ),
      ),
    );
  }
}
