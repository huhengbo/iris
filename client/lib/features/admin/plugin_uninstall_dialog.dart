import 'package:flutter/material.dart';

import '../../app/theme.dart';

/// 弹出卸载确认对话框，返回 null 表示取消，返回 bool 表示是否同时删除数据
Future<bool?> showPluginUninstallConfirmDialog(
  BuildContext context, {
  required String pluginName,
}) async {
  var deleteData = false;
  return showDialog<bool>(
    context: context,
    builder: (context) => StatefulBuilder(
      builder: (context, setState) => AlertDialog(
        title: Text('卸载「$pluginName」？'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text('卸载后将停用并移除此插件文件。'),
            const SizedBox(height: 12),
            CheckboxListTile(
              contentPadding: EdgeInsets.zero,
              value: deleteData,
              onChanged: (val) => setState(() => deleteData = val ?? false),
              title: const Text('同时删除数据'),
              subtitle: const Text('包含此插件的所有持久化配置、状态与密钥'),
              controlAffinity: ListTileControlAffinity.leading,
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(null),
            child: const Text('取消'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: CinemaColors.error,
              foregroundColor: Colors.white,
            ),
            onPressed: () => Navigator.of(context).pop(deleteData),
            child: const Text('卸载'),
          ),
        ],
      ),
    ),
  );
}
