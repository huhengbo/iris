import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/format.dart';
import '../../core/providers.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/dropdown_field.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/form_error_text.dart';
import 'admin_widgets.dart';
import 'resources_api.dart';

const _maxNameLength = 64;

/// 资源来源列表：网盘、服务器本地目录等。资料库的目录从这些来源里选。
class ResourcesPage extends ConsumerWidget {
  const ResourcesPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final sources = ref.watch(resourceSourcesProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('资源来源'),
        actions: [
          IconButton(
            tooltip: '刷新',
            onPressed: () => ref.invalidate(resourceSourcesProvider),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      floatingActionButton: FocusRing(
        radius: CinemaRadius.fab,
        child: FloatingActionButton.extended(
          onPressed: () => _create(context, ref),
          icon: const Icon(Icons.add),
          label: const Text('新建来源'),
        ),
      ),
      body: AsyncView(
        value: sources,
        onRetry: () => ref.invalidate(resourceSourcesProvider),
        builder: (items) {
          if (items.isEmpty) {
            return const EmptyState(
              icon: Icons.storage_outlined,
              title: '还没有资源来源',
              message: '资源来源提供视频文件，资料库的目录从来源里选择。',
            );
          }
          return RefreshIndicator(
            onRefresh: () => ref.refresh(resourceSourcesProvider.future),
            child: AdminListWidth(
              bottom: 88,
              builder: (padding) => ListView(
                padding: padding,
                children: [
                  for (final source in items)
                    ResourceSourceTile(
                      source: source,
                      onTap: () =>
                          context.push('/settings/manage/sources/${source.id}'),
                    ),
                ],
              ),
            ),
          );
        },
      ),
    );
  }

  Future<void> _create(BuildContext context, WidgetRef ref) async {
    final created = await showDialog<ResourceSourceInfo>(
      context: context,
      builder: (_) => const CreateSourceDialog(),
    );
    if (created == null) return;
    ref.invalidate(resourceSourcesProvider);
    if (context.mounted) context.push('/settings/manage/sources/${created.id}');
  }
}

class ResourceSourceTile extends StatelessWidget {
  const ResourceSourceTile({super.key, required this.source, this.onTap});

  final ResourceSourceInfo source;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final reason = source.unusableReason;
    final facts = [
      if (source.pluginName != source.name) source.pluginName,
      reason ?? resourceStatusLabel(source.status),
      if (source.checkedAt != null) '检查于 ${formatIsoAgo(source.checkedAt)}',
    ];
    final error = reason == null && source.status == 'error'
        ? source.lastError
        : null;
    return FocusRing(
      child: ListTile(
        leading: CircleAvatar(
          child: Icon(
            Icons.storage_outlined,
            color: reason != null ? CinemaColors.text2 : null,
          ),
        ),
        title: Text(source.name, maxLines: 1, overflow: TextOverflow.ellipsis),
        subtitle: Text.rich(
          TextSpan(
            text: facts.join(' · '),
            children: [
              if (error != null)
                TextSpan(
                  text: '\n$error',
                  style: const TextStyle(color: CinemaColors.error),
                ),
            ],
          ),
          maxLines: 3,
          overflow: TextOverflow.ellipsis,
        ),
        isThreeLine: error != null,
        trailing: const Icon(Icons.chevron_right),
        onTap: onTap,
      ),
    );
  }
}

/// 新建来源：选插件、起名字、填插件要求的配置。
class CreateSourceDialog extends ConsumerStatefulWidget {
  const CreateSourceDialog({super.key});

  @override
  ConsumerState<CreateSourceDialog> createState() => _CreateSourceDialogState();
}

class _CreateSourceDialogState extends ConsumerState<CreateSourceDialog> {
  final _name = TextEditingController();
  final Map<String, TextEditingController> _config = {};
  String? _pluginId;
  bool _submitting = false;
  String? _nameError;
  String? _error;

  @override
  void dispose() {
    _name.dispose();
    for (final controller in _config.values) {
      controller.dispose();
    }
    super.dispose();
  }

  void _select(ResourceProviderInfo provider) {
    for (final controller in _config.values) {
      controller.dispose();
    }
    _config
      ..clear()
      ..addAll({
        for (final field in provider.configFields)
          field.key: TextEditingController(text: field.defaultValue),
      });
    setState(() {
      _pluginId = provider.pluginId;
      _error = null;
      if (_name.text.trim().isEmpty) _name.text = provider.name;
    });
  }

  Future<void> _submit(ResourceProviderInfo? provider) async {
    if (_submitting || provider == null) return;
    final name = _name.text.trim();
    setState(() {
      _nameError = name.isEmpty ? '请输入名称' : null;
      _error = null;
    });
    if (_nameError != null) return;
    setState(() => _submitting = true);
    try {
      final created = await createResourceSource(
        ref.read(apiClientProvider),
        pluginId: provider.pluginId,
        name: name,
        config: {
          for (final entry in _config.entries) entry.key: entry.value.text,
        },
      );
      if (mounted) Navigator.of(context).pop(created);
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
    final providers = ref.watch(resourceProvidersProvider);
    final creatable = [
      for (final provider in providers.value ?? <ResourceProviderInfo>[])
        if (provider.canCreate) provider,
    ];
    final provider = creatable
        .where((item) => item.pluginId == _pluginId)
        .firstOrNull;
    if (provider == null && creatable.isNotEmpty && !_submitting) {
      // 第一次拿到插件列表时默认选第一个
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && _pluginId == null) _select(creatable.first);
      });
    }
    return AlertDialog(
      title: const Text('新建资源来源'),
      content: SizedBox(
        width: 520,
        child: AsyncView(
          value: providers,
          onRetry: () => ref.invalidate(resourceProvidersProvider),
          builder: (_) {
            if (creatable.isEmpty) {
              return const Text('没有可以新建来源的资源插件。先到「插件」里启用资源插件。');
            }
            return SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  AppDropdownField<String>(
                    // 下拉框只在创建时读取初始值，默认选中第一个插件后要重建
                    key: ValueKey(provider?.pluginId),
                    label: '类型',
                    value: provider?.pluginId,
                    items: [
                      for (final item in creatable)
                        DropdownMenuItem(
                          value: item.pluginId,
                          child: Text(item.name),
                        ),
                    ],
                    onChanged: _submitting
                        ? null
                        : (value) => _select(
                            creatable.firstWhere((p) => p.pluginId == value),
                          ),
                  ),
                  const SizedBox(height: 16),
                  TextField(
                    controller: _name,
                    enabled: !_submitting,
                    inputFormatters: [
                      LengthLimitingTextInputFormatter(_maxNameLength),
                    ],
                    decoration: InputDecoration(
                      labelText: '名称',
                      hintText: '例如：NAS 电影盘',
                      errorText: _nameError,
                    ),
                  ),
                  for (final field
                      in provider?.configFields ??
                          <ResourceConfigFieldInfo>[]) ...[
                    const SizedBox(height: 16),
                    TextField(
                      controller: _config[field.key],
                      enabled: !_submitting,
                      decoration: InputDecoration(
                        labelText: field.label,
                        helperText: field.help.isEmpty ? null : field.help,
                        helperMaxLines: 3,
                      ),
                      onSubmitted: (_) => _submit(provider),
                    ),
                  ],
                  if (_error != null) ...[
                    const SizedBox(height: 16),
                    FormErrorText(_error!),
                  ],
                ],
              ),
            );
          },
        ),
      ),
      actions: [
        TextButton(
          onPressed: _submitting ? null : () => Navigator.of(context).pop(),
          child: const Text('取消'),
        ),
        FilledButton(
          onPressed: _submitting || provider == null
              ? null
              : () => _submit(provider),
          child: _submitting
              ? const SizedBox.square(
                  dimension: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Text('创建'),
        ),
      ],
    );
  }
}
