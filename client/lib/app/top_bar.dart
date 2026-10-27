import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../core/servers.dart';
import '../features/auth/account_switcher.dart';
import '../features/auth/auth_controller.dart';
import 'account_menu.dart';
import 'activity_menu.dart';
import 'theme.dart';

/// 宽屏顶栏：左边是搜索框，右边依次是活动（仅管理员）、设置扳手和头像菜单。
class TopBar extends ConsumerStatefulWidget {
  const TopBar({super.key});

  @override
  ConsumerState<TopBar> createState() => _TopBarState();
}

class _TopBarState extends ConsumerState<TopBar> {
  static const _height = 64.0;

  final _search = TextEditingController();
  final _searchFocus = FocusNode();

  @override
  void dispose() {
    _search.dispose();
    _searchFocus.dispose();
    super.dispose();
  }

  /// 搜索结果页从地址里的 q 取词（同手机「搜索」栏）。同 Plex Web，在顶栏里直接打字，
  /// 回车后进搜索页。
  void _openSearch() {
    final query = _search.text.trim();
    _searchFocus.unfocus();
    context.go(
      Uri(
        path: '/search',
        queryParameters: query.isEmpty ? null : {'q': query},
      ).toString(),
    );
  }

  @override
  Widget build(BuildContext context) {
    final isAdmin = ref.watch(authControllerProvider).value?.isAdmin == true;
    return Container(
      height: _height,
      color: CinemaColors.surface1,
      padding: const EdgeInsets.symmetric(horizontal: CinemaSpacing.gutterWide),
      child: Row(
        children: [
          Expanded(
            child: Align(
              alignment: Alignment.centerLeft,
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 420),
                child: TextField(
                  controller: _search,
                  focusNode: _searchFocus,
                  textInputAction: TextInputAction.search,
                  onSubmitted: (_) => _openSearch(),
                  decoration: InputDecoration(
                    hintText: '搜索影片、剧集或人物',
                    prefixIcon: const Icon(Icons.search, size: 20),
                    prefixIconConstraints: const BoxConstraints(
                      minWidth: 40,
                      minHeight: 40,
                    ),
                    isDense: true,
                    contentPadding: const EdgeInsets.symmetric(
                      horizontal: 12,
                      vertical: 10,
                    ),
                    fillColor: CinemaColors.surface2,
                  ),
                ),
              ),
            ),
          ),
          const SizedBox(width: 8),
          if (isAdmin) const ActivityMenu(),
          IconButton(
            tooltip: '设置',
            onPressed: () => context.push('/settings'),
            icon: const Icon(Icons.build_outlined),
          ),
          const SizedBox(width: 4),
          const _AccountButton(),
        ],
      ),
    );
  }
}

/// 头像按钮：弹出和手机「我的」页同一份条目。
class _AccountButton extends ConsumerWidget {
  const _AccountButton();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final user = ref.watch(authControllerProvider).value;
    if (user == null) return const SizedBox.shrink();
    final serverName = currentServerName(ref);
    final items = accountMenuItems(
      canManageServers: ref.watch(manageServersProvider),
      serverName: serverName,
    );
    final text = Theme.of(context).textTheme;
    return PopupMenuButton<AccountAction>(
      tooltip: '账号',
      offset: const Offset(0, 8),
      position: PopupMenuPosition.under,
      onSelected: (action) => runAccountAction(context, ref, action),
      itemBuilder: (_) => [
        PopupMenuItem<AccountAction>(
          enabled: false,
          child: Row(
            children: [
              AccountAvatar(username: user.username, radius: 16),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      user.username,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: text.titleSmall,
                    ),
                    if (serverName != null && serverName.isNotEmpty)
                      Text(
                        serverName,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: text.bodySmall!.copyWith(
                          color: CinemaColors.text2,
                        ),
                      ),
                  ],
                ),
              ),
            ],
          ),
        ),
        const PopupMenuDivider(),
        for (final item in items)
          PopupMenuItem<AccountAction>(
            value: item.action,
            child: Row(
              children: [
                Icon(item.icon, size: 18),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(item.title),
                      if (item.subtitle case final subtitle?
                          when subtitle.isNotEmpty)
                        Text(
                          subtitle,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: text.bodySmall!.copyWith(
                            color: CinemaColors.text2,
                          ),
                        ),
                    ],
                  ),
                ),
              ],
            ),
          ),
      ],
      child: Padding(
        padding: const EdgeInsets.all(6),
        child: AccountAvatar(username: user.username, radius: 16),
      ),
    );
  }
}
