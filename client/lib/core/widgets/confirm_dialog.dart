import 'package:flutter/material.dart';

/// 确认对话框：标题写成问句（「删除「X」？」），正文说明后果；左边文字按钮
/// 「取消」，右边琥珀主动作，动作用具体动词（「删除」「退出」），不写「确定」。
///
/// [destructive] 的操作（删除、撤销等）默认焦点在「取消」上，遥控器连按
/// 确定不会误删；其余默认焦点在主动作上。
Future<bool> showConfirmDialog(
  BuildContext context, {
  required String title,
  String? body,
  required String action,
  bool destructive = false,
}) async {
  final confirmed = await showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text(title),
      content: body == null ? null : Text(body),
      actions: [
        TextButton(
          autofocus: destructive,
          onPressed: () => Navigator.pop(context, false),
          child: const Text('取消'),
        ),
        FilledButton(
          autofocus: !destructive,
          onPressed: () => Navigator.pop(context, true),
          child: Text(action),
        ),
      ],
    ),
  );
  return confirmed ?? false;
}
