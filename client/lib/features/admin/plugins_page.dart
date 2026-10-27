import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/providers.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/section_title.dart';
import '../../core/widgets/snack.dart';
import 'admin_widgets.dart';
import 'busy_icon.dart';
import 'plugins_api.dart';
import 'resources_api.dart';

/// 插件中心：按分类分组的卡片，操作都收在卡片右上角的菜单里。
class PluginsPage extends ConsumerStatefulWidget {
  const PluginsPage({super.key});

  @override
  ConsumerState<PluginsPage> createState() => _PluginsPageState();
}

class _PluginsPageState extends ConsumerState<PluginsPage> {
  final _busy = <String>{};

  Future<void> _setEnabled(
    AdminPlugin plugin,
    bool enabled,
    List<ResourceSourceInfo> sources,
  ) async {
    if (!enabled && sources.isNotEmpty) {
      // 停用资源插件会让它的来源整体不可用，先说清楚影响哪些来源
      final folders = sources.fold(0, (sum, s) => sum + s.folderCount);
      final confirmed = await showConfirmDialog(
        context,
        title: '停用「${plugin.name}」？',
        body:
            '来源 ${sources.map((s) => '「${s.name}」').join('、')} 会不可用'
            '${folders > 0 ? '，$folders 个资料库目录的同步会跳过，其中的视频不能播放' : ''}。'
            '重新启用后恢复。',
        action: '停用',
        destructive: true,
      );
      if (!confirmed || !mounted) return;
    }
    setState(() => _busy.add(plugin.id));
    try {
      await setPluginEnabled(ref.read(apiClientProvider), plugin, enabled);
      ref
        ..invalidate(pluginsProvider)
        ..invalidate(resourceSourcesProvider);
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    } finally {
      if (mounted) setState(() => _busy.remove(plugin.id));
    }
  }

  Future<void> _remove(AdminPlugin plugin) async {
    final confirmed = await showConfirmDialog(
      context,
      title: '移除「${plugin.name}」的记录？',
      body: '当前进程没有安装这个插件，移除后它不会再出现在插件列表里，密钥和配置也会一并删除。',
      action: '移除',
      destructive: true,
    );
    if (!confirmed || !mounted) return;
    setState(() => _busy.add(plugin.id));
    try {
      await deletePlugin(ref.read(apiClientProvider), plugin.id);
      ref.invalidate(pluginsProvider);
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    } finally {
      if (mounted) setState(() => _busy.remove(plugin.id));
    }
  }

  @override
  Widget build(BuildContext context) {
    final sources =
        ref.watch(resourceSourcesProvider).value ??
        const <ResourceSourceInfo>[];
    List<ResourceSourceInfo> sourcesOf(AdminPlugin plugin) => [
      for (final source in sources)
        if (source.pluginId == plugin.id) source,
    ];
    return Scaffold(
      appBar: AppBar(
        title: const Text('插件中心'),
        actions: [
          IconButton(
            tooltip: '刷新',
            onPressed: () => ref.invalidate(pluginsProvider),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: AsyncView(
        value: ref.watch(pluginsProvider),
        onRetry: () => ref.invalidate(pluginsProvider),
        builder: (plugins) {
          if (plugins.isEmpty) {
            return const EmptyState(
              icon: Icons.extension_outlined,
              title: '还没有插件',
              message: '把插件放进服务端的插件目录后重启，它们会出现在这里。',
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
                  if (plugins.any((plugin) => plugin.category == category))
                    _PluginGroup(
                      category: category,
                      plugins: [
                        for (final plugin in plugins)
                          if (plugin.category == category) plugin,
                      ],
                      first: category == PluginCategory.values.first,
                      busy: _busy,
                      onOpen: (plugin) =>
                          context.push('/settings/manage/plugins/${plugin.id}'),
                      onToggle: (plugin) => _setEnabled(
                        plugin,
                        !plugin.enabled,
                        sourcesOf(plugin),
                      ),
                      onRemove: _remove,
                    ),
              ],
            ),
          );
        },
      ),
    );
  }
}

/// 一组同分类的插件：节标题 + 响应式的卡片网格。
class _PluginGroup extends StatelessWidget {
  const _PluginGroup({
    required this.category,
    required this.plugins,
    required this.first,
    required this.busy,
    required this.onOpen,
    required this.onToggle,
    required this.onRemove,
  });

  final PluginCategory category;
  final List<AdminPlugin> plugins;
  final bool first;
  final Set<String> busy;
  final ValueChanged<AdminPlugin> onOpen;
  final ValueChanged<AdminPlugin> onToggle;
  final ValueChanged<AdminPlugin> onRemove;

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
            // TV 的字号更大，卡片按文字缩放留出余量，避免简介被挤出去
            final extra = MediaQuery.textScalerOf(context).scale(1) - 1;
            return GridView.builder(
              shrinkWrap: true,
              physics: const NeverScrollableScrollPhysics(),
              gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                crossAxisCount: columns,
                crossAxisSpacing: 12,
                mainAxisSpacing: 12,
                mainAxisExtent: 204.0 + extra.clamp(0, 0.4).toDouble() * 70,
              ),
              itemCount: plugins.length,
              itemBuilder: (context, index) {
                final plugin = plugins[index];
                return _PluginCard(
                  plugin: plugin,
                  busy: busy.contains(plugin.id),
                  onOpen: () => onOpen(plugin),
                  onToggle: () => onToggle(plugin),
                  onRemove: () => onRemove(plugin),
                );
              },
            );
          },
        ),
      ],
    );
  }
}

enum _PluginAction { settings, toggle, remove }

class _PluginCard extends StatelessWidget {
  const _PluginCard({
    required this.plugin,
    required this.busy,
    required this.onOpen,
    required this.onToggle,
    required this.onRemove,
  });

  final AdminPlugin plugin;
  final bool busy;
  final VoidCallback onOpen;
  final VoidCallback onToggle;
  final VoidCallback onRemove;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final canToggle = plugin.state != PluginState.notInstalled;
    final color = switch (plugin.state) {
      PluginState.failed => CinemaColors.error,
      PluginState.notInstalled => CinemaColors.text2,
      PluginState.stopped => CinemaColors.text2,
      PluginState.running => CinemaColors.amber,
    };
    return FocusRing(
      child: Card(
        margin: EdgeInsets.zero,
        child: InkWell(
          borderRadius: BorderRadius.circular(CinemaRadius.card),
          onTap: busy ? null : onOpen,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 8, 16),
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
                        color: color,
                      ),
                    ),
                    const Spacer(),
                    if (busy)
                      const BusyIcon()
                    else
                      PopupMenuButton<_PluginAction>(
                        tooltip: '管理 ${plugin.name}',
                        onSelected: (action) {
                          switch (action) {
                            case _PluginAction.settings:
                              onOpen();
                            case _PluginAction.toggle:
                              onToggle();
                            case _PluginAction.remove:
                              onRemove();
                          }
                        },
                        itemBuilder: (_) => [
                          const PopupMenuItem(
                            value: _PluginAction.settings,
                            child: Text('设置'),
                          ),
                          if (canToggle)
                            PopupMenuItem(
                              value: _PluginAction.toggle,
                              child: Text(plugin.enabled ? '停用' : '启用'),
                            ),
                          if (plugin.state == PluginState.notInstalled)
                            const PopupMenuItem(
                              value: _PluginAction.remove,
                              child: Text('移除记录'),
                            ),
                        ],
                        icon: const Icon(Icons.more_horiz),
                      ),
                  ],
                ),
                const SizedBox(height: 6),
                Text(
                  plugin.name,
                  style: text.titleMedium,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                Text(
                  'v${plugin.version}',
                  style: text.bodySmall,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                const SizedBox(height: 6),
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
                const SizedBox(height: 6),
                Align(
                  alignment: Alignment.centerLeft,
                  child: _StateBadge(plugin.state),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _StateBadge extends StatelessWidget {
  const _StateBadge(this.state);

  final PluginState state;

  @override
  Widget build(BuildContext context) {
    final failed = state == PluginState.failed;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 2),
      decoration: BoxDecoration(
        color: failed
            ? CinemaColors.error.withValues(alpha: 0.12)
            : CinemaColors.surface3,
        borderRadius: BorderRadius.circular(CinemaRadius.pill),
      ),
      child: Text(
        state.label,
        style: Theme.of(context).textTheme.labelSmall
            ?.copyWith(color: failed ? CinemaColors.error : CinemaColors.text2),
      ),
    );
  }
}
