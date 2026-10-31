import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/format.dart';
import '../../core/providers.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/snack.dart';
import 'admin_widgets.dart';
import 'plugin_catalog_api.dart';

/// 仓库管理页：列表展示、启停、添加（带风险确认）、删除（官方不可删）与手动检查更新。
class PluginRepositoriesPage extends ConsumerStatefulWidget {
  const PluginRepositoriesPage({super.key});

  @override
  ConsumerState<PluginRepositoriesPage> createState() =>
      _PluginRepositoriesPageState();
}

class _PluginRepositoriesPageState
    extends ConsumerState<PluginRepositoriesPage> {
  final _busy = <int>{};
  bool _refreshing = false;

  Future<void> _refresh() async {
    if (_refreshing) return;
    setState(() => _refreshing = true);
    try {
      await refreshPluginRepositories(ref.read(apiClientProvider));
      ref
        ..invalidate(pluginRepositoriesProvider)
        ..invalidate(pluginCatalogProvider);
      if (mounted) {
        showSnack(context, '已完成所有仓库检查');
      }
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    } finally {
      if (mounted) setState(() => _refreshing = false);
    }
  }

  Future<void> _setEnabled(PluginRepository repo, bool enabled) async {
    setState(() => _busy.add(repo.id));
    try {
      await setPluginRepositoryEnabled(
        ref.read(apiClientProvider),
        repo.id,
        enabled,
      );
      ref
        ..invalidate(pluginRepositoriesProvider)
        ..invalidate(pluginCatalogProvider);
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    } finally {
      if (mounted) setState(() => _busy.remove(repo.id));
    }
  }

  Future<void> _delete(PluginRepository repo) async {
    if (repo.official) {
      showSnack(context, '官方仓库不可删除');
      return;
    }
    final confirmed = await showConfirmDialog(
      context,
      title: '删除仓库「${repo.name.isNotEmpty ? repo.name : repo.url}」？',
      body: '删除后将无法从该仓库安装插件或接收更新。',
      action: '删除',
      destructive: true,
    );
    if (!confirmed || !mounted) return;

    setState(() => _busy.add(repo.id));
    try {
      await deletePluginRepository(ref.read(apiClientProvider), repo.id);
      ref
        ..invalidate(pluginRepositoriesProvider)
        ..invalidate(pluginCatalogProvider);
      if (mounted) showSnack(context, '仓库已删除');
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    } finally {
      if (mounted) setState(() => _busy.remove(repo.id));
    }
  }

  Future<void> _add() async {
    final added = await showDialog<bool>(
      context: context,
      builder: (context) => const _AddRepositoryDialog(),
    );
    if (added == true && mounted) {
      ref
        ..invalidate(pluginRepositoriesProvider)
        ..invalidate(pluginCatalogProvider);
      showSnack(context, '仓库添加成功');
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('插件仓库'),
        actions: [
          TextButton.icon(
            onPressed: _refreshing ? null : _refresh,
            icon: _refreshing
                ? const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.refresh),
            label: const Text('检查更新'),
          ),
          IconButton(
            tooltip: '添加仓库',
            onPressed: _add,
            icon: const Icon(Icons.add),
          ),
        ],
      ),
      body: AsyncView(
        value: ref.watch(pluginRepositoriesProvider),
        onRetry: () => ref.invalidate(pluginRepositoriesProvider),
        builder: (repos) {
          if (repos.isEmpty) {
            return const EmptyState(
              icon: Icons.source_outlined,
              title: '暂无插件仓库',
              message: '点击右上角「添加仓库」添加插件源。',
            );
          }
          return AdminListWidth(
            top: 16,
            bottom: 32,
            builder: (padding) => ListView.separated(
              padding:
                  padding +
                  const EdgeInsets.symmetric(horizontal: CinemaSpacing.gutter),
              itemCount: repos.length,
              separatorBuilder: (_, _) => const SizedBox(height: 12),
              itemBuilder: (context, index) {
                final repo = repos[index];
                final isBusy = _busy.contains(repo.id);
                return _RepositoryCard(
                  repo: repo,
                  busy: isBusy,
                  onToggle: (val) => _setEnabled(repo, val),
                  onDelete: () => _delete(repo),
                );
              },
            ),
          );
        },
      ),
    );
  }
}

class _RepositoryCard extends StatelessWidget {
  const _RepositoryCard({
    required this.repo,
    required this.busy,
    required this.onToggle,
    required this.onDelete,
  });

  final PluginRepository repo;
  final bool busy;
  final ValueChanged<bool> onToggle;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final statusColor = switch (repo.status) {
      RepositoryStatus.ok => CinemaColors.text2,
      RepositoryStatus.pending => CinemaColors.amber,
      RepositoryStatus.error ||
      RepositoryStatus.unsupported => CinemaColors.error,
    };

    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Expanded(
                  child: Row(
                    children: [
                      Flexible(
                        child: Text(
                          repo.name.isNotEmpty ? repo.name : repo.url,
                          style: text.titleMedium,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      if (repo.official) ...[
                        const SizedBox(width: 8),
                        Container(
                          padding: const EdgeInsets.symmetric(
                            horizontal: 6,
                            vertical: 2,
                          ),
                          decoration: BoxDecoration(
                            color: CinemaColors.amber.withValues(alpha: 0.15),
                            borderRadius: BorderRadius.circular(
                              CinemaRadius.pill,
                            ),
                          ),
                          child: Text(
                            '官方',
                            style: text.labelSmall?.copyWith(
                              color: CinemaColors.amber,
                            ),
                          ),
                        ),
                      ],
                    ],
                  ),
                ),
                Switch(value: repo.enabled, onChanged: busy ? null : onToggle),
                if (!repo.official)
                  IconButton(
                    icon: const Icon(Icons.delete_outline),
                    tooltip: '删除仓库',
                    onPressed: busy ? null : onDelete,
                  )
                else
                  const Tooltip(
                    message: '官方仓库不可删除',
                    child: IconButton(
                      icon: Icon(Icons.delete_outline),
                      onPressed: null,
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 6),
            Text(
              repo.url,
              style: text.bodySmall?.copyWith(color: CinemaColors.text2),
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 16,
              runSpacing: 4,
              children: [
                Text(
                  '${repo.pluginCount} 个插件'
                  '${repo.invalidCount > 0 ? '（${repo.invalidCount} 个无效已跳过）' : ''}',
                  style: text.bodySmall?.copyWith(color: CinemaColors.text2),
                ),
                Text(
                  '最后检查：${repo.lastCheckedAt != null ? formatDateTime(repo.lastCheckedAt!) : '未检查'}',
                  style: text.bodySmall?.copyWith(color: CinemaColors.text2),
                ),
                Text(
                  repo.status.label,
                  style: text.bodySmall?.copyWith(color: statusColor),
                ),
              ],
            ),
            if (repo.error != null && repo.error!.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(
                '拉取失败：${repo.error}',
                style: text.bodySmall?.copyWith(color: CinemaColors.error),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _AddRepositoryDialog extends ConsumerStatefulWidget {
  const _AddRepositoryDialog();

  @override
  ConsumerState<_AddRepositoryDialog> createState() =>
      _AddRepositoryDialogState();
}

class _AddRepositoryDialogState extends ConsumerState<_AddRepositoryDialog> {
  final _controller = TextEditingController();
  bool _acceptRisk = false;
  bool _submitting = false;
  String? _error;

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final url = _controller.text.trim();
    if (url.isEmpty) {
      setState(() => _error = '请输入仓库地址');
      return;
    }
    if (!_acceptRisk) {
      setState(() => _error = '请阅读并勾选风险确认');
      return;
    }

    setState(() {
      _submitting = true;
      _error = null;
    });

    try {
      await addPluginRepository(
        ref.read(apiClientProvider),
        url: url,
        acceptRisk: true,
      );
      if (mounted) Navigator.of(context).pop(true);
    } catch (error) {
      if (mounted) {
        setState(() {
          _submitting = false;
          _error = ApiError.from(error).message;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      title: const Text('添加插件仓库'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            TextField(
              controller: _controller,
              autofocus: true,
              enabled: !_submitting,
              decoration: InputDecoration(
                labelText: '仓库索引地址（index.json）',
                hintText: 'https://example.com/iris-plugins/index.json',
                errorText: _error,
              ),
              onSubmitted: (_) => _submit(),
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
                      '第三方仓库提供的插件将在宿主独立进程运行，具有系统权限，请确认仓库来源可信。',
                      style: Theme.of(context).textTheme.bodySmall
                          ?.copyWith(color: CinemaColors.text2),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 8),
            CheckboxListTile(
              contentPadding: EdgeInsets.zero,
              value: _acceptRisk,
              onChanged: _submitting
                  ? null
                  : (val) => setState(() => _acceptRisk = val ?? false),
              title: const Text('我已知晓并承担使用第三方插件仓库的风险'),
              controlAffinity: ListTileControlAffinity.leading,
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: _submitting ? null : () => Navigator.of(context).pop(),
          child: const Text('取消'),
        ),
        FilledButton(
          onPressed: _submitting || !_acceptRisk ? null : _submit,
          child: _submitting
              ? const SizedBox(
                  width: 16,
                  height: 16,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Text('添加'),
        ),
      ],
    );
  }
}
