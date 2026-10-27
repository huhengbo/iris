import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/providers.dart';
import '../../core/format.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/snack.dart';
import 'admin_widgets.dart';
import 'backup_api.dart';

class BackupPage extends ConsumerStatefulWidget {
  const BackupPage({super.key});

  @override
  ConsumerState<BackupPage> createState() => _BackupPageState();
}

class _BackupPageState extends ConsumerState<BackupPage> {
  bool _creating = false;
  bool _exporting = false;

  /// 正在恢复的备份；恢复期间其他备份的「恢复」也不可点
  String? _restoring;

  void _showError(Object error) {
    if (mounted) showApiErrorSnack(context, error);
  }

  Future<void> _create() async {
    if (_creating) return;
    setState(() => _creating = true);
    try {
      final backup = await createBackup(ref.read(apiClientProvider));
      ref.invalidate(backupsProvider);
      if (mounted) showSnack(context, '已创建备份 ${backup.name}');
    } catch (error) {
      _showError(error);
    }
    if (mounted) setState(() => _creating = false);
  }

  Future<void> _export() async {
    if (_exporting) return;
    setState(() => _exporting = true);
    Map<String, dynamic>? config;
    try {
      config = await exportConfig(ref.read(apiClientProvider));
    } catch (error) {
      _showError(error);
    }
    if (!mounted) return;
    setState(() => _exporting = false);
    if (config != null) {
      await showDialog<void>(
        context: context,
        builder: (context) => _ConfigDialog(config: config!),
      );
    }
  }

  Future<void> _restore(BackupRecord backup) async {
    // 恢复会覆盖当前数据库，按危险操作处理：默认焦点落在「取消」
    final confirmed = await showConfirmDialog(
      context,
      title: '恢复数据库？',
      body:
          '将恢复 ${backup.name}（${formatSize(backup.size)}）。'
          '当前数据库会先自动备份，恢复后需要重新加载页面。',
      action: '恢复',
      destructive: true,
    );
    if (!confirmed || !mounted) return;
    setState(() => _restoring = backup.name);
    try {
      final report = await restoreBackup(
        ref.read(apiClientProvider),
        backup.name,
      );
      ref.invalidate(backupsProvider);
      if (mounted) {
        showSnack(context, '已恢复，恢复前的数据库保存在 ${report.safetyBackup.name}');
      }
    } catch (error) {
      _showError(error);
    }
    if (mounted) setState(() => _restoring = null);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('备份与恢复'),
        actions: [
          IconButton(
            tooltip: '导出安全配置',
            onPressed: _exporting ? null : _export,
            icon: _exporting
                ? const SizedBox.square(
                    dimension: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.file_download_outlined),
          ),
          IconButton(
            tooltip: '立即备份',
            onPressed: _creating ? null : _create,
            icon: _creating
                ? const SizedBox.square(
                    dimension: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.backup_outlined),
          ),
        ],
      ),
      body: AsyncView(
        value: ref.watch(backupsProvider),
        onRetry: () => ref.invalidate(backupsProvider),
        builder: (backups) => backups.isEmpty
            ? const EmptyState(
                icon: Icons.backup_outlined,
                title: '还没有备份',
                message: '备份包含数据库和插件状态，图片缓存可以重新生成。',
              )
            : AdminListWidth(
                top: 8,
                builder: (padding) => ListView.builder(
                  padding: padding,
                  itemCount: backups.length,
                  itemBuilder: (context, index) {
                    final backup = backups[index];
                    return _BackupTile(
                      backup: backup,
                      restoring: _restoring == backup.name,
                      onRestore: _restoring == null
                          ? () => _restore(backup)
                          : null,
                    );
                  },
                ),
              ),
      ),
    );
  }
}

class _BackupTile extends StatelessWidget {
  const _BackupTile({
    required this.backup,
    required this.restoring,
    required this.onRestore,
  });

  final BackupRecord backup;
  final bool restoring;
  final VoidCallback? onRestore;

  @override
  Widget build(BuildContext context) {
    return FocusRing(
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(
          horizontal: CinemaSpacing.gutter,
        ),
        leading: const Icon(Icons.storage_outlined, color: CinemaColors.text2),
        title: Text(backup.name),
        subtitle: Text(
          '${formatDateTime(backup.createdAt)} · ${formatSize(backup.size)}',
        ),
        trailing: restoring
            ? const SizedBox.square(
                dimension: 20,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            : TextButton(onPressed: onRestore, child: const Text('恢复')),
      ),
    );
  }
}

/// 导出的配置直接显示出来，可以复制保存。密钥和凭据由服务端排除。
class _ConfigDialog extends StatelessWidget {
  const _ConfigDialog({required this.config});

  final Map<String, dynamic> config;

  @override
  Widget build(BuildContext context) {
    final text = const JsonEncoder.withIndent('  ').convert(config);
    final theme = Theme.of(context).textTheme;
    return AlertDialog(
      title: const Text('安全配置'),
      content: SizedBox(
        width: 480,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              '不含凭据、插件密钥和密码，可以放心保存。',
              style: theme.bodyMedium?.copyWith(color: CinemaColors.text2),
            ),
            const SizedBox(height: 12),
            Flexible(
              child: Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: CinemaColors.surface3,
                  borderRadius: BorderRadius.circular(CinemaRadius.card),
                ),
                child: SingleChildScrollView(
                  child: SelectableText(
                    text,
                    style: theme.bodySmall?.copyWith(
                      fontFamily: 'monospace',
                      color: CinemaColors.text1,
                    ),
                  ),
                ),
              ),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('关闭'),
        ),
        FilledButton.tonal(
          autofocus: true,
          // 复制要在点按里直接完成，Safari 才允许写剪贴板
          onPressed: () async {
            await Clipboard.setData(ClipboardData(text: text));
            if (context.mounted) showSnack(context, '已复制配置');
          },
          child: const Text('复制'),
        ),
      ],
    );
  }
}
