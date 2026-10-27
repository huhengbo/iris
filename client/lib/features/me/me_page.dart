import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/account_menu.dart';
import '../../app/theme.dart';
import '../../core/servers.dart';
import '../../core/widgets/focus_ring.dart';
import '../auth/account_switcher.dart';
import '../auth/auth_controller.dart';

/// 「我的」只放入口：顶部是头像、用户名和当前服务器，下面是头像菜单的条目；
/// 具体设置项在二级页面里。
class MePage extends ConsumerWidget {
  const MePage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final user = ref.watch(authControllerProvider).value;
    if (user == null) return const SizedBox.shrink();
    final textTheme = Theme.of(context).textTheme;
    final serverName = currentServerName(ref);
    final items = accountMenuItems(
      canManageServers: ref.watch(manageServersProvider),
      serverName: serverName,
    );
    return Scaffold(
      appBar: AppBar(title: const Text('我的')),
      body: ListView(
        padding: const EdgeInsets.only(bottom: 24),
        children: [
          _Header(
            username: user.username,
            // 服务端信息还没回来时退回角色，不让第二行空着
            detail: serverName ?? roleLabel(user.role),
            textTheme: textTheme,
          ),
          for (final item in items) ...[
            // 「切换用户」和「退出登录」单独一组，和上面的入口分开
            if (item.action == AccountAction.switchUser)
              const Divider(height: 24),
            _EntryTile(
              item: item,
              onTap: () => runAccountAction(context, ref, item.action),
            ),
          ],
        ],
      ),
    );
  }
}

class _Header extends StatelessWidget {
  const _Header({
    required this.username,
    required this.detail,
    required this.textTheme,
  });

  final String username;
  final String detail;
  final TextTheme textTheme;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(
      CinemaSpacing.gutter,
      8,
      CinemaSpacing.gutter,
      20,
    ),
    child: Row(
      children: [
        AccountAvatar(username: username, radius: 28),
        const SizedBox(width: 16),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                username,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: textTheme.titleLarge,
              ),
              const SizedBox(height: 2),
              Text(
                detail,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: textTheme.bodySmall!.copyWith(color: CinemaColors.text2),
              ),
            ],
          ),
        ),
      ],
    ),
  );
}

/// 进入二级页面的一行：图标 + 标题 + 可选的一行说明 + 行尾箭头。
class _EntryTile extends StatelessWidget {
  const _EntryTile({required this.item, required this.onTap});

  final AccountMenuItem item;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final subtitle = item.subtitle;
    return FocusRing(
      child: ListTile(
        leading: Icon(item.icon),
        title: Text(item.title),
        subtitle: subtitle == null || subtitle.isEmpty ? null : Text(subtitle),
        trailing: const Icon(Icons.chevron_right),
        onTap: onTap,
      ),
    );
  }
}
