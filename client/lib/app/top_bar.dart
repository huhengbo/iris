import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../core/servers.dart';
import '../features/ai/ai_entry_button.dart';
import '../features/auth/account_switcher.dart';
import '../features/auth/auth_controller.dart';
import '../features/search/search_page.dart';
import 'account_menu.dart';
import 'activity_menu.dart';
import 'theme.dart';

/// 宽屏顶栏：左边是搜索框，右边依次是活动（仅管理员）、设置扳手和头像菜单。
class TopBar extends ConsumerStatefulWidget {
  const TopBar({super.key, this.currentUri});

  final Uri? currentUri;

  @override
  ConsumerState<TopBar> createState() => TopBarState();
}

class TopBarState extends ConsumerState<TopBar> {
  static const _height = 64.0;
  static const _debounce = Duration(milliseconds: 300);

  final _search = TextEditingController();
  final _searchFocus = FocusNode();
  Timer? _timer;
  Uri? _lastUri;

  /// 全局快捷键（`/` 或 `Cmd/Ctrl + K`）聚焦顶栏搜索框。
  void focusSearch() {
    _searchFocus.requestFocus();
    if (_search.text.isNotEmpty) {
      _search.selection = TextSelection(
        baseOffset: 0,
        extentOffset: _search.text.length,
      );
    }
  }

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _syncFromRoute();
  }

  @override
  void didUpdateWidget(covariant TopBar oldWidget) {
    super.didUpdateWidget(oldWidget);
    _syncFromRoute();
  }

  @override
  void dispose() {
    _timer?.cancel();
    _search.dispose();
    _searchFocus.dispose();
    super.dispose();
  }

  Uri? get _currentUri =>
      widget.currentUri ?? GoRouter.maybeOf(context)?.state.uri;

  bool get _inSearch => _currentUri?.path == '/search';

  void _syncFromRoute() {
    final uri = _currentUri;
    if (uri == null || uri == _lastUri) return;
    _lastUri = uri;
    if (uri.path != '/search') return;
    final routeQ = uri.queryParameters['q'] ?? '';
    if (routeQ.trim() != _search.text.trim()) {
      _timer?.cancel();
      _search.value = TextEditingValue(
        text: routeQ,
        selection: TextSelection.collapsed(offset: routeQ.length),
      );
    }
  }

  void _onChanged(String text) {
    _timer?.cancel();
    if (!_inSearch) return;
    _timer = Timer(_debounce, () => _applyInSearch(text));
  }

  void _applyInSearch(String text) {
    _timer?.cancel();
    if (!mounted) return;
    final q = text.trim();
    final routeQ = (_currentUri?.queryParameters['q'] ?? '').trim();
    if (q == routeQ) return;
    if (q.isNotEmpty) {
      ref.read(searchHistoryProvider.notifier).add(q);
    }
    Router.neglect(
      context,
      () => context.go(
        Uri(
          path: '/search',
          queryParameters: q.isEmpty ? null : {'q': q},
        ).toString(),
      ),
    );
  }

  void _clear() {
    _timer?.cancel();
    _search.clear();
    if (_inSearch) {
      _applyInSearch('');
    }
    _searchFocus.requestFocus();
  }

  /// 搜索结果页从地址里的 q 取词（同手机「搜索」栏）。在搜索页外回车跳去搜索页，
  /// 在搜索页内回车立即应用搜索词。
  void _openSearch() {
    _timer?.cancel();
    final query = _search.text.trim();
    if (query.isNotEmpty) {
      ref.read(searchHistoryProvider.notifier).add(query);
    }
    _searchFocus.unfocus();
    final target = Uri(
      path: '/search',
      queryParameters: query.isEmpty ? null : {'q': query},
    ).toString();
    if (_inSearch) {
      Router.neglect(context, () => context.go(target));
    } else {
      context.go(target);
    }
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
                  maxLength: 100,
                  onChanged: _onChanged,
                  onSubmitted: (_) => _openSearch(),
                  decoration: InputDecoration(
                    hintText: '搜索影片、剧集或人物',
                    counterText: '',
                    prefixIcon: const Icon(Icons.search, size: 20),
                    prefixIconConstraints: const BoxConstraints(
                      minWidth: 40,
                      minHeight: 40,
                    ),
                    suffixIcon: ListenableBuilder(
                      listenable: _search,
                      builder: (_, _) => _search.text.isEmpty
                          ? const SizedBox.shrink()
                          : IconButton(
                              tooltip: '清除',
                              onPressed: _clear,
                              icon: const Icon(Icons.close, size: 18),
                            ),
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
          const AiEntryButton(),
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
