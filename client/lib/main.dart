import 'dart:ui' show PointerDeviceKind;

import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_web_plugins/url_strategy.dart';
import 'package:go_router/go_router.dart';
import 'package:media_kit/media_kit.dart';

import 'app/launch_splash.dart';
import 'app/router.dart';
import 'app/theme.dart';
import 'app/tv.dart';
import 'core/servers.dart';
import 'core/trust_roots.dart';
import 'features/auth/auth_controller.dart';

Future<void> main() async {
  // Web 用路径式地址（/item/12），与服务端的 SPA 回退一致；其他平台无影响
  usePathUrlStrategy();
  // push 打开的页面（来源详情、插件详情等）也要进地址栏，刷新和复制链接才能回到原页面；
  // 依赖调用方传参的 /player、/cast 刷新后由路由的 redirect 回到首页
  GoRouter.optionURLReflectsImperativeAPIs = true;
  WidgetsFlutterBinding.ensureInitialized();
  addLegacyTrustRoots();
  // 内置播放器只在 App 里用（Web 交给外部播放器）
  if (!kIsWeb) MediaKit.ensureInitialized();
  final television = await detectTelevision();
  if (television) enterTelevisionMode();
  runApp(
    ProviderScope(
      overrides: [televisionProvider.overrideWithValue(television)],
      child: const IrisApp(),
    ),
  );
}

class IrisApp extends ConsumerWidget {
  const IrisApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final television = ref.watch(televisionProvider);
    return MaterialApp.router(
      title: '光圈',
      debugShowCheckedModeBanner: false,
      theme: buildTheme(),
      shortcuts: appShortcuts,
      // 桌面浏览器里鼠标也能拖动横向片架（默认只有触屏能拖）
      scrollBehavior: const MaterialScrollBehavior().copyWith(
        dragDevices: {
          PointerDeviceKind.touch,
          PointerDeviceKind.mouse,
          PointerDeviceKind.stylus,
          PointerDeviceKind.trackpad,
        },
      ),
      // 开机动画在 TV 的界面缩放外面，和系统启动屏按同样的屏幕尺寸对齐；
      // 遥控器提示在缩放里面（让开过扫描），等开机动画张开后才出现
      builder: (context, child) => LaunchGate(
        child: television
            ? televisionBuilder(
                context,
                Consumer(
                  builder: (context, ref, child) => TvRemoteHint(
                    enabled: sessionSettled(
                      ref.watch(authControllerProvider),
                      ref.watch(serverGateProvider),
                    ),
                    delay: LaunchSplash.duration,
                    child: child!,
                  ),
                  child: child,
                ),
              )
            : child!,
      ),
      routerConfig: ref.watch(routerProvider),
    );
  }
}

/// Web 默认把方向键用来滚动页面；这里统一改成移动焦点，
/// 让键盘和 Android TV 的遥控器方向键行为一致（见 DESIGN.md 的 TV 一节）。
final appShortcuts = <ShortcutActivator, Intent>{
  ...WidgetsApp.defaultShortcuts,
  const SingleActivator(LogicalKeyboardKey.arrowUp):
      const DirectionalFocusIntent(TraversalDirection.up),
  const SingleActivator(LogicalKeyboardKey.arrowDown):
      const DirectionalFocusIntent(TraversalDirection.down),
  const SingleActivator(LogicalKeyboardKey.arrowLeft):
      const DirectionalFocusIntent(TraversalDirection.left),
  const SingleActivator(LogicalKeyboardKey.arrowRight):
      const DirectionalFocusIntent(TraversalDirection.right),
};
