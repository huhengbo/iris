import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/motion.dart';
import '../../app/theme.dart';
import '../../core/widgets/async_view.dart';
import 'ai_chat_controller.dart';
import 'ai_chat_input.dart';
import 'ai_chat_widgets.dart';

/// 对话内容（消息列表 + 输入框），不含 Scaffold 或抽屉外壳，
/// 全屏页和宽屏抽屉共用。[initialQuestion] 见 [aiChatControllerProvider]。
class AiChatView extends ConsumerStatefulWidget {
  const AiChatView({super.key, this.initialQuestion});

  final String? initialQuestion;

  @override
  ConsumerState<AiChatView> createState() => _AiChatViewState();
}

class _AiChatViewState extends ConsumerState<AiChatView> {
  final _scroll = ScrollController();

  /// 跟随最新内容：用户上滑就停止，滑回底部或点「回到最新」恢复
  bool _pinned = true;
  bool _jumpQueued = false;
  bool _jumping = false;

  static const _bottomSlack = 48.0;

  AiChatController get _controller =>
      ref.read(aiChatControllerProvider(widget.initialQuestion).notifier);

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  void _toEnd() {
    if (!_scroll.hasClients) return;
    final end = _scroll.position.maxScrollExtent;
    if ((_scroll.position.pixels - end).abs() < 0.5) return;
    _jumping = true;
    _scroll.jumpTo(end);
    _jumping = false;
  }

  void _queueJump() {
    if (_jumpQueued) return;
    _jumpQueued = true;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _jumpQueued = false;
      if (mounted && _pinned) _toEnd();
    });
  }

  bool _onNotification(Notification n) {
    if (n is ScrollMetricsNotification) {
      // 内容变高（流式输出、卡片、图片）时，贴着底就跟着走
      if (n.depth == 0 && _pinned) _queueJump();
    } else if (n is ScrollUpdateNotification && n.depth == 0 && !_jumping) {
      final atBottom = n.metrics.extentAfter < _bottomSlack;
      if (atBottom != _pinned) setState(() => _pinned = atBottom);
    }
    return false;
  }

  Future<void> _backToLatest() async {
    setState(() => _pinned = true);
    if (!_scroll.hasClients) return;
    if (Motion.reduced(context)) {
      _toEnd();
    } else {
      await _scroll.animateTo(
        _scroll.position.maxScrollExtent,
        duration: Motion.medium,
        curve: Motion.emphasized,
      );
    }
  }

  void _send(String text) {
    setState(() => _pinned = true);
    _queueJump();
    _controller.send(text);
  }

  @override
  Widget build(BuildContext context) {
    final provider = aiChatControllerProvider(widget.initialQuestion);
    final state = ref.watch(provider);
    // 内容变化后，贴底时跟到最新
    ref.listen(provider, (_, _) {
      if (_pinned) _queueJump();
    });

    final Widget body;
    if (state.loadError != null) {
      final error = state.loadError!;
      body = ErrorRetry(
        message: error.message,
        kind: error.kind,
        onRetry: _controller.reload,
      );
    } else if (state.loading) {
      body = const Center(child: CircularProgressIndicator());
    } else if (state.isEmpty) {
      body = AiEmptyChat(onPick: _send);
    } else {
      body = _messages(state);
    }

    return Column(
      children: [
        Expanded(
          child: Stack(
            children: [
              Positioned.fill(child: body),
              Positioned(
                right: 16,
                bottom: 12,
                child: _BackToLatest(
                  visible: !_pinned && !state.isEmpty,
                  onPressed: _backToLatest,
                ),
              ),
            ],
          ),
        ),
        AiChatInput(
          busy: state.busy,
          enabled: !state.loading && state.loadError == null,
          onSend: _send,
          onStop: _controller.stop,
        ),
      ],
    );
  }

  Widget _messages(AiChatState state) {
    final messages = state.messages;
    final lastAssistant = messages.lastIndexWhere((m) => !m.isUser);
    return NotificationListener<Notification>(
      onNotification: _onNotification,
      child: ListView.separated(
        controller: _scroll,
        padding: const EdgeInsets.symmetric(vertical: 16),
        itemCount: messages.length,
        separatorBuilder: (_, _) => const SizedBox(height: 20),
        itemBuilder: (context, i) {
          final message = messages[i];
          return AiMessageView(
            key: ValueKey(message.key),
            message: message,
            isLast: i == lastAssistant && i == messages.length - 1,
            busy: state.busy,
            onSuggestion: _send,
            onRetry: _controller.retry,
          );
        },
      ),
    );
  }
}

/// 右下角「回到最新」小圆钮，只淡入淡出。
class _BackToLatest extends StatelessWidget {
  const _BackToLatest({required this.visible, required this.onPressed});

  final bool visible;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) => AnimatedSwitcher(
    duration: Motion.short,
    child: visible
        ? Material(
            key: const ValueKey('visible'),
            color: CinemaColors.surface3,
            shape: const CircleBorder(
              side: BorderSide(color: CinemaColors.line),
            ),
            child: IconButton(
              tooltip: '回到最新',
              onPressed: onPressed,
              icon: const Icon(Icons.arrow_downward_rounded, size: 20),
            ),
          )
        : const SizedBox.shrink(key: ValueKey('hidden')),
  );
}
