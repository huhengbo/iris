import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../core/providers.dart';
import '../core/servers.dart';
import '../core/widgets/confirm_dialog.dart';
import '../features/auth/account_switcher.dart';
import '../features/auth/auth_controller.dart';

/// 头像菜单里的一项。手机「我的」页和宽屏顶栏共用这一份顺序和行为（ADR 0026 ①）。
/// 管理后台和关于已经并进「设置」，这里不再单独入口。
enum AccountAction {
  settings,
  playlists,
  favorites,
  devices,
  servers,
  switchUser,
  logout,
}

class AccountMenuItem {
  const AccountMenuItem(this.action, this.icon, this.title, {this.subtitle});

  final AccountAction action;
  final IconData icon;
  final String title;

  /// 行尾的一行小字，如服务器名
  final String? subtitle;
}

/// 头像菜单的条目：设置、播放列表、我的收藏、我的设备、服务器（仅 App）、
/// 切换用户、退出登录。管理后台和关于在「设置」里，不再单列。
List<AccountMenuItem> accountMenuItems({
  required bool canManageServers,
  String? serverName,
}) => [
  const AccountMenuItem(AccountAction.settings, Icons.settings_outlined, '设置'),
  const AccountMenuItem(AccountAction.playlists, Icons.playlist_play, '播放列表'),
  const AccountMenuItem(AccountAction.favorites, Icons.favorite_border, '我的收藏'),
  const AccountMenuItem(AccountAction.devices, Icons.devices_other, '我的设备'),
  if (canManageServers)
    AccountMenuItem(
      AccountAction.servers,
      Icons.dns_outlined,
      '服务器',
      subtitle: serverName,
    ),
  const AccountMenuItem(
    AccountAction.switchUser,
    Icons.switch_account_outlined,
    '切换用户',
  ),
  const AccountMenuItem(AccountAction.logout, Icons.logout, '退出登录'),
];

/// 执行头像菜单里的一项：跳转目标和手机「我的」页原来的行为一致。
Future<void> runAccountAction(
  BuildContext context,
  WidgetRef ref,
  AccountAction action,
) async {
  switch (action) {
    case AccountAction.settings:
      // 设置入口对手机、宽屏和 TV 都是同一个地址：SettingsPage 按端决定
      // 两栏还是列表，TV 只显示账户和本机。
      context.push('/settings');
    case AccountAction.playlists:
      context.push('/me/playlists');
    case AccountAction.favorites:
      context.push('/me/favorites');
    case AccountAction.devices:
      context.push('/settings/account/devices');
    case AccountAction.servers:
      context.push('/servers');
    case AccountAction.switchUser:
      await _switchUser(context, ref);
    case AccountAction.logout:
      await _logout(context, ref);
  }
}

Future<void> _switchUser(BuildContext context, WidgetRef ref) async {
  final messenger = ScaffoldMessenger.of(context);
  final router = GoRouter.of(context);
  if (!await showAccountSwitcher(context)) return;
  final name = ref.read(authControllerProvider).value?.username;
  // 换了账号，回到首页从头看这个账号的片库
  router.go('/');
  messenger
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text('已切换到 $name')));
}

Future<void> _logout(BuildContext context, WidgetRef ref) async {
  final controller = ref.read(authControllerProvider.notifier);
  // 退出后要重新输入密码（TV 上用遥控器打字很麻烦），默认焦点放在「取消」
  final confirmed = await showConfirmDialog(
    context,
    title: '退出登录？',
    body: '退出后这台设备不再记住这个账号，下次要重新输入密码。',
    action: '退出',
    destructive: true,
  );
  if (confirmed) await controller.logout();
}

/// 当前服务器的显示名：优先服务端自述的名字；App 上服务端信息还没回来时
/// 用本机记住的那台。Web 连的就是页面所在的服务器，没有本机记录。
String? currentServerName(WidgetRef ref) {
  final info = ref.watch(serverInfoProvider).value?.name;
  if (info != null && info.isNotEmpty) return info;
  if (!ref.watch(manageServersProvider)) return null;
  return ref.watch(serverListProvider).value?.current?.name;
}
