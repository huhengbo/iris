import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/providers.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/snack.dart';
import '../auth/account_switcher.dart' show AccountAvatar;
import '../auth/auth_controller.dart';
import '../auth/password_dialog.dart';
import 'admin_widgets.dart';
import 'libraries_api.dart';
import 'users_api.dart';
import '../../core/format.dart';

part 'users_sessions_dialog.dart';
part 'users_access_dialogs.dart';

class UsersPage extends ConsumerWidget {
  const UsersPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final users = ref.watch(adminUsersProvider);
    final me = ref.watch(authControllerProvider).value;
    return Scaffold(
      appBar: AppBar(
        title: const Text('用户管理'),
        actions: [
          // 遥控器没法下拉刷新
          IconButton(
            tooltip: '刷新',
            onPressed: () => ref.invalidate(adminUsersProvider),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      floatingActionButton: FocusRing(
        radius: CinemaRadius.fab,
        child: FloatingActionButton.extended(
          onPressed: () => _createUser(context, ref),
          icon: const Icon(Icons.person_add_alt),
          label: const Text('新建用户'),
        ),
      ),
      body: AsyncView(
        value: users,
        onRetry: () => ref.invalidate(adminUsersProvider),
        builder: (list) => RefreshIndicator(
          onRefresh: () => ref.refresh(adminUsersProvider.future),
          child: AdminListWidth(
            top: 8,
            bottom: 88,
            builder: (padding) => ListView.separated(
              padding: padding,
              itemCount: list.length,
              separatorBuilder: (_, _) => const Divider(height: 1),
              itemBuilder: (context, index) => _UserTile(
                entry: list[index],
                isSelf: list[index].user.id == me?.id,
              ),
            ),
          ),
        ),
      ),
    );
  }

  Future<void> _createUser(BuildContext context, WidgetRef ref) async {
    final created = await showDialog<bool>(
      context: context,
      builder: (_) => const _CreateUserDialog(),
    );
    if (created != true) return;
    ref.invalidate(adminUsersProvider);
    if (context.mounted) showSnack(context, '已创建用户');
  }
}

enum _UserAction {
  libraries,
  sessions,
  toggleSubtitleDownload,
  toggleRole,
  toggleDisabled,
  resetPassword,
  delete,
}

class _UserTile extends ConsumerWidget {
  const _UserTile({required this.entry, required this.isSelf});

  final AdminUser entry;
  final bool isSelf;

  AppUser get user => entry.user;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final lastSeen = entry.lastSeenAt;
    final tags = [
      user.isAdmin ? '管理员' : '普通用户',
      if (user.disabled) '已禁用',
      if (isSelf) '当前账号',
      if (!user.isAdmin && entry.allLibraries) '全部资料库',
      if (!user.isAdmin && !user.allowSubtitleDownload) '禁止下载字幕',
    ];
    return FocusRing(
      child: ListTile(
        contentPadding: const EdgeInsets.only(
          left: CinemaSpacing.gutter,
          right: 8,
        ),
        leading: AccountAvatar(username: user.username),
        title: Text(
          user.username,
          style: user.disabled
              ? const TextStyle(color: CinemaColors.text2)
              : null,
        ),
        subtitle: Text(
          '${tags.join(' · ')}\n'
          '${lastSeen == null ? '还没有登录过' : '最近活动 ${formatAgo(lastSeen)}'}',
        ),
        isThreeLine: true,
        trailing: PopupMenuButton<_UserAction>(
          tooltip: '管理 ${user.username}',
          onSelected: (action) => _run(context, ref, action),
          itemBuilder: (_) => [
            // 管理员总能看到全部资料库，不需要分配
            if (!user.isAdmin)
              const PopupMenuItem(
                value: _UserAction.libraries,
                child: Text('资料库权限'),
              ),
            if (!user.isAdmin)
              CheckedPopupMenuItem(
                key: const ValueKey('user-subtitle-download'),
                value: _UserAction.toggleSubtitleDownload,
                checked: user.allowSubtitleDownload,
                child: const Text('允许下载在线字幕'),
              ),
            const PopupMenuItem(
              value: _UserAction.sessions,
              child: Text('设备与会话'),
            ),
            const PopupMenuItem(
              value: _UserAction.resetPassword,
              child: Text('重置密码'),
            ),
            // 不能修改自己的角色、禁用或删除自己，避免把自己锁在外面
            if (!isSelf) ...[
              PopupMenuItem(
                value: _UserAction.toggleRole,
                child: Text(user.isAdmin ? '设为普通用户' : '设为管理员'),
              ),
              PopupMenuItem(
                value: _UserAction.toggleDisabled,
                child: Text(user.disabled ? '启用' : '禁用'),
              ),
              const PopupMenuItem(value: _UserAction.delete, child: Text('删除')),
            ],
          ],
        ),
      ),
    );
  }

  Future<void> _run(
    BuildContext context,
    WidgetRef ref,
    _UserAction action,
  ) async {
    final dio = ref.read(apiClientProvider);
    final name = user.username;
    try {
      final String done;
      switch (action) {
        case _UserAction.libraries:
          final changed = await showDialog<bool>(
            context: context,
            builder: (_) => _LibraryAccessDialog(user: user),
          );
          if (changed == true && context.mounted) {
            showSnack(context, '资料库权限已更新，该用户需要重新登录');
          }
          return;
        case _UserAction.sessions:
          await showDialog<void>(
            context: context,
            builder: (_) => _SessionsDialog(user: user),
          );
          return;
        case _UserAction.toggleSubtitleDownload:
          final allowed = !user.allowSubtitleDownload;
          await setUserSubtitleDownload(dio, user.id, allowed);
          done = allowed ? '已允许 $name 下载在线字幕' : '已禁止 $name 下载在线字幕';
        case _UserAction.resetPassword:
          final reset = await showPasswordDialog(
            context,
            title: '重置 $name 的密码',
            requireCurrent: false,
            onSubmit: (_, next) => resetUserPassword(dio, user.id, next),
          );
          if (reset && context.mounted) {
            showSnack(context, '密码已重置，该用户需要重新登录');
          }
          return;
        case _UserAction.toggleRole:
          final promote = !user.isAdmin;
          if (!await showConfirmDialog(
            context,
            title: promote ? '把 $name 设为管理员？' : '把 $name 设为普通用户？',
            body: promote
                ? '管理员能看到全部资料库，并能管理资料库、用户和服务器设置。'
                : '$name 将不能再进入管理后台，只能看到分配给他的资料库。',
            action: promote ? '设为管理员' : '设为普通用户',
          )) {
            return;
          }
          await setUserRole(dio, user.id, promote ? 'admin' : 'user');
          done = promote ? '已把 $name 设为管理员' : '已把 $name 设为普通用户';
        case _UserAction.toggleDisabled:
          if (!user.disabled &&
              !await showConfirmDialog(
                context,
                title: '禁用 $name？',
                body: '该用户会立即退出登录，无法再登录。',
                action: '禁用',
                destructive: true,
              )) {
            return;
          }
          await setUserDisabled(dio, user.id, !user.disabled);
          done = user.disabled ? '已启用 $name' : '已禁用 $name';
        case _UserAction.delete:
          if (!await showConfirmDialog(
            context,
            title: '删除 $name？',
            body: '该用户的账号和播放记录都会被删除，无法恢复。',
            action: '删除',
            destructive: true,
          )) {
            return;
          }
          await deleteUser(dio, user.id);
          done = '已删除 $name';
      }
      if (context.mounted) showSnack(context, done);
      ref.invalidate(adminUsersProvider);
    } catch (e) {
      if (context.mounted) showApiErrorSnack(context, e);
    }
  }
}
