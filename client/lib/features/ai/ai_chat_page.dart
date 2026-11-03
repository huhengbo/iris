import 'package:flutter/material.dart';

import 'ai_chat_history.dart';
import 'ai_chat_view.dart';

/// AI 对话页（M58），手机上的全屏页。[initialQuestion] 是从搜索页带过来的问句：
/// 有就新建对话并发送，没有就接着最近一次的对话。
/// 宽屏上同样的内容放在右侧抽屉里，见 `ai_chat_drawer.dart`。
class AiChatPage extends StatelessWidget {
  const AiChatPage({super.key, this.initialQuestion});

  final String? initialQuestion;

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('AI 助手'),
      actions: [AiChatMenuButton(initialQuestion: initialQuestion)],
    ),
    body: AiChatView(initialQuestion: initialQuestion),
  );
}
