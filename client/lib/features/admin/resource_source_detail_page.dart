import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/format.dart';
import '../../core/providers.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/form_error_text.dart';
import '../../core/widgets/notice.dart';
import '../../core/widgets/snack.dart';
import '../../core/widgets/text_prompt.dart';
import 'admin_widgets.dart';
import 'resources_api.dart';

/// 单个资源来源：状态、启停、配置，以及哪些资料库在用它的目录。
/// 目录在资料库的编辑对话框里添加和移除，这里只读。
class ResourceSourceDetailPage extends ConsumerStatefulWidget {
  const ResourceSourceDetailPage({super.key, required this.sourceId});
  final int sourceId;

  @override
  ConsumerState<ResourceSourceDetailPage> createState() =>
      _ResourceSourceDetailPageState();
}

class _ResourceSourceDetailPageState
    extends ConsumerState<ResourceSourceDetailPage> {
  bool _busy = false;

  Future<void> _run(Future<void> Function() action, String? done) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      await action();
      ref
        ..invalidate(resourceSourcesProvider)
        ..invalidate(resourceProvidersProvider);
      if (mounted && done != null) showSnack(context, done);
    } catch (e) {
      if (mounted) showApiErrorSnack(context, e);
    }
    if (mounted) setState(() => _busy = false);
  }

  Future<void> _rename(ResourceSourceInfo source) async {
    final name = await showTextPrompt(
      context,
      title: '重命名来源',
      label: '名称',
      action: '保存',
      initial: source.name,
    );
    if (name == null || name == source.name) return;
    await _run(
      () => updateResourceSource(
        ref.read(apiClientProvider),
        source.id,
        name: name,
      ),
      '已重命名为「$name」',
    );
  }

  Future<void> _setEnabled(ResourceSourceInfo source, bool enabled) async {
    if (!enabled && source.folderCount > 0) {
      final confirmed = await showConfirmDialog(
        context,
        title: '停用「${source.name}」？',
        body:
            '有 ${source.folderCount} 个资料库目录来自这个来源。停用后同步会跳过它们，'
            '其中的视频不能播放，直到重新启用。已有的条目和观看记录会保留。',
        action: '停用',
        destructive: true,
      );
      if (!confirmed) return;
    }
    await _run(
      () => setResourceEnabled(ref.read(apiClientProvider), source.id, enabled),
      enabled ? '已启用「${source.name}」' : '已停用「${source.name}」',
    );
  }

  Future<void> _delete(ResourceSourceInfo source) async {
    final confirmed = await showConfirmDialog(
      context,
      title: '删除来源「${source.name}」？',
      body: '只删除本系统里的来源配置，存储上的文件不受影响。',
      action: '删除',
      destructive: true,
    );
    if (!confirmed || !mounted) return;
    final router = GoRouter.of(context);
    setState(() => _busy = true);
    try {
      await deleteResourceSource(ref.read(apiClientProvider), source.id);
      ref
        ..invalidate(resourceSourcesProvider)
        ..invalidate(resourceProvidersProvider);
      if (!mounted) return;
      showSnack(context, '已删除来源「${source.name}」');
      router.pop();
    } catch (e) {
      if (!mounted) return;
      showApiErrorSnack(context, e);
      setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final sources = ref.watch(resourceSourcesProvider);
    final source = sources.value
        ?.where((item) => item.id == widget.sourceId)
        .firstOrNull;
    return Scaffold(
      appBar: AppBar(
        title: Text(source?.name ?? '资源来源'),
        actions: [
          if (source != null)
            IconButton(
              tooltip: '重命名',
              onPressed: _busy ? null : () => _rename(source),
              icon: const Icon(Icons.edit_outlined),
            ),
        ],
      ),
      body: AsyncView(
        value: sources,
        onRetry: () => ref.invalidate(resourceSourcesProvider),
        builder: (_) {
          if (source == null) {
            return const Center(child: Text('资源来源不存在或已删除'));
          }
          return AdminListWidth(
            builder: (padding) => ListView(
              padding: padding.add(const EdgeInsets.symmetric(vertical: 8)),
              children: _sections(context, source),
            ),
          );
        },
      ),
    );
  }

  List<Widget> _sections(BuildContext context, ResourceSourceInfo source) {
    final text = Theme.of(context).textTheme;
    final muted = text.bodySmall?.copyWith(color: CinemaColors.text2);
    final facts = [
      if (source.pluginName != source.name) source.pluginName,
      resourceStatusLabel(source.status),
      if (source.checkedAt != null) '检查于 ${formatIsoAgo(source.checkedAt)}',
    ];
    Widget heading(String title) => Padding(
      padding: const EdgeInsets.fromLTRB(16, 24, 16, 4),
      child: Text(title, style: text.titleSmall),
    );
    return [
      Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16),
        child: Text(facts.join(' · '), style: muted),
      ),
      if (!source.pluginEnabled)
        _padded(
          Notice(
            icon: Icons.extension_off_outlined,
            color: CinemaColors.amber,
            title: '插件已停用',
            body:
                '插件「${source.pluginName}」停用后这个来源不可用：同步会跳过它的目录，其中的视频不能播放。到「插件」里启用后恢复。',
          ),
        )
      else if (source.status == 'error' && source.lastError != null)
        _padded(
          Notice(
            icon: Icons.error_outline,
            color: CinemaColors.error,
            title: '上次同步出错',
            body: source.lastError!,
          ),
        ),
      const SizedBox(height: 8),
      FocusRing(
        child: SwitchListTile(
          title: const Text('启用'),
          value: source.enabled,
          onChanged: _busy ? null : (value) => _setEnabled(source, value),
        ),
      ),
      _ConfigSection(
        source: source,
        busy: _busy,
        onSaved: () {
          ref.invalidate(resourceSourcesProvider);
          showSnack(context, '已保存配置');
        },
      ),
      heading('删除来源'),
      if (source.folderCount > 0)
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: Text('还有资料库在用这个来源的目录，先在资料库里移除这些目录才能删除。', style: muted),
        ),
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
        child: Align(
          alignment: Alignment.centerLeft,
          child: OutlinedButton.icon(
            style: OutlinedButton.styleFrom(
              foregroundColor: CinemaColors.error,
            ),
            onPressed: _busy || source.folderCount > 0
                ? null
                : () => _delete(source),
            icon: const Icon(Icons.delete_outline),
            label: const Text('删除来源'),
          ),
        ),
      ),
    ];
  }
}

Widget _padded(Widget child) =>
    Padding(padding: const EdgeInsets.fromLTRB(16, 16, 16, 0), child: child);

/// 插件要求的配置项。插件停用时拿不到配置项定义，不显示。
class _ConfigSection extends ConsumerStatefulWidget {
  const _ConfigSection({
    required this.source,
    required this.busy,
    required this.onSaved,
  });

  final ResourceSourceInfo source;
  final bool busy;
  final VoidCallback onSaved;

  @override
  ConsumerState<_ConfigSection> createState() => _ConfigSectionState();
}

class _ConfigSectionState extends ConsumerState<_ConfigSection> {
  final Map<String, TextEditingController> _fields = {};
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    for (final controller in _fields.values) {
      controller.dispose();
    }
    super.dispose();
  }

  TextEditingController _field(ResourceConfigFieldInfo field) =>
      _fields.putIfAbsent(
        field.key,
        () => TextEditingController(
          text: '${widget.source.config[field.key] ?? field.defaultValue}',
        ),
      );

  Future<void> _save(List<ResourceConfigFieldInfo> fields) async {
    if (_saving) return;
    setState(() {
      _error = null;
      _saving = true;
    });
    final config = {
      for (final field in fields) field.key: _field(field).text.trim(),
    };
    try {
      await updateResourceSource(
        ref.read(apiClientProvider),
        widget.source.id,
        config: config,
      );
    } catch (e) {
      // 配置不合法时把原因留在表单下方，不只是一闪而过的提示
      if (mounted) {
        setState(() {
          _error = ApiError.from(e).message;
          _saving = false;
        });
      }
      return;
    }
    if (!mounted) return;
    setState(() => _saving = false);
    widget.onSaved();
  }

  @override
  Widget build(BuildContext context) {
    final provider = ref
        .watch(resourceProvidersProvider)
        .value
        ?.where((item) => item.pluginId == widget.source.pluginId)
        .firstOrNull;
    final fields = provider?.configFields ?? const [];
    if (fields.isEmpty) return const SizedBox.shrink();
    final disabled = widget.busy || _saving;
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 24, 16, 0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Text('配置', style: Theme.of(context).textTheme.titleSmall),
          for (final field in fields) ...[
            const SizedBox(height: 12),
            TextField(
              controller: _field(field),
              enabled: !disabled,
              decoration: InputDecoration(
                labelText: field.label,
                helperText: field.help.isEmpty ? null : field.help,
                helperMaxLines: 3,
              ),
            ),
          ],
          if (widget.source.folderCount > 0) ...[
            const SizedBox(height: 8),
            Text(
              '修改后资料库目录仍按原来的相对位置查找，下次同步时生效。',
              style: Theme.of(context).textTheme.bodySmall
                  ?.copyWith(color: CinemaColors.text2),
            ),
          ],
          if (_error != null) ...[
            const SizedBox(height: 12),
            FormErrorText(_error!),
          ],
          const SizedBox(height: 12),
          Align(
            alignment: Alignment.centerLeft,
            child: FilledButton.tonal(
              onPressed: disabled ? null : () => _save(fields),
              child: _saving
                  ? const SizedBox.square(
                      dimension: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Text('保存配置'),
            ),
          ),
        ],
      ),
    );
  }
}
