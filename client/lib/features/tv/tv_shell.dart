import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/account_menu.dart';
import '../../app/motion.dart';
import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../library/browse_api.dart';

/// 收起时只有图标，展开时图标加文字。宽度不含过扫描安全区，
/// 实际占位由 [TvShell] 再加上左边的系统边距（见 app/tv.dart）。
const tvSidebarCollapsedWidth = 80.0;
const tvSidebarExpandedWidth = 288.0;

/// 电视的主界面框架：Plex 大屏端的可折叠左侧栏。
/// 依次是搜索、首页、稍后观看和各资料库，底部是设置和切换用户。
/// 收起时只留一列图标；焦点一进侧栏就展开显示文字，离开后收回。
class TvShell extends ConsumerStatefulWidget {
  const TvShell({super.key, required this.navigationShell});

  final StatefulNavigationShell navigationShell;

  @override
  ConsumerState<TvShell> createState() => _TvShellState();
}

class _TvShellState extends ConsumerState<TvShell> {
  /// 只用来收集侧栏里的焦点和判断焦点在不在侧栏，自己不接焦点
  final _sidebar = FocusNode(
    debugLabel: 'tv-sidebar',
    canRequestFocus: false,
    skipTraversal: true,
  );

  /// 每一行自己的焦点节点：内容区最左边再按左键时，焦点要回到当前这一项
  final Map<String, FocusNode> _nodes = {};

  /// 地址里的路径：选中项、返回键和侧栏焦点都按它判断
  String _location = '/';
  bool _expanded = false;

  StatefulNavigationShell get navigationShell => widget.navigationShell;

  @override
  void initState() {
    super.initState();
    _sidebar.addListener(_syncExpanded);
  }

  @override
  void dispose() {
    _sidebar
      ..removeListener(_syncExpanded)
      ..dispose();
    for (final node in _nodes.values) {
      node.dispose();
    }
    super.dispose();
  }

  /// 焦点落在侧栏的任意一行时展开，交给内容区后收起。
  void _syncExpanded() {
    final expanded = _sidebar.hasFocus;
    if (expanded != _expanded) setState(() => _expanded = expanded);
  }

  FocusNode _node(String id) =>
      _nodes.putIfAbsent(id, () => FocusNode(debugLabel: id));

  /// 当前页面对应的侧栏项。没有对应项（如资料库列表页）时返回 null。
  String? _selectedKey(String path) {
    if (path == '/') return 'home';
    if (path == '/search') return 'search';
    if (path == '/watchlist') return 'watchlist';
    if (path.startsWith('/settings')) return 'settings';
    final library = _libraryIdIn(path);
    return library == null ? null : 'library:$library';
  }

  /// 从内容区往左回到侧栏时，焦点落在当前页对应项；没有对应项时落回首页。
  void _focusSelected() {
    final key = _selectedKey(_location);
    final node =
        (key == null ? null : _nodes[key]) ??
        _nodes['home'] ??
        _nodes.values.firstOrNull;
    node?.requestFocus();
  }

  /// 内容在各栏自己的导航器里，方向键走不出去。
  /// 往左已经到头（页面没处理这个键）时回到侧栏的当前项，遥控器和键盘都靠它切栏。
  /// 方向键可能直接把焦点交给侧栏里几何上最近的一行，这里统一改成当前页对应项。
  KeyEventResult _leaveContent(FocusNode _, KeyEvent event) {
    if (event is KeyUpEvent ||
        event.logicalKey != LogicalKeyboardKey.arrowLeft) {
      return KeyEventResult.ignored;
    }
    final focused = FocusManager.instance.primaryFocus;
    // 输入框里左键移动光标
    if (focused == null ||
        focused.context?.findAncestorWidgetOfExactType<EditableText>() !=
            null) {
      return KeyEventResult.ignored;
    }
    if (!focused.focusInDirection(TraversalDirection.left) ||
        _sidebar.hasFocus) {
      _focusSelected();
    }
    return KeyEventResult.handled;
  }

  /// 侧栏展开时浮在内容上面，几何上最近的上下邻居可能是被盖住的海报，
  /// 所以侧栏里的上下键按行的先后自己移动，不交给默认的方向遍历。
  KeyEventResult _moveInSidebar(FocusNode _, KeyEvent event) {
    if (event is KeyUpEvent) return KeyEventResult.ignored;
    final step = switch (event.logicalKey) {
      LogicalKeyboardKey.arrowDown => 1,
      LogicalKeyboardKey.arrowUp => -1,
      _ => 0,
    };
    if (step == 0) return KeyEventResult.ignored;
    final rows =
        _nodes.values
            .where((node) => node.context != null && node.canRequestFocus)
            .toList()
          ..sort((a, b) => a.rect.top.compareTo(b.rect.top));
    final current = rows.indexWhere((node) => node.hasPrimaryFocus);
    if (current < 0) return KeyEventResult.ignored;
    final next = current + step;
    if (next >= 0 && next < rows.length) {
      rows[next].requestFocus();
      Scrollable.ensureVisible(rows[next].context!);
    }
    return KeyEventResult.handled;
  }

  /// 返回键：在首页先回侧栏，再按一次才交给路由（退出或返回上一页）。
  /// 有对话框或别的页面盖在壳上面时不抢返回键，交给它们自己关闭。
  Future<bool> _handleBack() async {
    final current = ModalRoute.of(context)?.isCurrent ?? true;
    if (current && _location == '/' && !_sidebar.hasFocus) {
      _focusSelected();
      return true;
    }
    return false;
  }

  @override
  Widget build(BuildContext context) {
    _location = GoRouterState.of(context).uri.path;
    final selectedKey = _selectedKey(_location);
    final expanded = _expanded;
    final safe = MediaQuery.paddingOf(context);
    final libraries = ref.watch(librariesProvider);
    final destinations = [
      _TvDestination(
        id: 'search',
        label: '搜索',
        icon: Icons.search_rounded,
        selectedIcon: Icons.search_rounded,
        selected: selectedKey == 'search',
        onTap: () => context.go('/search'),
      ),
      _TvDestination(
        id: 'home',
        label: '首页',
        icon: Icons.home_outlined,
        selectedIcon: Icons.home_rounded,
        selected: selectedKey == 'home',
        onTap: () => context.go('/'),
      ),
      _TvDestination(
        id: 'watchlist',
        label: '稍后观看',
        icon: Icons.bookmark_border_rounded,
        selectedIcon: Icons.bookmark_rounded,
        selected: selectedKey == 'watchlist',
        onTap: () => context.go('/watchlist'),
      ),
    ];
    return BackButtonListener(
      onBackButtonPressed: _handleBack,
      child: Scaffold(
        body: Stack(
          children: [
            Padding(
              // 内容只让出收起时的窄栏，展开的侧栏浮在内容上面，海报不跟着挪位置
              padding: EdgeInsets.only(
                left: safe.left + tvSidebarCollapsedWidth,
              ),
              child: Focus(
                canRequestFocus: false,
                skipTraversal: true,
                onKeyEvent: _leaveContent,
                // 左边距（过扫描）已由侧栏让开，内容不再重复留
                child: Builder(
                  builder: (context) => MediaQuery.removePadding(
                    context: context,
                    removeLeft: true,
                    child: navigationShell,
                  ),
                ),
              ),
            ),
            Positioned(
              left: 0,
              top: 0,
              bottom: 0,
              child: Focus(
                focusNode: _sidebar,
                onKeyEvent: _moveInSidebar,
                child: AnimatedContainer(
                  key: const ValueKey('tv-sidebar'),
                  // 展开收起是焦点反馈，要跟手；减少动态效果时直接切换
                  duration: Motion.reduced(context)
                      ? Duration.zero
                      : Motion.short,
                  curve: Motion.emphasized,
                  width:
                      safe.left +
                      (expanded
                          ? tvSidebarExpandedWidth
                          : tvSidebarCollapsedWidth),
                  padding: EdgeInsets.fromLTRB(
                    safe.left,
                    safe.top,
                    0,
                    safe.bottom,
                  ),
                  decoration: BoxDecoration(
                    color: CinemaColors.surface1,
                    boxShadow: expanded ? CinemaShadows.lift : null,
                  ),
                  child: Column(
                    children: [
                      const SizedBox(height: 8),
                      for (final destination in destinations)
                        _SideItem(
                          destination: destination,
                          expanded: expanded,
                          focusNode: _node(destination.id),
                        ),
                      Expanded(
                        child: _LibraryList(
                          libraries: libraries,
                          selectedKey: selectedKey,
                          expanded: expanded,
                          node: _node,
                          onRetry: () => ref.invalidate(librariesProvider),
                        ),
                      ),
                      const _SideDivider(),
                      _SideItem(
                        destination: _TvDestination(
                          id: 'settings',
                          label: '设置',
                          icon: Icons.settings_outlined,
                          selectedIcon: Icons.settings_rounded,
                          selected: selectedKey == 'settings',
                          // 第 ⑤ 批把设置重组成一个入口；在那之前先到现有个人设置页。
                          // TV 不提供管理和插件配置，这一页只有「账户 / 本机」部分。
                          onTap: () => runAccountAction(
                            context,
                            ref,
                            AccountAction.settings,
                          ),
                        ),
                        expanded: expanded,
                        focusNode: _node('settings'),
                      ),
                      _SideItem(
                        destination: _TvDestination(
                          id: 'switchUser',
                          label: '切换用户',
                          icon: Icons.switch_account_outlined,
                          selectedIcon: Icons.switch_account_rounded,
                          selected: false,
                          onTap: () => runAccountAction(
                            context,
                            ref,
                            AccountAction.switchUser,
                          ),
                        ),
                        expanded: expanded,
                        focusNode: _node('switchUser'),
                      ),
                      const SizedBox(height: 8),
                    ],
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// 路径里的资料库 ID：海报墙在首页栏是 /library/:id，在资料库栏是 /libraries/:id。
int? _libraryIdIn(String path) {
  final match = RegExp(r'^/(?:library|libraries)/(\d+)').firstMatch(path);
  return match == null ? null : int.tryParse(match.group(1)!);
}

/// 侧栏里的一行。选中项是琥珀底 + 琥珀图标 + 加粗文字；焦点叠一层暖白提亮，
/// 和选中区分开（DESIGN.md「交互状态」「多端：Android TV」）。
class _TvDestination {
  const _TvDestination({
    required this.id,
    required this.label,
    required this.icon,
    required this.selectedIcon,
    required this.selected,
    required this.onTap,
  });

  final String id;
  final String label;
  final IconData icon;
  final IconData selectedIcon;
  final bool selected;
  final VoidCallback onTap;
}

/// 各资料库：列表来自现有资料库 provider，当前用户看不到的不出现。
/// 加载、失败和空都有对应的行，不占满整个侧栏。
class _LibraryList extends StatelessWidget {
  const _LibraryList({
    required this.libraries,
    required this.selectedKey,
    required this.expanded,
    required this.node,
    required this.onRetry,
  });

  final AsyncValue<List<LibrarySummary>> libraries;
  final String? selectedKey;
  final bool expanded;
  final FocusNode Function(String id) node;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final rows = switch (libraries) {
      // 刷新时保留上一次的列表，不闪回「正在加载」（AsyncLoading 也可能带着旧值）
      AsyncValue(hasValue: true, :final value?) => <Widget>[
        for (final library in value)
          _SideItem(
            destination: _TvDestination(
              id: 'library:${library.id}',
              label: library.name,
              icon: library.kind == MediaKind.series
                  ? Icons.tv_outlined
                  : Icons.movie_outlined,
              selectedIcon: library.kind == MediaKind.series
                  ? Icons.tv_rounded
                  : Icons.movie_rounded,
              selected: selectedKey == 'library:${library.id}',
              onTap: () => context.go('/libraries/${library.id}'),
            ),
            expanded: expanded,
            focusNode: node('library:${library.id}'),
          ),
        if (value.isEmpty && expanded) const _SideHint('没有可见的资料库'),
      ],
      AsyncError(:final error) => <Widget>[
        if (expanded) _SideHint(ApiError.from(error).message, error: true),
        _SideItem(
          destination: _TvDestination(
            id: 'retryLibraries',
            label: '重试',
            icon: Icons.refresh_rounded,
            selectedIcon: Icons.refresh_rounded,
            selected: false,
            onTap: onRetry,
          ),
          expanded: expanded,
          focusNode: node('retryLibraries'),
        ),
      ],
      _ => <Widget>[_SideLoading(expanded: expanded)],
    };
    return ListView(padding: EdgeInsets.zero, children: rows);
  }
}

class _SideItem extends StatefulWidget {
  const _SideItem({
    required this.destination,
    required this.expanded,
    required this.focusNode,
  });

  final _TvDestination destination;
  final bool expanded;
  final FocusNode focusNode;

  @override
  State<_SideItem> createState() => _SideItemState();
}

class _SideItemState extends State<_SideItem> {
  bool _focused = false;

  @override
  Widget build(BuildContext context) {
    final destination = widget.destination;
    final selected = destination.selected;
    final foreground = selected
        ? CinemaColors.amber
        : _focused
        ? CinemaColors.text0
        : CinemaColors.text1;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 3),
      child: FocusableActionDetector(
        focusNode: widget.focusNode,
        onShowFocusHighlight: (value) {
          if (value != _focused) setState(() => _focused = value);
        },
        actions: {
          ActivateIntent: CallbackAction<ActivateIntent>(
            onInvoke: (_) {
              destination.onTap();
              return null;
            },
          ),
        },
        child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTap: destination.onTap,
          child: AnimatedContainer(
            duration: Motion.focus,
            curve: Motion.emphasized,
            height: 56,
            decoration: BoxDecoration(
              color: _rowColor(selected: selected, focused: _focused),
              borderRadius: BorderRadius.circular(CinemaRadius.control),
            ),
            child: Row(
              children: [
                // 56 宽正好等于收起时的行宽：图标始终居中，展开后停在左边
                SizedBox(
                  width: 56,
                  child: Center(
                    child: Icon(
                      selected ? destination.selectedIcon : destination.icon,
                      size: 28,
                      color: foreground,
                    ),
                  ),
                ),
                Expanded(
                  child: ClipRect(
                    child: Padding(
                      padding: const EdgeInsets.only(left: 10),
                      child: AnimatedOpacity(
                        opacity: widget.expanded ? 1 : 0,
                        duration: Motion.reduced(context)
                            ? Duration.zero
                            : Motion.focus,
                        // 收起时宽度只剩图标那 56，文字先淡出再被裁掉，不跳动
                        child: Text(
                          destination.label,
                          maxLines: 1,
                          softWrap: false,
                          overflow: TextOverflow.ellipsis,
                          style: Theme.of(context).textTheme.titleMedium!
                              .copyWith(
                                color: foreground,
                                fontWeight: selected || _focused
                                    ? FontWeight.w700
                                    : FontWeight.w600,
                              ),
                        ),
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  /// 行的底色。选中项本身就是琥珀淡底，焦点用暖白提亮叠上去。
  Color _rowColor({required bool selected, required bool focused}) {
    final base = selected ? CinemaColors.amberSoft : Colors.transparent;
    if (!focused) return base;
    return Color.alphaBlend(
      CinemaColors.highlight,
      Color.alphaBlend(base, CinemaColors.surface1),
    );
  }
}

class _SideDivider extends StatelessWidget {
  const _SideDivider();

  @override
  Widget build(BuildContext context) => const Padding(
    padding: EdgeInsets.symmetric(horizontal: 16, vertical: 8),
    child: Divider(height: 1, color: CinemaColors.line),
  );
}

/// 资料库列表加载中：一个转圈，收起时也放得下。
class _SideLoading extends StatelessWidget {
  const _SideLoading({required this.expanded});

  final bool expanded;

  @override
  Widget build(BuildContext context) => SizedBox(
    height: 56,
    child: Row(
      children: [
        const SizedBox(
          width: 56,
          child: Center(
            child: SizedBox.square(
              dimension: 20,
              child: CircularProgressIndicator(strokeWidth: 2),
            ),
          ),
        ),
        if (expanded)
          Text(
            '正在加载资料库…',
            style: Theme.of(context).textTheme.bodySmall!
                .copyWith(color: CinemaColors.text2),
          ),
      ],
    ),
  );
}

/// 侧栏里的一行说明，如空资料库、加载失败的原因。不接焦点。
class _SideHint extends StatelessWidget {
  const _SideHint(this.message, {this.error = false});

  final String message;
  final bool error;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(16, 8, 12, 4),
    child: Text(
      message,
      maxLines: 2,
      overflow: TextOverflow.ellipsis,
      style: Theme.of(context).textTheme.bodySmall!
          .copyWith(color: error ? CinemaColors.error : CinemaColors.text2),
    ),
  );
}
