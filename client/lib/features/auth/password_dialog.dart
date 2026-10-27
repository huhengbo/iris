import 'package:flutter/material.dart';

import '../../core/api/api_error.dart';
import '../../core/widgets/form_error_text.dart';

/// 后端的密码最短长度，见 backend/app/auth/users.py。
const minPasswordLength = 8;

/// 设置密码的对话框。[requireCurrent] 为 true 时额外要求输入当前密码。
/// [onSubmit] 抛出的错误显示在对话框内，成功后关闭并返回 true。
Future<bool> showPasswordDialog(
  BuildContext context, {
  required String title,
  required bool requireCurrent,
  required Future<void> Function(String? current, String next) onSubmit,
}) async {
  final result = await showDialog<bool>(
    context: context,
    builder: (_) => _PasswordDialog(
      title: title,
      requireCurrent: requireCurrent,
      onSubmit: onSubmit,
    ),
  );
  return result ?? false;
}

class _PasswordDialog extends StatefulWidget {
  const _PasswordDialog({
    required this.title,
    required this.requireCurrent,
    required this.onSubmit,
  });

  final String title;
  final bool requireCurrent;
  final Future<void> Function(String? current, String next) onSubmit;

  @override
  State<_PasswordDialog> createState() => _PasswordDialogState();
}

class _PasswordDialogState extends State<_PasswordDialog> {
  final _formKey = GlobalKey<FormState>();
  final _current = TextEditingController();
  final _next = TextEditingController();
  final _confirm = TextEditingController();
  bool _submitting = false;
  String? _error;

  @override
  void dispose() {
    _current.dispose();
    _next.dispose();
    _confirm.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_submitting || !_formKey.currentState!.validate()) return;
    setState(() {
      _submitting = true;
      _error = null;
    });
    try {
      await widget.onSubmit(
        widget.requireCurrent ? _current.text : null,
        _next.text,
      );
      if (mounted) Navigator.of(context).pop(true);
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = ApiError.from(e).message;
          _submitting = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      // 输入法占去半个屏幕时（TV、横屏）标题和输入框一起滚动，光标所在处保持可见
      scrollable: true,
      title: Text(widget.title),
      content: Form(
        key: _formKey,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (widget.requireCurrent)
              TextFormField(
                controller: _current,
                autofocus: true,
                obscureText: true,
                autofillHints: const [AutofillHints.password],
                decoration: const InputDecoration(labelText: '当前密码'),
                validator: (v) => (v == null || v.isEmpty) ? '请输入当前密码' : null,
              ),
            if (widget.requireCurrent) const SizedBox(height: 12),
            TextFormField(
              controller: _next,
              autofocus: !widget.requireCurrent,
              obscureText: true,
              autofillHints: const [AutofillHints.newPassword],
              decoration: const InputDecoration(labelText: '新密码'),
              validator: (v) => (v == null || v.length < minPasswordLength)
                  ? '至少 $minPasswordLength 个字符'
                  : null,
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _confirm,
              obscureText: true,
              decoration: const InputDecoration(labelText: '确认新密码'),
              validator: (v) => v != _next.text ? '两次输入不一致' : null,
              onFieldSubmitted: (_) => _submit(),
            ),
            if (_error case final error?) ...[
              const SizedBox(height: 12),
              FormErrorText(error),
            ],
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: _submitting
              ? null
              : () => Navigator.of(context).pop(false),
          child: const Text('取消'),
        ),
        FilledButton(
          onPressed: _submitting ? null : _submit,
          child: _submitting
              ? const SizedBox.square(
                  dimension: 20,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Text('保存'),
        ),
      ],
    );
  }
}
