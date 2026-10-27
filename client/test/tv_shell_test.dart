import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/app/tv.dart';
import 'package:iris/features/tv/tv_shell.dart';
import 'package:iris/main.dart';

import 'support/fake_api.dart';
import 'support/nav_env.dart';

/// 电视侧栏：项的顺序、随焦点展开收起、方向键和返回键。
/// 首页内容用三个可聚焦的按钮代替，避免依赖真实首页数据。
void main() {
  late GoRouter router;

  Future<void> pumpShell(
    WidgetTester tester, {
    String location = '/',
    void Function(FakeAdminApi api)? setup,
  }) async {
    final env = navEnv(setup: setup);
    router = GoRouter(
      initialLocation: location,
      routes: [
        StatefulShellRoute.indexedStack(
          builder: (_, _, shell) => TvShell(navigationShell: shell),
          branches: [
            StatefulShellBranch(
              routes: [
                GoRoute(
                  path: '/',
                  builder: (_, _) => const _HomeContent(),
                  routes: [
                    GoRoute(
                      path: 'watchlist',
                      builder: (_, _) => const _Page('稍后观看页', autofocus: false),
                    ),
                  ],
                ),
              ],
            ),
            StatefulShellBranch(
              routes: [
                GoRoute(
                  path: '/libraries',
                  builder: (_, _) => const _Page('资料库列表页'),
                  routes: [
                    GoRoute(
                      path: ':id',
                      builder: (_, _) => const _Page('库内卡片'),
                    ),
                  ],
                ),
              ],
            ),
            StatefulShellBranch(
              routes: [
                GoRoute(path: '/search', builder: (_, _) => const _Page('搜索页')),
              ],
            ),
            StatefulShellBranch(
              routes: [
                GoRoute(path: '/me', builder: (_, _) => const _Page('我的页')),
              ],
            ),
          ],
        ),
        // 第 ⑤ 批起设置是一个独立地址，不再挂在「我的」下面
        GoRoute(path: '/settings', builder: (_, _) => const _Page('设置页')),
      ],
    );
    addTearDown(router.dispose);
    tester.view.physicalSize = const Size(1920, 1080);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(
      ProviderScope(
        overrides: env.overrides.cast(),
        child: MaterialApp.router(
          theme: buildTheme(),
          shortcuts: appShortcuts,
          builder: televisionBuilder,
          routerConfig: router,
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  /// 当前焦点是否落在显示这个文字的那一项上。
  bool focusedOn(WidgetTester tester, String label) {
    final focused = FocusManager.instance.primaryFocus?.context;
    if (focused == null) return false;
    return find
        .descendant(
          of: find.byElementPredicate((element) => element == focused),
          matching: find.text(label),
        )
        .evaluate()
        .isNotEmpty;
  }

  bool focusedInContent(WidgetTester tester) =>
      focusedOn(tester, '卡片一') ||
      focusedOn(tester, '卡片二') ||
      focusedOn(tester, '库内卡片') ||
      focusedOn(tester, '稍后观看页');

  double sidebarWidth(WidgetTester tester) =>
      tester.getSize(find.byKey(const ValueKey('tv-sidebar'))).width;

  Future<void> press(WidgetTester tester, LogicalKeyboardKey key) async {
    await tester.sendKeyEvent(key);
    await tester.pumpAndSettle();
  }

  testWidgets('sidebar lists search, home, watchlist, libraries, settings '
      'and switch user in order', (tester) async {
    await pumpShell(tester);
    // 过扫描安全区是 48，收起时侧栏净宽 80
    final safeLeft = TvLayout.overscan.left;
    expect(sidebarWidth(tester), safeLeft + tvSidebarCollapsedWidth);

    double top(String label) => tester.getTopLeft(find.text(label)).dy;
    expect(top('搜索'), lessThan(top('首页')));
    expect(top('首页'), lessThan(top('稍后观看')));
    expect(top('稍后观看'), lessThan(top('电影')));
    expect(top('电影'), lessThan(top('剧集')));
    expect(top('剧集'), lessThan(top('设置')));
    expect(top('设置'), lessThan(top('切换用户')));
  });

  testWidgets('sidebar expands while focused and collapses when focus leaves', (
    tester,
  ) async {
    await pumpShell(tester);
    final safeLeft = TvLayout.overscan.left;
    expect(focusedOn(tester, '卡片一'), isTrue);
    expect(sidebarWidth(tester), safeLeft + tvSidebarCollapsedWidth);
    final cardLeft = tester.getTopLeft(find.text('卡片一')).dx;

    // 内容最左边再按左：进入侧栏的当前项（首页），侧栏展开
    await press(tester, LogicalKeyboardKey.arrowLeft);
    expect(focusedOn(tester, '首页'), isTrue);
    expect(sidebarWidth(tester), safeLeft + tvSidebarExpandedWidth);
    // 展开的侧栏浮在内容上面，海报不跟着挪
    expect(tester.getTopLeft(find.text('卡片一')).dx, cardLeft);
    // 文字跟着展开出现
    final label = tester.widget<AnimatedOpacity>(
      find.ancestor(
        of: find.text('首页'),
        matching: find.byType(AnimatedOpacity),
      ),
    );
    expect(label.opacity, 1);

    // 右回到内容，侧栏收回
    await press(tester, LogicalKeyboardKey.arrowRight);
    expect(focusedInContent(tester), isTrue);
    expect(sidebarWidth(tester), safeLeft + tvSidebarCollapsedWidth);
  });

  testWidgets('arrows move inside the sidebar and into the page', (
    tester,
  ) async {
    await pumpShell(tester);
    await press(tester, LogicalKeyboardKey.arrowLeft);
    expect(focusedOn(tester, '首页'), isTrue);

    await press(tester, LogicalKeyboardKey.arrowUp);
    expect(focusedOn(tester, '搜索'), isTrue);
    await press(tester, LogicalKeyboardKey.arrowDown);
    expect(focusedOn(tester, '首页'), isTrue);
    await press(tester, LogicalKeyboardKey.arrowDown);
    expect(focusedOn(tester, '稍后观看'), isTrue);
    await press(tester, LogicalKeyboardKey.arrowDown);
    expect(focusedOn(tester, '电影'), isTrue);

    // 选中资料库，切到资料库栏；焦点进到新页面的默认位置
    await press(tester, LogicalKeyboardKey.select);
    expect(router.state.uri.path, '/libraries/1');
    expect(focusedOn(tester, '库内卡片'), isTrue);

    // 从内容往左回到侧栏时，焦点落在当前页对应的资料库项
    await press(tester, LogicalKeyboardKey.arrowLeft);
    expect(focusedOn(tester, '电影'), isTrue);
  });

  testWidgets('settings opens the personal settings page', (tester) async {
    await pumpShell(tester);
    await press(tester, LogicalKeyboardKey.arrowLeft);
    // 首页 → 稍后观看 → 电影 → 剧集 → 设置
    for (var i = 0; i < 4; i++) {
      await press(tester, LogicalKeyboardKey.arrowDown);
    }
    expect(focusedOn(tester, '设置'), isTrue);
    await press(tester, LogicalKeyboardKey.select);
    expect(router.state.uri.path, '/settings');
    expect(find.text('设置页'), findsOneWidget);
  });

  testWidgets('back on home returns focus to the sidebar first', (
    tester,
  ) async {
    await pumpShell(tester);
    expect(focusedOn(tester, '卡片一'), isTrue);

    await tester.binding.handlePopRoute();
    await tester.pumpAndSettle();

    expect(focusedOn(tester, '首页'), isTrue);
    expect(
      sidebarWidth(tester),
      TvLayout.overscan.left + tvSidebarExpandedWidth,
    );
  });
}

class _HomeContent extends StatelessWidget {
  const _HomeContent();

  @override
  Widget build(BuildContext context) => Scaffold(
    body: Center(
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          TextButton(
            autofocus: true,
            onPressed: () {},
            child: const Text('卡片一'),
          ),
          TextButton(onPressed: () {}, child: const Text('卡片二')),
        ],
      ),
    ),
  );
}

/// 一个简单的内容页：默认焦点放在按钮上，模拟真实页面的首焦点。
class _Page extends StatelessWidget {
  const _Page(this.label, {this.autofocus = true});

  final String label;
  final bool autofocus;

  @override
  Widget build(BuildContext context) => Scaffold(
    body: Center(
      child: TextButton(
        autofocus: autofocus,
        onPressed: () {},
        child: Text(label),
      ),
    ),
  );
}
