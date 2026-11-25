import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:go_router/go_router.dart';

import 'top_bar.dart';
import 'wide_sidebar.dart';

/// 宽屏切换为侧边导航的断点（Material 3 的 expanded 宽度）。
const wideLayoutBreakpoint = 840.0;

class _Destination {
  const _Destination(this.label, this.icon, this.selectedIcon);

  final String label;
  final IconData icon;
  final IconData selectedIcon;
}

const _destinations = [
  _Destination('首页', Icons.home_outlined, Icons.home),
  _Destination('资料库', Icons.video_library_outlined, Icons.video_library),
  _Destination('搜索', Icons.search_outlined, Icons.search),
  _Destination('我的', Icons.person_outline, Icons.person),
];

/// 主界面框架：手机上是底部导航，宽屏上是左侧栏加顶栏。
class AppShell extends StatefulWidget {
  const AppShell({super.key, required this.navigationShell});

  final StatefulNavigationShell navigationShell;

  @override
  State<AppShell> createState() => _AppShellState();
}

class _AppShellState extends State<AppShell> {
  /// 只用来找侧边导航里的按钮，自己不接焦点
  final _rail = FocusNode(canRequestFocus: false, skipTraversal: true);

  /// 宽屏侧栏：内容区最左边再按左键时要拿它的焦点节点和当前项
  final _sidebar = GlobalKey<WideSidebarState>();

  /// 宽屏顶栏：全局快捷键 `/` 或 `Cmd/Ctrl + K` 时聚焦搜索框
  final _topBar = GlobalKey<TopBarState>();

  StatefulNavigationShell get navigationShell => widget.navigationShell;

  @override
  void initState() {
    super.initState();
    HardwareKeyboard.instance.addHandler(_onGlobalKey);
  }

  @override
  void dispose() {
    HardwareKeyboard.instance.removeHandler(_onGlobalKey);
    _rail.dispose();
    super.dispose();
  }

  bool _onGlobalKey(KeyEvent event) {
    if (!mounted || event is! KeyDownEvent) return false;
    if (MediaQuery.sizeOf(context).width < wideLayoutBreakpoint) return false;
    if (!(ModalRoute.of(context)?.isCurrent ?? true)) return false;
    final focused = FocusManager.instance.primaryFocus;
    if (focused != null &&
        (focused.context?.widget is EditableText ||
            focused.context?.findAncestorWidgetOfExactType<EditableText>() !=
                null)) {
      return false;
    }
    final hw = HardwareKeyboard.instance;
    final isSlash =
        event.logicalKey == LogicalKeyboardKey.slash &&
        !hw.isControlPressed &&
        !hw.isMetaPressed &&
        !hw.isAltPressed &&
        !hw.isShiftPressed;
    final isCmdK =
        event.logicalKey == LogicalKeyboardKey.keyK &&
        (hw.isMetaPressed || hw.isControlPressed) &&
        !hw.isAltPressed;
    if (!isSlash && !isCmdK) return false;
    _topBar.currentState?.focusSearch();
    return true;
  }

  void _select(int index) {
    navigationShell.goBranch(
      index,
      initialLocation: index == navigationShell.currentIndex,
    );
  }

  /// 内容在各栏自己的导航器里，方向键走不出去。
  /// 往左已经到头（页面没处理这个键）时回到侧边导航的当前项，遥控器和键盘都靠它切栏。
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
    if (!focused.focusInDirection(TraversalDirection.left)) {
      final sidebar = _sidebar.currentState;
      if (sidebar != null) {
        sidebar.focusSelected();
      } else {
        final buttons = _rail.traversalDescendants.toList();
        buttons.firstOrNull?.requestFocus();
      }
    }
    return KeyEventResult.handled;
  }

  @override
  Widget build(BuildContext context) {
    final media = MediaQuery.of(context);
    final wide = media.size.width >= wideLayoutBreakpoint;
    if (wide) {
      return Scaffold(
        body: Row(
          children: [
            Focus(
              focusNode: _rail,
              child: WideSidebar(
                key: _sidebar,
                // 高亮当前资料库要跟着地址走
                currentPath: GoRouterState.of(context).uri.path,
              ),
            ),
            const VerticalDivider(width: 1),
            Expanded(
              child: Column(
                children: [
                  // 顶栏吃掉上边的刘海，下面的页面不再重复留
                  SafeArea(
                    bottom: false,
                    left: false,
                    right: false,
                    child: TopBar(
                      key: _topBar,
                      currentUri: GoRouterState.of(context).uri,
                    ),
                  ),
                  // 左边距（刘海、TV 过扫描）已由侧栏让开，上边距由顶栏让开
                  Expanded(
                    child: Focus(
                      canRequestFocus: false,
                      skipTraversal: true,
                      onKeyEvent: _leaveContent,
                      // 要用 Scaffold 里面的 context：外面那份还带着键盘高度，
                      // 页面会再让一次键盘，弹出输入法时内容被挤没
                      child: Builder(
                        builder: (context) => MediaQuery.removePadding(
                          context: context,
                          removeLeft: true,
                          removeTop: true,
                          child: navigationShell,
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      );
    }
    return Scaffold(
      body: navigationShell,
      bottomNavigationBar: NavigationBar(
        selectedIndex: navigationShell.currentIndex,
        onDestinationSelected: _select,
        destinations: [
          for (final d in _destinations)
            NavigationDestination(
              icon: Icon(d.icon),
              selectedIcon: Icon(d.selectedIcon),
              label: d.label,
            ),
        ],
      ),
    );
  }
}
