import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../app/tv.dart';
import '../../core/api/api_error.dart';
import '../../core/format.dart';
import '../../core/providers.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/notice.dart';
import '../../core/widgets/snack.dart';
import 'plugin_bridge.dart';
import 'plugin_frame/plugin_frame.dart';
import 'plugin_task_dialog.dart';
import 'plugin_uninstall_dialog.dart';
import 'plugins_api.dart';
import 'resources_api.dart';
import 'settings_api.dart';

/// 插件详情：基本信息、启停，以及插件自己提供的页面。
class PluginDetailPage extends ConsumerStatefulWidget {
  const PluginDetailPage({super.key, required this.pluginId, this.pageId});

  final String pluginId;

  /// 打开时选中的插件页面；来源状态里的 page 指向它，为空时用第一页
  final String? pageId;

  @override
  ConsumerState<PluginDetailPage> createState() => _PluginDetailPageState();
}

class _PluginDetailPageState extends ConsumerState<PluginDetailPage>
    with SingleTickerProviderStateMixin {
  bool _busy = false;

  /// TV 上不提供网络设置，只保留设置页面和关于
  late final bool _television;
  late final TabController _tabs;

  @override
  void initState() {
    super.initState();
    _television = ref.read(televisionProvider);
    _tabs = TabController(length: _television ? 2 : 3, vsync: this);
  }

  @override
  void dispose() {
    _tabs.dispose();
    super.dispose();
  }

  Future<void> _toggle(AdminPlugin plugin) async {
    await _setEnabled(plugin, !plugin.enabled);
  }

  Future<void> _retry(AdminPlugin plugin) async {
    await _setEnabled(plugin, true);
  }

  Future<void> _setEnabled(AdminPlugin plugin, bool enabled) async {
    setState(() => _busy = true);
    try {
      await setPluginEnabled(ref.read(apiClientProvider), plugin, enabled);
      ref.invalidate(pluginsProvider);
      ref.invalidate(pluginSessionProvider(plugin.id));
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _upgrade(AdminPlugin plugin) async {
    if (plugin.availableVersion == null) return;
    setState(() => _busy = true);
    try {
      final taskId = await upgradePlugin(
        ref.read(apiClientProvider),
        plugin.id,
        version: plugin.availableVersion!,
        acceptPermissions: true,
      );
      if (!mounted) return;
      final ok = await showPluginTaskProgressDialog(
        context,
        ref: ref,
        taskId: taskId,
        title: '升级「${plugin.name}」',
      );
      if (ok && mounted) {
        ref.invalidate(pluginsProvider);
        showSnack(context, '插件${plugin.name}升级成功');
      }
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _uninstall(AdminPlugin plugin) async {
    final deleteData = await showPluginUninstallConfirmDialog(
      context,
      pluginName: plugin.name,
    );
    if (deleteData == null || !mounted) return;
    setState(() => _busy = true);
    try {
      await uninstallPlugin(
        ref.read(apiClientProvider),
        plugin.id,
        deleteData: deleteData,
      );
      ref
        ..invalidate(pluginsProvider)
        ..invalidate(resourceSourcesProvider);
      if (!mounted) return;
      showSnack(context, '插件已卸载');
      if (Navigator.of(context).canPop()) Navigator.of(context).pop();
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
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
    setState(() => _busy = true);
    try {
      await deletePlugin(ref.read(apiClientProvider), plugin.id);
      ref.invalidate(pluginsProvider);
      if (!mounted) return;
      if (Navigator.of(context).canPop()) Navigator.of(context).pop();
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final plugins = ref.watch(pluginsProvider);
    final plugin = plugins.value
        ?.where((item) => item.id == widget.pluginId)
        .firstOrNull;
    return Scaffold(
      appBar: AppBar(title: Text(plugin?.name ?? '插件')),
      body: AsyncView(
        value: plugins,
        onRetry: () => ref.invalidate(pluginsProvider),
        builder: (_) => plugin == null
            ? const EmptyState(
                icon: Icons.extension_off_outlined,
                title: '插件不存在',
                message: '它可能已被卸载，返回插件中心刷新列表。',
              )
            : Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  _PluginHeader(
                    plugin: plugin,
                    busy: _busy,
                    onToggle: () => _toggle(plugin),
                    onRetry: () => _retry(plugin),
                    onUpgrade: () => _upgrade(plugin),
                    onUninstall: () => _uninstall(plugin),
                    onRemove: () => _remove(plugin),
                  ),
                  TabBar(
                    controller: _tabs,
                    isScrollable: true,
                    tabAlignment: TabAlignment.start,
                    padding: const EdgeInsets.symmetric(
                      horizontal: CinemaSpacing.gutter,
                    ),
                    labelColor: CinemaColors.amber,
                    unselectedLabelColor: CinemaColors.text2,
                    indicatorColor: CinemaColors.amber,
                    dividerColor: CinemaColors.line,
                    tabs: [
                      const Tab(text: '设置'),
                      if (!_television) const Tab(text: '网络'),
                      const Tab(text: '关于'),
                    ],
                  ),
                  Expanded(
                    child: TabBarView(
                      controller: _tabs,
                      // 插件页面是 WebView/iframe，横向滑动要留给页面本身
                      physics: const NeverScrollableScrollPhysics(),
                      children: [
                        _PluginPages(
                          plugin: plugin,
                          initialPageId: widget.pageId,
                        ),
                        if (!_television) _PluginNetwork(plugin: plugin),
                        _PluginAbout(plugin: plugin),
                      ],
                    ),
                  ),
                ],
              ),
      ),
    );
  }
}

class _PluginHeader extends StatelessWidget {
  const _PluginHeader({
    required this.plugin,
    required this.busy,
    required this.onToggle,
    required this.onRetry,
    required this.onUpgrade,
    required this.onUninstall,
    required this.onRemove,
  });

  final AdminPlugin plugin;
  final bool busy;
  final VoidCallback onToggle;
  final VoidCallback onRetry;
  final VoidCallback onUpgrade;
  final VoidCallback onUninstall;
  final VoidCallback onRemove;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final isSystem = plugin.source == PluginSource.system || plugin.builtIn;
    final sourceLabel = switch (plugin.source) {
      PluginSource.system => '系统内置',
      PluginSource.repository =>
        plugin.repositoryName?.isNotEmpty == true
            ? '第三方 · 来源：${plugin.repositoryName}'
            : '第三方仓库',
      PluginSource.local => '第三方 · 本地安装',
    };
    final details = [
      'v${plugin.version}',
      plugin.builtIn ? '内置' : sourceLabel,
      if (plugin.provides.isNotEmpty) '提供 ${plugin.provides.join('、')}',
      if (plugin.requires.isNotEmpty) '使用 ${plugin.requires.join('、')}',
    ];
    final canToggle = plugin.state != PluginState.notInstalled;
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        CinemaSpacing.gutter,
        8,
        CinemaSpacing.gutter,
        12,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  details.join(' · '),
                  style: text.bodySmall?.copyWith(color: CinemaColors.text2),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              const SizedBox(width: 12),
              Text(plugin.state.label),
              if (canToggle)
                Switch(
                  value: plugin.enabled,
                  onChanged: busy ? null : (_) => onToggle(),
                ),
            ],
          ),
          if (plugin.availableVersion != null) ...[
            const SizedBox(height: 8),
            Row(
              children: [
                const Icon(Icons.upgrade, color: CinemaColors.amber, size: 20),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    '有新版本可用：v${plugin.availableVersion}',
                    style: text.bodySmall?.copyWith(color: CinemaColors.amber),
                  ),
                ),
                FilledButton.tonal(
                  onPressed: busy ? null : onUpgrade,
                  child: const Text('升级'),
                ),
              ],
            ),
          ],
          if (plugin.state == PluginState.failed)
            Row(
              children: [
                Expanded(
                  child: Text(
                    '启动失败，请查看服务端日志',
                    style: text.bodySmall?.copyWith(color: CinemaColors.error),
                  ),
                ),
                TextButton(
                  onPressed: busy ? null : onRetry,
                  child: const Text('重试启动'),
                ),
              ],
            ),
          if (plugin.state == PluginState.notInstalled)
            Row(
              children: [
                Expanded(
                  child: Text(
                    '当前进程没有安装这个插件，不能启用。',
                    style: text.bodySmall?.copyWith(color: CinemaColors.text2),
                  ),
                ),
                TextButton(
                  onPressed: busy ? null : onRemove,
                  child: const Text('移除记录'),
                ),
              ],
            ),
          if (!isSystem && plugin.state != PluginState.notInstalled)
            Align(
              alignment: Alignment.centerLeft,
              child: TextButton.icon(
                style: TextButton.styleFrom(
                  foregroundColor: CinemaColors.error,
                  padding: EdgeInsets.zero,
                ),
                onPressed: busy ? null : onUninstall,
                icon: const Icon(Icons.delete_outline, size: 18),
                label: const Text('卸载插件'),
              ),
            ),
        ],
      ),
    );
  }
}

class _PluginNetwork extends ConsumerStatefulWidget {
  const _PluginNetwork({required this.plugin});

  final AdminPlugin plugin;

  @override
  ConsumerState<_PluginNetwork> createState() => _PluginNetworkState();
}

class _PluginNetworkState extends ConsumerState<_PluginNetwork> {
  late PluginProxyMode _mode = widget.plugin.proxyMode;
  late final _proxy = TextEditingController(text: widget.plugin.proxy ?? '');
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _proxy.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (_saving) return;
    final proxy = _mode == PluginProxyMode.custom ? _proxy.text.trim() : null;
    if (_mode == PluginProxyMode.custom && proxy!.isEmpty) {
      setState(() => _error = '请填写代理地址');
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      final updated = await setPluginProxy(
        ref.read(apiClientProvider),
        widget.plugin.id,
        _mode,
        proxy,
      );
      if (!mounted) return;
      setState(() {
        _mode = updated.proxyMode;
        _proxy.text = updated.proxy ?? '';
      });
      showSnack(context, '已保存');
      ref.invalidate(pluginsProvider);
    } catch (error) {
      if (mounted) setState(() => _error = ApiError.from(error).message);
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final settings = ref.watch(adminSettingsProvider).value;
    final globalProxy = settings?.pluginsProxy;
    final hasGlobal = globalProxy != null && globalProxy.isNotEmpty;
    return ListView(
      padding: const EdgeInsets.fromLTRB(
        CinemaSpacing.gutter,
        16,
        CinemaSpacing.gutter,
        32,
      ),
      children: [
        Text(
          '插件访问外网时使用的代理。沿用全局代理时，设置页里的插件代理一改，这个插件也跟着变。',
          style: text.bodyMedium?.copyWith(color: CinemaColors.text2),
        ),
        if (settings != null) ...[
          const SizedBox(height: 12),
          Row(
            children: [
              Icon(
                Icons.language,
                size: 18,
                color: hasGlobal ? CinemaColors.amber : CinemaColors.text2,
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  hasGlobal ? '全局代理：$globalProxy' : '全局代理：未设置',
                  style: text.bodyMedium,
                ),
              ),
            ],
          ),
        ],
        const SizedBox(height: 4),
        Text('全局代理在 设置 › 插件网络代理 里修改', style: text.bodySmall),
        const SizedBox(height: CinemaSpacing.section),
        Text('代理模式', style: text.titleSmall),
        RadioGroup<PluginProxyMode>(
          groupValue: _mode,
          onChanged: (value) {
            if (value != null && !_saving) setState(() => _mode = value);
          },
          child: Column(
            children: [
              for (final mode in PluginProxyMode.values)
                RadioListTile<PluginProxyMode>(
                  value: mode,
                  title: Text(mode.label),
                  enabled: !_saving,
                  contentPadding: EdgeInsets.zero,
                  activeColor: CinemaColors.amber,
                ),
            ],
          ),
        ),
        if (_mode == PluginProxyMode.custom) ...[
          const SizedBox(height: 4),
          TextField(
            controller: _proxy,
            autocorrect: false,
            maxLength: 1024,
            keyboardType: TextInputType.url,
            enabled: !_saving,
            decoration: const InputDecoration(
              labelText: '代理地址',
              hintText: 'http://127.0.0.1:7890 或 socks5://…',
              helperText: '密码以 *** 显示，不修改就原样保留',
              counterText: '',
            ),
            onSubmitted: (_) => _save(),
          ),
        ],
        if (_error != null) ...[
          const SizedBox(height: 8),
          Semantics(
            liveRegion: true,
            child: Text(
              _error!,
              style: text.bodyMedium?.copyWith(color: CinemaColors.error),
            ),
          ),
        ],
        const SizedBox(height: 16),
        Align(
          alignment: Alignment.centerLeft,
          child: FilledButton.tonal(
            onPressed: _saving ? null : _save,
            child: _saving
                ? const SizedBox.square(
                    dimension: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Text('保存'),
          ),
        ),
      ],
    );
  }
}

class _PluginAbout extends StatelessWidget {
  const _PluginAbout({required this.plugin});

  final AdminPlugin plugin;

  @override
  Widget build(BuildContext context) {
    final provides = plugin.provides.isEmpty ? '无' : plugin.provides.join('、');
    final requires = plugin.requires.isEmpty ? '无' : plugin.requires.join('、');
    final trust = plugin.builtIn
        ? '内置（${plugin.trust}）'
        : '第三方（${plugin.trust}）';
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(
        CinemaSpacing.gutter,
        16,
        CinemaSpacing.gutter,
        32,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _AboutField(
            label: '简介',
            value: plugin.description.isEmpty ? '没有简介' : plugin.description,
          ),
          const Divider(height: 24),
          _AboutField(label: '版本', value: 'v${plugin.version}'),
          const Divider(height: 24),
          _AboutField(label: '分类', value: plugin.category.label),
          const Divider(height: 24),
          _AboutField(label: '提供的能力', value: provides),
          const Divider(height: 24),
          _AboutField(label: '使用的宿主能力', value: requires),
          const Divider(height: 24),
          _AboutField(label: '信任级别', value: trust),
          const Divider(height: 24),
          _AboutField(label: '更新时间', value: formatDateTime(plugin.updatedAt)),
        ],
      ),
    );
  }
}

class _AboutField extends StatelessWidget {
  const _AboutField({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: text.bodySmall),
        const SizedBox(height: 2),
        Text(value, style: text.bodyMedium),
      ],
    );
  }
}

class _PluginPages extends ConsumerStatefulWidget {
  const _PluginPages({required this.plugin, this.initialPageId});

  final AdminPlugin plugin;

  /// 从来源状态跳进来时要打开的页面 id
  final String? initialPageId;

  @override
  ConsumerState<_PluginPages> createState() => _PluginPagesState();
}

class _PluginPagesState extends ConsumerState<_PluginPages> {
  late String? _pageId = widget.initialPageId;

  @override
  Widget build(BuildContext context) {
    final plugin = widget.plugin;
    if (plugin.pages.isEmpty) {
      return const EmptyState(
        icon: Icons.tune,
        title: '没有设置页面',
        message: '这个插件不需要单独配置。',
      );
    }
    if (plugin.state == PluginState.failed) {
      return const _PageNotice(
        icon: Icons.error_outline,
        title: '插件启动失败',
        body: '请查看服务端日志后重试启动。',
      );
    }
    if (!plugin.enabled) {
      return const _PageNotice(
        icon: Icons.pause_circle_outline,
        title: '插件已停用',
        body: '启用后才能打开它的设置页面。',
      );
    }
    if (ref.watch(televisionProvider)) {
      return const _PageNotice(
        icon: Icons.tv_off_outlined,
        title: '电视上无法配置插件',
        body: '请在网页端或手机上配置',
      );
    }
    final session = ref.watch(pluginSessionProvider(plugin.id));
    return AsyncView(
      value: session,
      onRetry: () => ref.invalidate(pluginSessionProvider(plugin.id)),
      builder: (session) {
        final pages = session.pages;
        final page = pages.firstWhere(
          (item) => item.id == _pageId,
          orElse: () => pages.first,
        );
        return Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (pages.length > 1)
              Padding(
                padding: const EdgeInsets.fromLTRB(
                  CinemaSpacing.gutter,
                  0,
                  CinemaSpacing.gutter,
                  12,
                ),
                child: Wrap(
                  spacing: 8,
                  children: [
                    for (final item in pages)
                      ChoiceChip(
                        label: Text(item.title),
                        selected: item.id == page.id,
                        onSelected: (_) => setState(() => _pageId = item.id),
                      ),
                  ],
                ),
              ),
            const Divider(height: 1),
            Expanded(
              child: _PluginFrameHost(
                key: ValueKey('${plugin.id}/${page.id}'),
                pluginId: plugin.id,
                page: page,
              ),
            ),
          ],
        );
      },
    );
  }
}

/// 为一个页面建立桥：页面准备好后由宿主发令牌，令牌过期时页面再来要。
class _PluginFrameHost extends ConsumerStatefulWidget {
  const _PluginFrameHost({
    super.key,
    required this.pluginId,
    required this.page,
  });

  final String pluginId;
  final PluginPageLink page;

  @override
  ConsumerState<_PluginFrameHost> createState() => _PluginFrameHostState();
}

class _PluginFrameHostState extends ConsumerState<_PluginFrameHost> {
  late final PluginBridge _bridge = PluginBridge(
    pluginId: widget.pluginId,
    issueToken: () async => (await createPluginSession(
      ref.read(apiClientProvider),
      widget.pluginId,
    )).token,
    onToast: (message) {
      if (mounted) showSnack(context, message);
    },
    onError: (error) {
      if (mounted) {
        showSnack(context, '无法打开插件页面：${ApiError.from(error).message}');
      }
    },
  );

  @override
  Widget build(BuildContext context) {
    final base = Uri.parse(ref.watch(apiBaseUrlProvider));
    final url = base.resolve(widget.page.url);
    return ref.watch(pluginFrameProvider)(url, _bridge);
  }
}

class _PageNotice extends StatelessWidget {
  const _PageNotice({
    required this.icon,
    required this.title,
    required this.body,
  });

  final IconData icon;
  final String title;
  final String body;

  @override
  Widget build(BuildContext context) => Align(
    alignment: Alignment.topCenter,
    child: Padding(
      padding: const EdgeInsets.all(CinemaSpacing.gutter),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 640),
        child: Notice(
          icon: icon,
          color: CinemaColors.amber,
          title: title,
          body: body,
        ),
      ),
    ),
  );
}
