import 'package:flutter/material.dart';

/// 单行输入对话框，返回去掉首尾空白后的文字；取消时返回空。
/// 按钮一直可用（电视遥控器聚焦不到禁用的按钮），空白提交时在输入框下方说明。
Future<String?> showTextPrompt(
  BuildContext context, {
  required String title,
  required String label,
  required String action,
  String initial = '',
}) => showDialog<String>(
  context: context,
  builder: (_) =>
      _TextPrompt(title: title, label: label, action: action, initial: initial),
);

class _TextPrompt extends StatefulWidget {
  const _TextPrompt({
    required this.title,
    required this.label,
    required this.action,
    required this.initial,
  });

  final String title;
  final String label;
  final String action;
  final String initial;

  @override
  State<_TextPrompt> createState() => _TextPromptState();
}

class _TextPromptState extends State<_TextPrompt> {
  // 由对话框自己释放：关闭动画期间输入框还在用
  late final _text = TextEditingController(text: widget.initial);
  bool _touched = false;

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  bool get _empty => _text.text.trim().isEmpty;

  void _submit() {
    if (_empty) {
      setState(() => _touched = true);
      return;
    }
    Navigator.pop(context, _text.text.trim());
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      // 输入法弹出时按钮不被挡住
      scrollable: true,
      title: Text(widget.title),
      content: TextField(
        controller: _text,
        autofocus: true,
        decoration: InputDecoration(
          labelText: widget.label,
          errorText: _touched && _empty ? '请填写${widget.label}' : null,
        ),
        onChanged: (_) {
          if (_touched) setState(() {});
        },
        onSubmitted: (_) => _submit(),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('取消'),
        ),
        FilledButton(onPressed: _submit, child: Text(widget.action)),
      ],
    );
  }
}
