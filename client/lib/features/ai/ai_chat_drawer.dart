import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../app/motion.dart';
import '../../app/theme.dart';
import 'ai_chat_history.dart';
import 'ai_chat_view.dart';

/// 宽屏上的 AI 对话抽屉宽度。
const aiDrawerWidth = 420.0;

/// 从右侧滑出的 AI 对话抽屉（宽屏）：surface1、左侧 `CinemaShadows.lift`，
/// 淡入 + 横向位移走 `Motion.emphasized` / `Motion.medium`，减少动态效果时只淡入。
/// 内容和手机全屏页是同一个 [AiChatView]。点抽屉外、按 Esc 或关闭按钮都会收起；
/// 对话里跳到别的页面（点卡片、查看全部）时也自动收起。
/// [initialQuestion] 是从搜索页带来的问句，打开后自动发送。
Future<void> showAiChatDrawer(
  BuildContext context, {
  String? initialQuestion,
}) => showGeneralDialog<void>(
  context: context,
  barrierDismissible: true,
  barrierLabel: '关闭 AI 助手',
  barrierColor: CinemaColors.scrim,
  transitionDuration: Motion.medium,
  pageBuilder: (context, _, _) => _AiDrawer(initialQuestion: initialQuestion),
  transitionBuilder: (context, animation, _, child) {
    final curved = CurvedAnimation(
      parent: animation,
      curve: Motion.emphasized,
      reverseCurve: Motion.emphasized.flipped,
    );
    final faded = FadeTransition(opacity: curved, child: child);
    if (Motion.reduced(context)) return faded;
    return SlideTransition(
      position: Tween(
        begin: const Offset(1, 0),
        end: Offset.zero,
      ).animate(curved),
      child: faded,
    );
  },
);

class _AiDrawer extends StatefulWidget {
  const _AiDrawer({this.initialQuestion});

  /// 从搜索页带来的问句，打开后自动发送
  final String? initialQuestion;

  @override
  State<_AiDrawer> createState() => _AiDrawerState();
}

class _AiDrawerState extends State<_AiDrawer> {
  GoRouter? _router;
  String? _opened;
  ModalRoute<dynamic>? _route;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _route ??= ModalRoute.of(context);
    if (_router == null) {
      final router = GoRouter.maybeOf(context);
      if (router != null) {
        _router = router;
        _opened = router.routerDelegate.state.uri.toString();
        router.routerDelegate.addListener(_onLocation);
      }
    }
  }

  /// 对话里点了卡片或「查看全部」，页面已经换了：收起抽屉，不盖在新页面上。
  void _onLocation() {
    final now = _router?.routerDelegate.state.uri.toString();
    final route = _route;
    if (now == _opened || route == null || !route.isActive) return;
    route.navigator?.removeRoute(route);
  }

  @override
  void dispose() {
    _router?.routerDelegate.removeListener(_onLocation);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final width = MediaQuery.sizeOf(context).width;
    return Align(
      alignment: Alignment.centerRight,
      child: SizedBox(
        width: width < aiDrawerWidth ? width : aiDrawerWidth,
        child: DecoratedBox(
          decoration: const BoxDecoration(
            color: CinemaColors.surface1,
            boxShadow: CinemaShadows.lift,
          ),
          child: Scaffold(
            backgroundColor: CinemaColors.surface1,
            appBar: AppBar(
              backgroundColor: CinemaColors.surface1,
              automaticallyImplyLeading: false,
              title: const Text('AI 助手'),
              actions: [
                AiChatMenuButton(initialQuestion: widget.initialQuestion),
                IconButton(
                  tooltip: '关闭',
                  icon: const Icon(Icons.close),
                  onPressed: () => Navigator.of(context).pop(),
                ),
              ],
            ),
            body: AiChatView(initialQuestion: widget.initialQuestion),
          ),
        ),
      ),
    );
  }
}
