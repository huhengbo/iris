import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../app/theme.dart';
import 'ai_chat_controller.dart';

/// 底部输入框。生成中发送按钮换成「停止」；回车（软键盘的发送键）提交，
/// 输入法组词中的回车由输入法自己处理，不会误发。
class AiChatInput extends StatefulWidget {
  const AiChatInput({
    super.key,
    required this.busy,
    required this.enabled,
    required this.onSend,
    required this.onStop,
  });

  final bool busy;

  /// 正在读取会话或读取失败时不能发送
  final bool enabled;
  final ValueChanged<String> onSend;
  final VoidCallback onStop;

  @override
  State<AiChatInput> createState() => _AiChatInputState();
}

class _AiChatInputState extends State<AiChatInput> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _submit() {
    final text = _controller.text.trim();
    if (text.isEmpty || widget.busy || !widget.enabled) return;
    widget.onSend(text);
    _controller.clear();
  }

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Expanded(
              child: TextField(
                controller: _controller,
                minLines: 1,
                maxLines: 5,
                // 用文本键盘 + 发送键：回车提交；多行内容靠自动换行，不需要手动换行
                keyboardType: TextInputType.text,
                textInputAction: TextInputAction.send,
                // 自定义 onEditingComplete 以免提交后输入框失去焦点
                onEditingComplete: _submit,
                inputFormatters: [
                  LengthLimitingTextInputFormatter(aiMaxQuestionLength),
                ],
                decoration: const InputDecoration(
                  hintText: '问点什么，例如「最近有什么高分科幻片」',
                  counterText: '',
                  contentPadding: EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 12,
                  ),
                ),
              ),
            ),
            const SizedBox(width: 8),
            SizedBox(
              height: 48,
              child: widget.busy
                  ? OutlinedButton.icon(
                      onPressed: widget.onStop,
                      icon: const Icon(Icons.stop_rounded, size: 20),
                      label: const Text('停止'),
                    )
                  : ValueListenableBuilder(
                      valueListenable: _controller,
                      builder: (context, value, _) => IconButton.filled(
                        tooltip: '发送',
                        onPressed:
                            widget.enabled && value.text.trim().isNotEmpty
                            ? _submit
                            : null,
                        icon: const Icon(Icons.arrow_upward_rounded),
                        style: IconButton.styleFrom(
                          fixedSize: const Size(48, 48),
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(
                              CinemaRadius.control,
                            ),
                          ),
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
