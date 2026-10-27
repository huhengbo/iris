import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:iris/app/shell.dart';
import 'package:iris/app/tv.dart';
import 'package:iris/app/wide_sidebar.dart';
import 'package:iris/main.dart';

import 'support/nav_env.dart';

void main() {
  late TextEditingController field;
  late GoRouter router;

  Future<void> pumpShell(WidgetTester tester) async {
    final env = navEnv();
    field = TextEditingController(text: 'abc');
    addTearDown(field.dispose);
    router = GoRouter(
      routes: [
        StatefulShellRoute.indexedStack(
          builder: (_, _, shell) => AppShell(navigationShell: shell),
          branches: [
            StatefulShellBranch(
              routes: [
                GoRoute(
                  path: '/',
                  builder: (_, _) => Scaffold(
                    body: Row(
                      children: [
                        TextButton(
                          autofocus: true,
                          onPressed: () {},
                          child: const Text('甲'),
                        ),
                        TextButton(onPressed: () {}, child: const Text('乙')),
                      ],
                    ),
                  ),
                  routes: [
                    GoRoute(
                      path: 'watchlist',
                      builder: (_, _) => Scaffold(
                        body: TextField(
                          key: const ValueKey('page-field'),
                          controller: field,
                        ),
                      ),
                    ),
                  ],
                ),
              ],
            ),
            StatefulShellBranch(
              routes: [
                GoRoute(
                  path: '/libraries',
                  builder: (_, _) => const Scaffold(body: Text('资料库列表')),
                ),
              ],
            ),
            StatefulShellBranch(
              routes: [
                GoRoute(
                  path: '/search',
                  builder: (_, _) => const Scaffold(body: Text('搜索页')),
                ),
              ],
            ),
            StatefulShellBranch(
              routes: [
                GoRoute(
                  path: '/me',
                  builder: (_, _) => const Scaffold(body: Text('我的页')),
                ),
              ],
            ),
          ],
        ),
      ],
    );
    addTearDown(router.dispose);
    tester.view.physicalSize = const Size(1920, 1080);
    tester.view.devicePixelRatio = 2;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(
      ProviderScope(
        overrides: env.overrides.cast(),
        child: MaterialApp.router(
          shortcuts: appShortcuts,
          builder: televisionBuilder,
          routerConfig: router,
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  bool inSidebar() =>
      FocusManager.instance.primaryFocus!.context!
          .findAncestorWidgetOfExactType<WideSidebar>() !=
      null;

  /// 当前焦点是否落在这个文字所在的那一项上：按钮和侧栏行都包在自己的 Focus 里，
  /// 文字本身最近的那个 Focus 可能不是接焦点的那个。
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

  Future<void> press(WidgetTester tester, LogicalKeyboardKey key) async {
    await tester.sendKeyEvent(key);
    await tester.pumpAndSettle();
  }

  /// 从首页底部导航进「稍后观看」这一栏，页面里有一个输入框。
  Future<void> openWatchlist(WidgetTester tester) async {
    await tester.tap(find.text('稍后观看'));
    await tester.pumpAndSettle();
    expect(router.state.uri.path, '/watchlist');
  }

  testWidgets('left at the edge of a page moves to the side navigation, '
      'and the arrows keep working inside it', (tester) async {
    await pumpShell(tester);
    expect(focusedOn(tester, '甲'), isTrue);

    // 页面里还能往左时照常移动
    await press(tester, LogicalKeyboardKey.arrowRight);
    expect(focusedOn(tester, '乙'), isTrue);
    await press(tester, LogicalKeyboardKey.arrowLeft);
    expect(focusedOn(tester, '甲'), isTrue);

    await press(tester, LogicalKeyboardKey.arrowLeft);
    expect(inSidebar(), isTrue);
    // 焦点落在当前这一项（首页），往下能走到下一项并回车切栏
    expect(focusedOn(tester, '首页'), isTrue);
    await press(tester, LogicalKeyboardKey.arrowDown);
    expect(focusedOn(tester, '稍后观看'), isTrue);
    await press(tester, LogicalKeyboardKey.select);
    expect(router.state.uri.path, '/watchlist');
  });

  testWidgets('switches sections from the side navigation, and left inside '
      'a text field moves the cursor', (tester) async {
    await pumpShell(tester);
    await openWatchlist(tester);

    await tester.tap(find.byKey(const ValueKey('page-field')));
    await tester.pumpAndSettle();
    field.selection = const TextSelection.collapsed(offset: 3);
    await press(tester, LogicalKeyboardKey.arrowLeft);
    expect(inSidebar(), isFalse);
    expect(field.selection.baseOffset, 2);
  });

  testWidgets('pages make room for the keyboard only once', (tester) async {
    await pumpShell(tester);
    await openWatchlist(tester);
    final before = tester.getSize(find.byType(Scaffold).last).height;
    await tester.showKeyboard(find.byKey(const ValueKey('page-field')));
    // 1080p 屏幕下半被输入法占去 260dp
    tester.view.viewInsets = const FakeViewPadding(bottom: 520);
    await tester.pumpAndSettle();

    // 框架已经把内容区缩到键盘上方，页面里的 Scaffold 不能再缩一次
    final page = tester.element(find.byKey(const ValueKey('page-field')));
    expect(MediaQuery.viewInsetsOf(page).bottom, 0);
    expect(tester.getSize(find.byType(Scaffold).last).height, before - 260);
  });
}
