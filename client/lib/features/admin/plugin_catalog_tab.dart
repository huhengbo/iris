import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/format.dart';
import '../../core/providers.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/section_title.dart';
import '../../core/widgets/snack.dart';
import 'admin_widgets.dart';
import 'plugin_catalog_api.dart';
import 'plugin_task_dialog.dart';
import 'plugins_api.dart';

/// 插件目录标签页：按分类排布的卡片网格，展示各仓库插件及兼容/冲突状态
class PluginCatalogTab extends ConsumerStatefulWidget {
  const PluginCatalogTab({super.key});

  @override
  ConsumerState<PluginCatalogTab> createState() => _PluginCatalogTabState();
}

class _PluginCatalogTabState extends ConsumerState<PluginCatalogTab> {
  final _busyPluginIds = <String>{};

  Future<void> _install(CatalogPlugin plugin) async {
    // 1. 弹出权限与信任确认对话框
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => _InstallPermissionsDialog(plugin: plugin),
    );
    if (confirmed != true || !mounted) return;

    setState(() => _busyPluginIds.add(plugin.id));
    final dio = ref.read(apiClientProvider);

    try {
      final taskId = await installCatalogPlugin(
        dio,
        repositoryId: plugin.repositoryId,
        pluginId: plugin.id,
        version: plugin.version,
        acceptPermissions: true,
      );
      if (!mounted) return;

      final action = plugin.status == CatalogPluginStatus.updateAvailable
          ? '升级'
          : '安装';
      final ok = await showPluginTaskProgressDialog(
        context,
        ref: ref,
        taskId: taskId,
        title: '$action「${plugin.name}」',
      );

      if (ok && mounted) {
        ref
          ..invalidate(pluginsProvider)
          ..invalidate(pluginCatalogProvider);
        showSnack(context, '插件${plugin.name}$action成功');
      }
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    } finally {
      if (mounted) setState(() => _busyPluginIds.remove(plugin.id));
    }
  }

  void _showDetail(CatalogPlugin plugin) {
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      backgroundColor: CinemaColors.surface1,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(
          top: Radius.circular(CinemaRadius.card),
        ),
      ),
      builder: (context) => _CatalogPluginDetailSheet(
        plugin: plugin,
        busy: _busyPluginIds.contains(plugin.id),
        onInstall: () {
          Navigator.of(context).pop();
          _install(plugin);
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return AsyncView(
      value: ref.watch(pluginCatalogProvider),
      onRetry: () => ref.invalidate(pluginCatalogProvider),
      builder: (catalog) {
        final plugins = catalog.plugins;
        if (plugins.isEmpty) {
          return const EmptyState(
            icon: Icons.storefront_outlined,
            title: '目录中暂无可用插件',
            message: '已启用的仓库中暂无插件，或未添加/启用插件仓库。',
          );
        }

        return AdminListWidth(
          top: 8,
          bottom: 32,
          maxWidth: adminGridWidth,
          builder: (padding) => ListView(
            padding:
                padding +
                const EdgeInsets.symmetric(horizontal: CinemaSpacing.gutter),
            children: [
              for (final category in PluginCategory.values)
                if (plugins.any((p) => p.category == category))
                  _CatalogGroup(
                    category: category,
                    plugins: [
                      for (final p in plugins)
                        if (p.category == category) p,
                    ],
                    first: category == PluginCategory.values.first,
                    busyIds: _busyPluginIds,
                    onTap: _showDetail,
                  ),
            ],
          ),
        );
      },
    );
  }
}

class _CatalogGroup extends StatelessWidget {
  const _CatalogGroup({
    required this.category,
    required this.plugins,
    required this.first,
    required this.busyIds,
    required this.onTap,
  });

  final PluginCategory category;
  final List<CatalogPlugin> plugins;
  final bool first;
  final Set<String> busyIds;
  final ValueChanged<CatalogPlugin> onTap;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SectionTitle(category.label, first: first, inset: false),
        LayoutBuilder(
          builder: (context, constraints) {
            final columns = constraints.maxWidth >= 900
                ? 3
                : constraints.maxWidth >= 560
                ? 2
                : 1;
            final extra = MediaQuery.textScalerOf(context).scale(1) - 1;
            return GridView.builder(
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                crossAxisCount: columns,
                crossAxisSpacing: 12,
                mainAxisSpacing: 12,
                mainAxisExtent: 210.0 + extra.clamp(0, 0.4).toDouble() * 70,
              ),
              itemCount: plugins.length,
              itemBuilder: (context, index) {
                final plugin = plugins[index];
                return _CatalogPluginCard(
                  plugin: plugin,
                  busy: busyIds.contains(plugin.id),
                  onTap: () => onTap(plugin),
                );
              },
            );
          },
        ),
      ],
    );
  }
}

class _CatalogPluginCard extends StatelessWidget {
  const _CatalogPluginCard({
    required this.plugin,
    required this.busy,
    required this.onTap,
  });

  final CatalogPlugin plugin;
  final bool busy;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final statusColor = switch (plugin.status) {
      CatalogPluginStatus.updateAvailable => CinemaColors.amber,
      CatalogPluginStatus.conflict ||
      CatalogPluginStatus.incompatible => CinemaColors.error,
      CatalogPluginStatus.available ||
      CatalogPluginStatus.installed => CinemaColors.text2,
    };

    return FocusRing(
      child: Card(
        margin: EdgeInsets.zero,
        child: InkWell(
          borderRadius: BorderRadius.circular(CinemaRadius.card),
          onTap: busy ? null : onTap,
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Row(
                  children: [
                    Container(
                      width: 36,
                      height: 36,
                      decoration: BoxDecoration(
                        color: CinemaColors.surface3,
                        borderRadius: BorderRadius.circular(CinemaRadius.small),
                      ),
                      child: Icon(
                        switch (plugin.category) {
                          PluginCategory.metadata => Icons.movie_outlined,
                          PluginCategory.subtitle => Icons.subtitles_outlined,
                          PluginCategory.resource => Icons.cloud_outlined,
                          PluginCategory.other => Icons.extension_outlined,
                        },
                        size: 20,
                        color: CinemaColors.amber,
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            plugin.name,
                            style: text.titleMedium,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                          Text(
                            'v${plugin.version} · ${plugin.repositoryName}',
                            style: text.bodySmall?.copyWith(
                              color: CinemaColors.text2,
                            ),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                if (plugin.description.isNotEmpty)
                  Expanded(
                    child: Text(
                      plugin.description,
                      style: text.bodySmall,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                  )
                else
                  const Spacer(),
                const SizedBox(height: 8),
                Row(
                  children: [
                    Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 8,
                        vertical: 2,
                      ),
                      decoration: BoxDecoration(
                        color: statusColor.withValues(alpha: 0.12),
                        borderRadius: BorderRadius.circular(CinemaRadius.pill),
                      ),
                      child: Text(
                        plugin.status.label,
                        style: text.labelSmall?.copyWith(color: statusColor),
                      ),
                    ),
                    if (plugin.reason != null &&
                        (plugin.status == CatalogPluginStatus.conflict ||
                            plugin.status ==
                                CatalogPluginStatus.incompatible)) ...[
                      const SizedBox(width: 8),
                      Expanded(
                        child: Text(
                          plugin.reason!,
                          style: text.bodySmall?.copyWith(
                            color: CinemaColors.error,
                          ),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ],
                  ],
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _CatalogPluginDetailSheet extends StatelessWidget {
  const _CatalogPluginDetailSheet({
    required this.plugin,
    required this.busy,
    required this.onInstall,
  });

  final CatalogPlugin plugin;
  final bool busy;
  final VoidCallback onInstall;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final canInstall =
        plugin.status == CatalogPluginStatus.available ||
        plugin.status == CatalogPluginStatus.updateAvailable;
    final statusColor = switch (plugin.status) {
      CatalogPluginStatus.updateAvailable => CinemaColors.amber,
      CatalogPluginStatus.conflict ||
      CatalogPluginStatus.incompatible => CinemaColors.error,
      _ => CinemaColors.text2,
    };

    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(plugin.name, style: text.headlineSmall),
                        Text(
                          '${plugin.id} · v${plugin.version} · 来自「${plugin.repositoryName}」',
                          style: text.bodySmall?.copyWith(
                            color: CinemaColors.text2,
                          ),
                        ),
                      ],
                    ),
                  ),
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 10,
                      vertical: 4,
                    ),
                    decoration: BoxDecoration(
                      color: statusColor.withValues(alpha: 0.12),
                      borderRadius: BorderRadius.circular(CinemaRadius.pill),
                    ),
                    child: Text(
                      plugin.status.label,
                      style: text.labelMedium?.copyWith(color: statusColor),
                    ),
                  ),
                ],
              ),
              if (plugin.reason != null && plugin.reason!.isNotEmpty) ...[
                const SizedBox(height: 12),
                Container(
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: CinemaColors.error.withValues(alpha: 0.12),
                    borderRadius: BorderRadius.circular(CinemaRadius.small),
                  ),
                  child: Text(
                    '无法安装原因：${plugin.reason}',
                    style: text.bodySmall?.copyWith(color: CinemaColors.error),
                  ),
                ),
              ],
              const SizedBox(height: 16),
              if (plugin.description.isNotEmpty) ...[
                Text(plugin.description, style: text.bodyMedium),
                const SizedBox(height: 16),
              ],
              Wrap(
                spacing: 16,
                runSpacing: 8,
                children: [
                  if (plugin.sdk.isNotEmpty)
                    Text('SDK 兼容：${plugin.sdk}', style: text.bodySmall),
                  if (plugin.size > 0)
                    Text(
                      '包大小：${formatSize(plugin.size)}',
                      style: text.bodySmall,
                    ),
                  if (plugin.installedVersion != null)
                    Text(
                      '当前已装：v${plugin.installedVersion}',
                      style: text.bodySmall,
                    ),
                ],
              ),
              const SizedBox(height: 12),
              if (plugin.provides.isNotEmpty)
                Text(
                  '声明能力：${plugin.provides.join('、')}',
                  style: text.bodySmall?.copyWith(color: CinemaColors.text2),
                ),
              if (plugin.capabilities.isNotEmpty)
                Text(
                  '所需权限：${plugin.capabilities.join('、')}',
                  style: text.bodySmall?.copyWith(color: CinemaColors.text2),
                ),
              const SizedBox(height: 24),
              FilledButton(
                onPressed: (!canInstall || busy) ? null : onInstall,
                child: Text(
                  plugin.status == CatalogPluginStatus.updateAvailable
                      ? '立即升级'
                      : canInstall
                      ? '安装插件'
                      : '不可安装',
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _InstallPermissionsDialog extends StatelessWidget {
  const _InstallPermissionsDialog({required this.plugin});

  final CatalogPlugin plugin;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final isUpgrade = plugin.status == CatalogPluginStatus.updateAvailable;

    return AlertDialog(
      title: Text(isUpgrade ? '升级「${plugin.name}」' : '安装「${plugin.name}」'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('插件 ID：${plugin.id}', style: text.bodySmall),
            Text('来源仓库：${plugin.repositoryName}', style: text.bodySmall),
            Text('目标版本：v${plugin.version}', style: text.bodySmall),
            if (plugin.size > 0)
              Text('大小：${formatSize(plugin.size)}', style: text.bodySmall),
            const SizedBox(height: 12),
            if (plugin.provides.isNotEmpty) ...[
              Text(
                '声明提供能力：',
                style: text.bodySmall?.copyWith(fontWeight: FontWeight.bold),
              ),
              Text(plugin.provides.join('、'), style: text.bodySmall),
              const SizedBox(height: 8),
            ],
            if (plugin.capabilities.isNotEmpty) ...[
              Text(
                '所需权限：',
                style: text.bodySmall?.copyWith(fontWeight: FontWeight.bold),
              ),
              Text(plugin.capabilities.join('、'), style: text.bodySmall),
              const SizedBox(height: 8),
            ],
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: CinemaColors.surface3,
                borderRadius: BorderRadius.circular(CinemaRadius.small),
              ),
              child: Text(
                '插件代码由第三方仓库提供并在独立进程中执行，拥有管理员权限，请确认来源受信任。',
                style: text.bodySmall?.copyWith(color: CinemaColors.text2),
              ),
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
          onPressed: () => Navigator.of(context).pop(true),
          child: Text(isUpgrade ? '确认升级' : '确认安装'),
        ),
      ],
    );
  }
}
