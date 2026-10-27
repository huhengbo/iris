import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/widgets/form_error_text.dart';
import '../../core/api/token_store.dart';
import '../../core/widgets/focus_ring.dart';
import 'auth_controller.dart';

/// 切换用户：列出当前账号和本机记住的账号，点一下直接换过去，不用再输密码。
/// 换成功返回 true；选了「添加账号」时当前账号已记住并退回登录页。
Future<bool> showAccountSwitcher(BuildContext context) async {
  final switched = await showDialog<bool>(
    context: context,
    builder: (context) => const _AccountSwitcher(),
  );
  return switched ?? false;
}

class _AccountSwitcher extends ConsumerStatefulWidget {
  const _AccountSwitcher();

  @override
  ConsumerState<_AccountSwitcher> createState() => _AccountSwitcherState();
}

class _AccountSwitcherState extends ConsumerState<_AccountSwitcher> {
  /// 正在切换的账号；切换期间其他行禁用，防止连点
  int? _switching;
  String? _error;

  AuthController get _controller => ref.read(authControllerProvider.notifier);

  Future<void> _switch(SavedAccount account) async {
    if (_switching != null) return;
    setState(() {
      _switching = account.id;
      _error = null;
    });
    try {
      await _controller.switchTo(account);
      if (mounted) Navigator.of(context).pop(true);
    } catch (e) {
      if (mounted) {
        setState(() {
          _switching = null;
          _error = ApiError.from(e).message;
        });
      }
    }
  }

  Future<void> _add() async {
    // 对话框关掉后 ref 就不能用了，先取出来
    final controller = _controller;
    Navigator.of(context).pop(false);
    await controller.addAccount();
  }

  @override
  Widget build(BuildContext context) {
    final current = ref.watch(authControllerProvider).value;
    final saved = ref.watch(savedAccountsProvider);
    final busy = _switching != null;
    return SimpleDialog(
      title: const Text('切换用户'),
      // 行和选择弹层一样带圆角和左右留白，选中行的底色不贴边
      contentPadding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
      children: [
        if (current != null)
          _AccountRow(
            username: current.username,
            role: current.role,
            selected: true,
            autofocus: true,
            enabled: !busy,
            onTap: () => Navigator.of(context).pop(false),
          ),
        for (final (i, account)
            in (saved.value ?? const <SavedAccount>[]).indexed)
          _AccountRow(
            username: account.username,
            role: account.role,
            autofocus: current == null && i == 0,
            enabled: !busy,
            trailing: _switching == account.id
                ? const Padding(
                    padding: EdgeInsets.all(12),
                    child: SizedBox.square(
                      dimension: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    ),
                  )
                : IconButton(
                    tooltip: '不再记住 ${account.username}',
                    onPressed: busy ? null : () => _controller.forget(account),
                    icon: const Icon(Icons.close),
                  ),
            onTap: () => _switch(account),
          ),
        if (_error case final error?)
          Padding(
            padding: const EdgeInsets.fromLTRB(8, 8, 8, 4),
            child: FormErrorText(error),
          ),
        // 登录页本身就是在添加账号，那里不再给这一行
        if (current != null)
          FocusRing(
            child: ListTile(
              enabled: !busy,
              contentPadding: const EdgeInsets.symmetric(horizontal: 8),
              shape: _rowShape,
              leading: const SizedBox.square(
                dimension: 40,
                child: Icon(Icons.person_add_alt_outlined),
              ),
              title: const Text('添加账号'),
              onTap: _add,
            ),
          ),
      ],
    );
  }
}

const _rowShape = RoundedRectangleBorder(
  borderRadius: BorderRadius.all(Radius.circular(CinemaRadius.card)),
);

/// 一个账号：选中（当前账号）和选择弹层的 PickerOption 一样是 surface3 底 +
/// 加粗标题 + 琥珀勾，聚焦是琥珀环。
class _AccountRow extends StatelessWidget {
  const _AccountRow({
    required this.username,
    required this.role,
    required this.enabled,
    required this.onTap,
    this.selected = false,
    this.autofocus = false,
    this.trailing,
  });

  final String username;
  final String role;
  final bool selected;
  final bool autofocus;
  final bool enabled;
  final Widget? trailing;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return FocusRing(
      child: ListTile(
        autofocus: autofocus,
        enabled: enabled,
        selected: selected,
        selectedColor: CinemaColors.text0,
        selectedTileColor: CinemaColors.surface3,
        contentPadding: const EdgeInsets.symmetric(horizontal: 8),
        shape: _rowShape,
        leading: AccountAvatar(username: username),
        title: Text(
          username,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(
            color: selected ? CinemaColors.text0 : CinemaColors.text1,
            fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
          ),
        ),
        subtitle: Text(roleLabel(role)),
        trailing: selected
            ? Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text('当前', style: Theme.of(context).textTheme.bodySmall),
                  const SizedBox(width: 8),
                  const Icon(Icons.check, color: CinemaColors.amber),
                ],
              )
            : trailing,
        onTap: onTap,
      ),
    );
  }
}

String roleLabel(String role) => role == 'admin' ? '管理员' : '普通用户';

/// 用户名首字的圆形头像。
class AccountAvatar extends StatelessWidget {
  const AccountAvatar({super.key, required this.username, this.radius = 20});

  final String username;
  final double radius;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final initial = username.isEmpty
        ? '?'
        : String.fromCharCode(username.runes.first).toUpperCase();
    return CircleAvatar(
      radius: radius,
      backgroundColor: CinemaColors.surface3,
      foregroundColor: CinemaColors.text0,
      // 首字随头像大小取字号阶梯：「我的」顶部的 56 头像用页面标题一级
      child: Text(
        initial,
        style: (radius >= 28 ? textTheme.titleLarge : textTheme.titleSmall)!
            .copyWith(color: CinemaColors.text0),
      ),
    );
  }
}
