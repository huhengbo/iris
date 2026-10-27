import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/account_menu.dart' show currentServerName;
import '../../app/theme.dart';
import '../../core/widgets/section_title.dart';
import '../admin/admin_widgets.dart' show AdminListWidth;
import '../auth/account_switcher.dart' show AccountAvatar, roleLabel;
import '../auth/auth_controller.dart';
import 'preferences_page.dart' show ChangePasswordTile, WebDavSection;

/// 「账户 › 个人资料与密码」：当前账号、角色和服务器，改密码，以及这个账号的
/// 外部访问（WebDAV）。WebDAV 用登录用户名和密码，所以放在账户组里。
class AccountProfilePage extends ConsumerWidget {
  const AccountProfilePage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final auth = ref.watch(authControllerProvider);
    final user = auth.value;
    final textTheme = Theme.of(context).textTheme;
    return Scaffold(
      appBar: AppBar(title: const Text('个人资料与密码')),
      body: AdminListWidth(
        bottom: 32,
        builder: (padding) => ListView(
          padding: padding,
          children: [
            if (user != null)
              _Header(
                username: user.username,
                role: user.role,
                serverName: currentServerName(ref),
              )
            else
              Padding(
                padding: const EdgeInsets.fromLTRB(
                  CinemaSpacing.gutter,
                  16,
                  CinemaSpacing.gutter,
                  8,
                ),
                child: Text(
                  auth.hasError ? '账号信息暂时无法读取' : '正在读取账号…',
                  style: textTheme.bodyMedium?.copyWith(
                    color: CinemaColors.text2,
                  ),
                ),
              ),
            const SectionTitle('密码'),
            const ChangePasswordTile(),
            const SectionTitle('外部访问'),
            Padding(
              padding: const EdgeInsets.fromLTRB(
                CinemaSpacing.gutter,
                0,
                CinemaSpacing.gutter,
                8,
              ),
              child: Text(
                'WebDAV 用这个账号的用户名和登录密码，在 Infuse 等播放器里直接浏览片库。',
                style: textTheme.bodySmall?.copyWith(color: CinemaColors.text2),
              ),
            ),
            const WebDavSection(),
          ],
        ),
      ),
    );
  }
}

/// 头像、用户名，第二行是角色和当前服务器。
class _Header extends StatelessWidget {
  const _Header({
    required this.username,
    required this.role,
    required this.serverName,
  });

  final String username;
  final String role;
  final String? serverName;

  @override
  Widget build(BuildContext context) {
    final detail = [
      roleLabel(role),
      if (serverName != null && serverName!.isNotEmpty) serverName!,
    ].join(' · ');
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        CinemaSpacing.gutter,
        16,
        CinemaSpacing.gutter,
        4,
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
                  style: Theme.of(context).textTheme.titleLarge,
                ),
                const SizedBox(height: 2),
                Text(
                  detail,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: Theme.of(context).textTheme.bodySmall!
                      .copyWith(color: CinemaColors.text2),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
