import 'dart:async';
import 'dart:typed_data';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/format.dart';
import '../../core/providers.dart';
import '../../core/widgets/snack.dart';
import 'plugin_catalog_api.dart';
import 'plugin_task_dialog.dart';
import 'plugins_api.dart';

const _maxUploadBytes = 50 * 1024 * 1024; // 50 MiB

/// 选取本地 zip 插件包并执行上传与确认安装流程
Future<void> pickAndInstallLocalPlugin(
  BuildContext context,
  WidgetRef ref,
) async {
  const typeGroup = XTypeGroup(
    label: 'zip',
    extensions: ['zip'],
    mimeTypes: ['application/zip', 'application/x-zip-compressed'],
  );
  final file = await openFile(acceptedTypeGroups: [typeGroup]);
  if (file == null) return;

  final dio = ref.read(apiClientProvider);

  // 1. 读取文件并校验大小
  final Uint8List bytes;
  try {
    bytes = await file.readAsBytes();
  } catch (e) {
    if (context.mounted) {
      showSnack(context, '读取文件失败: $e');
    }
    return;
  }

  if (bytes.length > _maxUploadBytes) {
    if (context.mounted) {
      showSnack(context, '插件包大小超出 50 MiB 限制');
    }
    return;
  }

  // 2. 上传文件
  PluginUpload upload;
  try {
    upload = await uploadPluginZip(dio, bytes);
  } catch (error) {
    if (context.mounted) {
      showApiErrorSnack(context, error);
    }
    return;
  }

  if (!context.mounted) return;

  // 3. 弹窗预览并确认风险
  final confirmed = await showDialog<bool>(
    context: context,
    barrierDismissible: false,
    builder: (context) => _PluginUploadPreviewDialog(upload: upload),
  );

  if (confirmed != true) {
    // 用户取消，清理上传的临时文件
    unawaited(deleteUploadedPlugin(dio, upload.uploadId));
    return;
  }

  if (!context.mounted) return;

  // 4. 提交安装并轮询任务
  try {
    final taskId = await installUploadedPlugin(
      dio,
      upload.uploadId,
      acceptRisk: true,
    );
    if (!context.mounted) return;

    final actionLabel = upload.action == 'upgrade' ? '升级' : '安装';
    final ok = await showPluginTaskProgressDialog(
      context,
      ref: ref,
      taskId: taskId,
      title: '$actionLabel「${upload.manifest.name}」',
    );

    if (ok && context.mounted) {
      ref.invalidate(pluginsProvider);
      showSnack(context, '插件${upload.manifest.name}$actionLabel成功');
    }
  } catch (error) {
    if (context.mounted) {
      showApiErrorSnack(context, error);
    }
  }
}

class _PluginUploadPreviewDialog extends StatefulWidget {
  const _PluginUploadPreviewDialog({required this.upload});

  final PluginUpload upload;

  @override
  State<_PluginUploadPreviewDialog> createState() =>
      _PluginUploadPreviewDialogState();
}

class _PluginUploadPreviewDialogState
    extends State<_PluginUploadPreviewDialog> {
  bool _acceptRisk = false;

  @override
  Widget build(BuildContext context) {
    final upload = widget.upload;
    final manifest = upload.manifest;
    final text = Theme.of(context).textTheme;
    final isUpgrade = upload.action == 'upgrade';

    return AlertDialog(
      title: Text('本地安装预览：${manifest.name}'),
      content: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text('插件 ID：${manifest.id}', style: text.bodySmall),
            Text('目标版本：v${manifest.version}', style: text.bodySmall),
            if (isUpgrade && upload.installedVersion != null)
              Text('当前版本：v${upload.installedVersion}', style: text.bodySmall),
            Text('安装方式：${isUpgrade ? '升级' : '全新安装'}', style: text.bodySmall),
            Text('包大小：${formatSize(upload.size)}', style: text.bodySmall),
            if (manifest.description.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(
                manifest.description,
                style: text.bodyMedium?.copyWith(color: CinemaColors.text2),
              ),
            ],
            const SizedBox(height: 12),
            if (manifest.provides.isNotEmpty)
              Text(
                '提供能力：${manifest.provides.join('、')}',
                style: text.bodySmall,
              ),
            if (manifest.capabilities.isNotEmpty)
              Text(
                '所需权限：${manifest.capabilities.join('、')}',
                style: text.bodySmall,
              ),
            const SizedBox(height: 16),
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: CinemaColors.surface3,
                borderRadius: BorderRadius.circular(CinemaRadius.small),
              ),
              child: Row(
                children: [
                  const Icon(
                    Icons.warning_amber_rounded,
                    color: CinemaColors.amber,
                    size: 20,
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      '本地插件代码未经官方仓库校验，具有管理员权限并在后台运行，请确保来源受信任。',
                      style: text.bodySmall?.copyWith(
                        color: CinemaColors.text2,
                      ),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 12),
            CheckboxListTile(
              contentPadding: EdgeInsets.zero,
              value: _acceptRisk,
              onChanged: (val) => setState(() => _acceptRisk = val ?? false),
              title: const Text('我已知晓并承担运行第三方插件代码的风险'),
              controlAffinity: ListTileControlAffinity.leading,
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(false),
          child: const Text('取消'),
        ),
        FilledButton(
          onPressed: _acceptRisk ? () => Navigator.of(context).pop(true) : null,
          child: Text(isUpgrade ? '确认升级' : '确认安装'),
        ),
      ],
    );
  }
}
