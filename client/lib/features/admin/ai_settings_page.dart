import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/providers.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/dropdown_field.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/form_error_text.dart';
import '../../core/widgets/notice.dart';
import '../../core/widgets/picker_sheet.dart';
import '../../core/widgets/section_title.dart';
import '../../core/widgets/snack.dart';
import 'admin_widgets.dart';
import 'ai_api.dart';
import 'busy_icon.dart';

const _maxNameLength = 128;
const _maxUrlLength = 1024;
const _maxKeyLength = 2048;
const _maxModelLength = 256;
const _customPresetId = '';

/// 管理后台的 AI 助手配置页（M51）：模型提供方、模型用途、匹配建议、语义索引和用量。
class AiSettingsPage extends ConsumerWidget {
  const AiSettingsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final providers = ref.watch(aiProvidersProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('AI 助手'),
        actions: [
          IconButton(
            tooltip: '刷新',
            onPressed: () {
              ref
                ..invalidate(aiProvidersProvider)
                ..invalidate(aiRolesProvider)
                ..invalidate(aiSettingsProvider)
                ..invalidate(aiSemanticProvider)
                ..invalidate(aiUsageProvider);
            },
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: _Part<List<AiProvider>>(
        value: providers,
        onRetry: () => ref.invalidate(aiProvidersProvider),
        loading: const Center(child: CircularProgressIndicator()),
        builder: (items) => AdminListWidth(
          top: 8,
          bottom: 32,
          // 不用懒加载的 ListView：滚出屏幕的分段会被销毁，没保存的输入和轮询状态都会丢
          builder: (padding) => SingleChildScrollView(
            padding:
                padding +
                const EdgeInsets.symmetric(horizontal: CinemaSpacing.gutter),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Notice(
                  icon: Icons.privacy_tip_outlined,
                  color: CinemaColors.amber,
                  title: '隐私提示',
                  body:
                      '片名、简介、网盘文件名和用户在对话中输入的内容会发送给你配置的模型提供方；在意隐私可以用本地 Ollama。',
                ),
                if (items.isEmpty)
                  _NoProviders(onAdd: () => _addProvider(context, ref))
                else ...[
                  _ProvidersSection(providers: items),
                  _RolesPart(providers: items),
                  const _SettingsPart(),
                  const _SemanticSection(),
                  const _UsagePart(),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// 有数据（包括后台刷新中、刷新失败时的旧数据）就显示数据，不闪加载圈，
/// 也不让正在编辑的表单被重建；只有第一次加载失败才显示错误和重试。
class _Part<T> extends StatelessWidget {
  const _Part({
    required this.value,
    required this.onRetry,
    required this.builder,
    this.loading = const _SectionLoading(),
  });

  final AsyncValue<T> value;
  final VoidCallback onRetry;
  final Widget Function(T data) builder;
  final Widget loading;

  @override
  Widget build(BuildContext context) {
    if (value.hasValue) return builder(value.requireValue);
    if (value.hasError) {
      final error = ApiError.from(value.error!);
      return ErrorRetry(
        message: error.message,
        kind: error.kind,
        onRetry: onRetry,
      );
    }
    return loading;
  }
}

class _SectionLoading extends StatelessWidget {
  const _SectionLoading();

  @override
  Widget build(BuildContext context) => const Padding(
    padding: EdgeInsets.symmetric(vertical: 24),
    child: Center(child: CircularProgressIndicator()),
  );
}

/// 保存、删除、用途变更后让系统信息重新获取，App 里的 AI 入口随之出现或消失。
/// 先取出容器：请求期间离开本页也能刷新。
VoidCallback _availabilitySync(BuildContext context) {
  final container = ProviderScope.containerOf(context, listen: false);
  return () => container.invalidate(serverInfoProvider);
}

String _formatTime(DateTime time) {
  String two(int n) => n.toString().padLeft(2, '0');
  return '${time.year}-${two(time.month)}-${two(time.day)} '
      '${two(time.hour)}:${two(time.minute)}';
}

String _formatCount(int value) {
  final digits = value.abs().toString();
  final buffer = StringBuffer(value < 0 ? '-' : '');
  for (var i = 0; i < digits.length; i++) {
    if (i > 0 && (digits.length - i) % 3 == 0) buffer.write(',');
    buffer.write(digits[i]);
  }
  return buffer.toString();
}

/// 等刷新完成；刷新失败时页面保留旧值，这里不再报错。最多等几秒：
/// 刷新迟迟不回来也不能让按钮一直停在「保存中」。
Future<void> _reload(Future<Object?> future) async {
  try {
    await future.timeout(const Duration(seconds: 5));
  } catch (_) {}
}

String _roleList(Iterable<String> roles) =>
    roles.map((role) => '「${aiRoleLabel(role)}」').join('、');

// ───────────────────────── 空状态 ─────────────────────────

class _NoProviders extends StatelessWidget {
  const _NoProviders({required this.onAdd});

  final VoidCallback onAdd;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(top: CinemaSpacing.section),
    child: EmptyState(
      icon: Icons.auto_awesome_outlined,
      title: '先添加一个模型提供方',
      message: '配置后，AI 可以帮你找片、为待处理作品生成匹配建议。支持 DeepSeek、硅基流动、OpenAI、Anthropic 和本地 Ollama。',
      action: FilledButton.icon(
        onPressed: onAdd,
        icon: const Icon(Icons.add),
        label: const Text('添加模型提供方'),
      ),
    ),
  );
}

// ───────────────────────── 模型提供方 ─────────────────────────

/// 新增提供方：先选预设（或自定义），再填表单。
Future<void> _addProvider(BuildContext context, WidgetRef ref) async {
  final dio = ref.read(apiClientProvider);
  final sync = _availabilitySync(context);
  // 预设拿不到也不影响手动添加
  var presets = const <AiPreset>[];
  try {
    presets = await ref.read(aiPresetsProvider.future);
  } catch (_) {}
  if (!context.mounted) return;
  final picked = await showChoicePicker<String>(
    context,
    title: '选择提供方',
    subtitle: '预设只预填名称、类型和地址，之后都能改',
    current: null,
    choices: [
      for (final preset in presets)
        (
          value: preset.id,
          label: preset.name,
          subtitle: '${aiKindLabel(preset.kind)} · ${preset.baseUrl}',
        ),
      (
        value: _customPresetId,
        label: '自定义',
        subtitle: 'OpenAI 兼容或 Anthropic 接口，自己填地址',
      ),
    ],
  );
  if (picked == null || !context.mounted) return;
  final preset = presets.where((p) => p.id == picked.$1).firstOrNull;
  final created = await showDialog<bool>(
    context: context,
    builder: (_) => _ProviderDialog(
      initialName: preset?.name ?? '',
      initialKind: preset?.kind ?? 'openai',
      initialUrl: preset?.baseUrl ?? '',
      submit: (form) => createAiProvider(
        dio,
        name: form.name,
        kind: form.kind,
        baseUrl: form.baseUrl,
        apiKey: form.apiKey,
      ),
    ),
  );
  if (created != true) return;
  sync();
  ref.invalidate(aiProvidersProvider);
  if (context.mounted) showSnack(context, '已添加模型提供方');
}

class _ProvidersSection extends ConsumerStatefulWidget {
  const _ProvidersSection({required this.providers});

  final List<AiProvider> providers;

  @override
  ConsumerState<_ProvidersSection> createState() => _ProvidersSectionState();
}

class _ProvidersSectionState extends ConsumerState<_ProvidersSection> {
  /// 正在处理的提供方（测试、删除），处理中该行的菜单换成小圈
  final _busy = <int>{};

  Future<void> _guard(
    AiProvider provider,
    Future<void> Function() action,
  ) async {
    if (_busy.contains(provider.id)) return;
    setState(() => _busy.add(provider.id));
    try {
      await action();
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    }
    if (mounted) setState(() => _busy.remove(provider.id));
  }

  Future<void> _test(AiProvider provider) => _guard(provider, () async {
    final result = await testAiProvider(
      ref.read(apiClientProvider),
      provider.id,
    );
    // 最近一次测试记在提供方上，列表跟着刷新
    ref.invalidate(aiProvidersProvider);
    if (!mounted) return;
    showSnack(
      context,
      result.ok
          ? '「${provider.name}」连接成功（${result.durationMs} ms）'
          : '「${provider.name}」连接失败：${result.reason}',
    );
  });

  Future<void> _edit(AiProvider provider) async {
    final dio = ref.read(apiClientProvider);
    final sync = _availabilitySync(context);
    final saved = await showDialog<bool>(
      context: context,
      builder: (_) => _ProviderDialog(
        editing: provider,
        initialName: provider.name,
        initialKind: provider.kind,
        initialUrl: provider.baseUrl,
        submit: (form) => updateAiProvider(
          dio,
          provider.id,
          name: form.name,
          kind: form.kind,
          baseUrl: form.baseUrl,
          apiKey: form.apiKey,
          clearKey: form.clearKey,
        ),
      ),
    );
    if (saved != true) return;
    sync();
    ref.invalidate(aiProvidersProvider);
    // 地址或 Key 变了，之前的测试结果和向量索引都可能不再适用
    ref.invalidate(aiSemanticProvider);
    if (mounted) showSnack(context, '已保存');
  }

  Future<void> _delete(AiProvider provider) async {
    final confirmed = await showConfirmDialog(
      context,
      title: '删除「${provider.name}」？',
      body: '删除后将不再使用这个提供方，已保存的 Key 也会一起删除。',
      action: '删除',
      destructive: true,
    );
    if (!confirmed || !mounted) return;
    final sync = _availabilitySync(context);
    await _guard(provider, () async {
      try {
        await deleteAiProvider(ref.read(apiClientProvider), provider.id);
      } catch (error) {
        final api = ApiError.from(error);
        if (api.code != 'ai_provider_in_use') rethrow;
        if (mounted) {
          showSnack(
            context,
            '「${provider.name}」正被${_roleList(provider.usedBy)}使用，'
            '请先在「模型用途」里改用其他提供方或清除，再删除。',
          );
        }
        // 用途可能在别处改过，把列表刷新成最新的
        ref.invalidate(aiProvidersProvider);
        return;
      }
      sync();
      ref.invalidate(aiProvidersProvider);
      if (mounted) showSnack(context, '已删除「${provider.name}」');
    });
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const SectionTitle('模型提供方', inset: false),
        for (final provider in widget.providers)
          _ProviderTile(
            provider: provider,
            busy: _busy.contains(provider.id),
            onTest: () => _test(provider),
            onEdit: () => _edit(provider),
            onDelete: () => _delete(provider),
          ),
        const SizedBox(height: 8),
        Align(
          alignment: Alignment.centerLeft,
          child: FilledButton.tonalIcon(
            onPressed: () => _addProvider(context, ref),
            icon: const Icon(Icons.add),
            label: const Text('添加模型提供方'),
          ),
        ),
      ],
    );
  }
}

class _ProviderTile extends StatelessWidget {
  const _ProviderTile({
    required this.provider,
    required this.busy,
    required this.onTest,
    required this.onEdit,
    required this.onDelete,
  });

  final AiProvider provider;
  final bool busy;
  final VoidCallback onTest;
  final VoidCallback onEdit;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final secondary = text.bodySmall?.copyWith(color: CinemaColors.text2);
    final used = provider.usedBy.isEmpty
        ? '未被使用'
        : '用于${_roleList(provider.usedBy)}';
    // 行本身不可点，操作都在行尾菜单里，聚焦环描在菜单按钮上
    return ListTile(
      contentPadding: const EdgeInsets.only(left: 0, right: 0),
      leading: const Icon(Icons.hub_outlined),
      title: Text(provider.name, maxLines: 1, overflow: TextOverflow.ellipsis),
      subtitle: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '${aiKindLabel(provider.kind)} · ${provider.baseUrl}',
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
          ),
          Text(
            '${provider.keySet ? 'Key 已设置' : 'Key 未设置'} · $used',
            style: secondary,
          ),
          const SizedBox(height: 2),
          _LastTest(test: provider.lastTest),
        ],
      ),
      trailing: busy
          ? const BusyIcon()
          : FocusRing(
              child: PopupMenuButton<VoidCallback>(
                tooltip: '更多操作',
                onSelected: (action) => action(),
                itemBuilder: (_) => [
                  PopupMenuItem(value: onTest, child: const Text('测试连接')),
                  PopupMenuItem(value: onEdit, child: const Text('编辑')),
                  PopupMenuItem(value: onDelete, child: const Text('删除')),
                ],
              ),
            ),
    );
  }
}

/// 最近一次测试连接的结果和时间。
class _LastTest extends StatelessWidget {
  const _LastTest({required this.test});

  final AiLastTest? test;

  @override
  Widget build(BuildContext context) {
    final style = Theme.of(context).textTheme.bodySmall;
    final test = this.test;
    if (test == null) {
      return Text('还没有测试过', style: style?.copyWith(color: CinemaColors.text2));
    }
    final color = test.ok ? CinemaColors.text2 : CinemaColors.error;
    final detail = test.message;
    final label = test.ok
        ? '最近测试：连接成功'
        : '最近测试：失败${detail == null || detail.isEmpty ? '' : '，$detail'}';
    return Semantics(
      liveRegion: true,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 1),
            child: Icon(
              test.ok ? Icons.check_circle_outline : Icons.error_outline,
              size: 14,
              color: color,
            ),
          ),
          const SizedBox(width: 4),
          Expanded(
            child: Text(
              '$label（${_formatTime(test.checkedAt)}）',
              style: style?.copyWith(color: color),
            ),
          ),
        ],
      ),
    );
  }
}

/// 提供方表单的内容。Key 没填（[apiKey] 为空）表示保持不变。
class _ProviderForm {
  const _ProviderForm({
    required this.name,
    required this.kind,
    required this.baseUrl,
    this.apiKey,
    this.clearKey = false,
  });

  final String name;
  final String kind;
  final String baseUrl;
  final String? apiKey;
  final bool clearKey;
}

/// 新增或编辑提供方。提交在对话框里进行，出错时留在对话框改。
class _ProviderDialog extends StatefulWidget {
  const _ProviderDialog({
    required this.initialName,
    required this.initialKind,
    required this.initialUrl,
    required this.submit,
    this.editing,
  });

  final String initialName;
  final String initialKind;
  final String initialUrl;
  final AiProvider? editing;
  final Future<void> Function(_ProviderForm form) submit;

  @override
  State<_ProviderDialog> createState() => _ProviderDialogState();
}

class _ProviderDialogState extends State<_ProviderDialog> {
  // 由对话框自己释放：关闭动画期间输入框还在用
  late final _name = TextEditingController(text: widget.initialName);
  late final _url = TextEditingController(text: widget.initialUrl);
  final _key = TextEditingController();
  late String _kind = widget.initialKind;
  bool _clearKey = false;
  bool _saving = false;
  String? _nameError;
  String? _urlError;
  String? _error;

  @override
  void dispose() {
    _name.dispose();
    _url.dispose();
    _key.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (_saving) return;
    final name = _name.text.trim();
    final url = _url.text.trim();
    final uri = Uri.tryParse(url);
    final validUrl =
        uri != null &&
        (uri.scheme == 'http' || uri.scheme == 'https') &&
        uri.host.isNotEmpty;
    setState(() {
      _nameError = name.isEmpty ? '请填写名称' : null;
      _urlError = validUrl ? null : '请填写 http:// 或 https:// 开头的地址';
      _error = null;
    });
    if (name.isEmpty || !validUrl) return;
    setState(() => _saving = true);
    try {
      await widget.submit(
        _ProviderForm(
          name: name,
          kind: _kind,
          baseUrl: url,
          apiKey: _key.text.trim(),
          clearKey: _clearKey,
        ),
      );
      if (mounted) Navigator.pop(context, true);
    } catch (error) {
      if (mounted) {
        setState(() {
          _saving = false;
          _error = ApiError.from(error).message;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final editing = widget.editing;
    final keySet = editing?.keySet ?? false;
    return AlertDialog(
      // 输入法弹出时按钮不被挡住
      scrollable: true,
      title: Text(editing == null ? '添加模型提供方' : '编辑模型提供方'),
      content: SizedBox(
        width: 420,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            TextField(
              key: const ValueKey('provider-name'),
              controller: _name,
              autofocus: editing == null,
              enabled: !_saving,
              maxLength: _maxNameLength,
              decoration: InputDecoration(
                labelText: '名称',
                errorText: _nameError,
                counterText: '',
              ),
            ),
            const SizedBox(height: 12),
            AppDropdownField<String>(
              key: ValueKey('provider-kind-$_kind'),
              label: '类型',
              value: _kind,
              items: [
                for (final kind in const ['openai', 'anthropic'])
                  DropdownMenuItem(value: kind, child: Text(aiKindLabel(kind))),
              ],
              onChanged: _saving
                  ? null
                  : (kind) => setState(() => _kind = kind ?? _kind),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const ValueKey('provider-url'),
              controller: _url,
              enabled: !_saving,
              autocorrect: false,
              keyboardType: TextInputType.url,
              maxLength: _maxUrlLength,
              decoration: InputDecoration(
                labelText: '地址',
                hintText: 'https://api.example.com/v1',
                errorText: _urlError,
                counterText: '',
              ),
            ),
            const SizedBox(height: 12),
            TextField(
              key: const ValueKey('provider-key'),
              controller: _key,
              enabled: !_saving && !_clearKey,
              obscureText: true,
              autocorrect: false,
              enableSuggestions: false,
              maxLength: _maxKeyLength,
              decoration: InputDecoration(
                labelText: 'API Key',
                hintText: keySet ? '已设置，留空保持不变' : '本地 Ollama 等可不填',
                counterText: '',
              ),
              onSubmitted: (_) => _save(),
            ),
            if (keySet)
              FocusRing(
                child: CheckboxListTile(
                  key: const ValueKey('provider-clear-key'),
                  contentPadding: EdgeInsets.zero,
                  dense: true,
                  value: _clearKey,
                  onChanged: _saving
                      ? null
                      : (checked) => setState(() {
                          _clearKey = checked == true;
                          if (_clearKey) _key.clear();
                        }),
                  title: const Text('清除已保存的 Key'),
                ),
              ),
            if (_error case final error?)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: FormErrorText(error),
              ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: _saving ? null : () => Navigator.pop(context, false),
          child: const Text('取消'),
        ),
        FilledButton(
          onPressed: _saving ? null : _save,
          child: Text(_saving ? '正在保存…' : '保存'),
        ),
      ],
    );
  }
}

// ───────────────────────── 模型用途 ─────────────────────────

/// 用途区需要预设（模型名预填），拿不到时不预填。
class _RolesPart extends ConsumerWidget {
  const _RolesPart({required this.providers});

  final List<AiProvider> providers;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final roles = ref.watch(aiRolesProvider);
    final presets = ref.watch(aiPresetsProvider).asData?.value ?? const [];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const SectionTitle('模型用途', inset: false),
        _Part<AiRoles>(
          value: roles,
          onRetry: () => ref.invalidate(aiRolesProvider),
          builder: (roles) => _RolesSection(
            // 服务端的用途变了才重建，没保存的输入不受别的刷新影响
            key: ValueKey([for (final role in aiRoles) roles.of(role)]),
            providers: providers,
            presets: presets,
            roles: roles,
          ),
        ),
      ],
    );
  }
}

class _RolesSection extends ConsumerStatefulWidget {
  const _RolesSection({
    super.key,
    required this.providers,
    required this.presets,
    required this.roles,
  });

  final List<AiProvider> providers;
  final List<AiPreset> presets;
  final AiRoles roles;

  @override
  ConsumerState<_RolesSection> createState() => _RolesSectionState();
}

class _RolesSectionState extends ConsumerState<_RolesSection> {
  final _provider = <String, int?>{};
  final _model = <String, TextEditingController>{};

  /// 上一次由预设预填的模型名：用户没改过它时，换提供方可以跟着换
  final _prefilled = <String, String>{};
  final _testing = <String>{};
  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    for (final role in aiRoles) {
      final binding = widget.roles.of(role);
      _provider[role] = _available(role).any((p) => p.id == binding?.providerId)
          ? binding?.providerId
          : null;
      _model[role] = TextEditingController(text: binding?.model ?? '')
        ..addListener(_changed);
    }
  }

  @override
  void dispose() {
    for (final controller in _model.values) {
      controller.dispose();
    }
    super.dispose();
  }

  void _changed() {
    if (mounted) setState(() {});
  }

  /// 向量只能用 OpenAI 兼容的提供方，服务端也会校验
  List<AiProvider> _available(String role) => [
    for (final p in widget.providers)
      if (role != 'embedding' || p.kind == 'openai') p,
  ];

  bool _dirty(String role) {
    final saved = widget.roles.of(role);
    final model = _model[role]!.text.trim();
    if (saved == null) return _provider[role] != null || model.isNotEmpty;
    return _provider[role] != saved.providerId || model != saved.model;
  }

  String _normalized(String url) =>
      url.trim().replaceAll(RegExp(r'/+$'), '').toLowerCase();

  void _pick(String role, int? id) {
    setState(() {
      _provider[role] = id;
      final provider = widget.providers.where((p) => p.id == id).firstOrNull;
      final preset = provider == null
          ? null
          : widget.presets
                .where(
                  (p) =>
                      p.kind == provider.kind &&
                      _normalized(p.baseUrl) == _normalized(provider.baseUrl),
                )
                .firstOrNull;
      final suggestion = preset?.modelFor(role) ?? '';
      final controller = _model[role]!;
      final untouched =
          controller.text.trim().isEmpty ||
          controller.text.trim() == _prefilled[role];
      if (suggestion.isNotEmpty && untouched) {
        controller.text = suggestion;
        _prefilled[role] = suggestion;
      }
    });
  }

  void _clear(String role) {
    setState(() {
      _provider[role] = null;
      _model[role]!.clear();
      _prefilled.remove(role);
    });
  }

  Future<void> _test(String role) async {
    if (_testing.contains(role)) return;
    setState(() => _testing.add(role));
    final label = aiRoleLabel(role);
    try {
      final result = await testAiRole(ref.read(apiClientProvider), role);
      // 测试同样会更新提供方上的最近结果
      ref.invalidate(aiProvidersProvider);
      if (mounted) {
        showSnack(
          context,
          result.ok
              ? '「$label」的模型可用（${result.durationMs} ms）'
              : '「$label」的模型不可用：${result.reason}',
        );
      }
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    }
    if (mounted) setState(() => _testing.remove(role));
  }

  Future<void> _save() async {
    if (_saving) return;
    final changes = <String, AiRoleBinding?>{};
    for (final role in aiRoles) {
      if (!_dirty(role)) continue;
      final id = _provider[role];
      final model = _model[role]!.text.trim();
      final label = aiRoleLabel(role);
      if (id == null && model.isEmpty) {
        changes[role] = null;
      } else if (id == null) {
        setState(() => _error = '请为「$label」选择提供方，或者清除模型名');
        return;
      } else if (model.isEmpty) {
        setState(() => _error = '请填写「$label」的模型名');
        return;
      } else {
        changes[role] = AiRoleBinding(providerId: id, model: model);
      }
    }
    if (changes.isEmpty) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    final sync = _availabilitySync(context);
    try {
      await saveAiRoles(ref.read(apiClientProvider), changes);
      sync();
      ref
        ..invalidate(aiRolesProvider)
        ..invalidate(aiProvidersProvider)
        ..invalidate(aiSemanticProvider);
      if (mounted) showSnack(context, '已保存模型用途');
      // 等新值回来再恢复按钮，避免保存按钮在刷新的空档里又亮起来
      await _reload(ref.read(aiRolesProvider.future));
    } catch (error) {
      if (mounted) setState(() => _error = ApiError.from(error).message);
    }
    if (mounted) setState(() => _saving = false);
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final anyDirty = aiRoles.any(_dirty);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          '每种用途选一个提供方，模型名手动填写。改动后点「保存」才生效。',
          style: text.bodyMedium?.copyWith(color: CinemaColors.text2),
        ),
        const SizedBox(height: CinemaSpacing.heading),
        for (final role in aiRoles) ...[
          _roleRow(context, role),
          const SizedBox(height: 16),
        ],
        if (_error case final error?) ...[
          FormErrorText(error),
          const SizedBox(height: 8),
        ],
        Align(
          alignment: Alignment.centerLeft,
          child: FilledButton.tonal(
            onPressed: _saving || !anyDirty ? null : _save,
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

  Widget _roleRow(BuildContext context, String role) {
    final text = Theme.of(context).textTheme;
    final label = aiRoleLabel(role);
    final dirty = _dirty(role);
    final saved = widget.roles.of(role);
    final items = _available(role);
    final selected = items.any((p) => p.id == _provider[role])
        ? _provider[role]
        : null;
    // 测试用的是已保存的配置，有未保存的改动时先保存
    final canTest = saved != null && !dirty && !_testing.contains(role);
    final canClear = selected != null || _model[role]!.text.isNotEmpty;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(label, style: text.titleSmall),
        const SizedBox(height: 8),
        AppDropdownField<int>(
          key: ValueKey('role-provider-$role-$selected'),
          label: '提供方',
          value: selected,
          items: [
            for (final p in items)
              DropdownMenuItem(
                value: p.id,
                child: Text(p.name, overflow: TextOverflow.ellipsis),
              ),
          ],
          helperText: role == 'embedding' ? '只能选 OpenAI 兼容的提供方' : null,
          onChanged: _saving ? null : (id) => _pick(role, id),
        ),
        const SizedBox(height: 12),
        TextField(
          key: ValueKey('role-model-$role'),
          controller: _model[role],
          enabled: !_saving,
          autocorrect: false,
          enableSuggestions: false,
          maxLength: _maxModelLength,
          decoration: const InputDecoration(labelText: '模型名', counterText: ''),
        ),
        const SizedBox(height: 4),
        Row(
          children: [
            Expanded(
              child: Text(
                aiRoleHint(role),
                style: text.bodySmall?.copyWith(color: CinemaColors.text2),
              ),
            ),
            if (_testing.contains(role))
              const BusyIcon()
            else
              TextButton(
                key: ValueKey('role-test-$role'),
                onPressed: canTest ? () => _test(role) : null,
                child: const Text('测试'),
              ),
            TextButton(
              key: ValueKey('role-clear-$role'),
              onPressed: canClear && !_saving ? () => _clear(role) : null,
              child: const Text('清除'),
            ),
          ],
        ),
      ],
    );
  }
}

// ───────────────────────── 匹配建议 ─────────────────────────

class _SettingsPart extends ConsumerWidget {
  const _SettingsPart();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final settings = ref.watch(aiSettingsProvider);
    final roles = ref.watch(aiRolesProvider);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const SectionTitle('匹配建议', inset: false),
        _Part<AiSettings>(
          value: settings,
          onRetry: () => ref.invalidate(aiSettingsProvider),
          builder: (settings) => _MatchSection(
            key: ValueKey((
              settings.suggestMatches,
              settings.autoMatch,
              settings.autoMatchThreshold,
            )),
            settings: settings,
            // 用途还没加载出来之前按已配置处理，避免开关闪一下禁用
            taskConfigured: roles.hasValue
                ? roles.requireValue.task != null
                : true,
          ),
        ),
      ],
    );
  }
}

class _MatchSection extends ConsumerStatefulWidget {
  const _MatchSection({
    super.key,
    required this.settings,
    required this.taskConfigured,
  });

  final AiSettings settings;
  final bool taskConfigured;

  @override
  ConsumerState<_MatchSection> createState() => _MatchSectionState();
}

class _MatchSectionState extends ConsumerState<_MatchSection> {
  static const _minThreshold = 0.5;

  late double _threshold = widget.settings.autoMatchThreshold.clamp(
    _minThreshold,
    1.0,
  );
  bool _saving = false;

  /// 界面显示的值：保存成功后直接取服务端回的结果，不等重新获取，
  /// 开关不会卡在「保存中」，也不会先跳回旧值。
  late AiSettings _settings = widget.settings;

  @override
  void didUpdateWidget(_MatchSection oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.settings != oldWidget.settings) {
      _settings = widget.settings;
      _threshold = widget.settings.autoMatchThreshold.clamp(_minThreshold, 1.0);
    }
  }

  Future<void> _save({
    bool? suggestMatches,
    bool? autoMatch,
    double? threshold,
  }) async {
    if (_saving) return;
    setState(() => _saving = true);
    final sync = _availabilitySync(context);
    try {
      final saved = await saveAiSettings(
        ref.read(apiClientProvider),
        suggestMatches: suggestMatches,
        autoMatch: autoMatch,
        autoMatchThreshold: threshold,
      );
      sync();
      ref.invalidate(aiSettingsProvider);
      if (mounted) {
        setState(() {
          _settings = saved;
          _threshold = saved.autoMatchThreshold.clamp(_minThreshold, 1.0);
        });
        showSnack(context, '已保存');
      }
    } catch (error) {
      if (mounted) {
        showApiErrorSnack(context, error);
        // 保存失败：滑块和开关回到服务端的值
        setState(
          () => _threshold = _settings.autoMatchThreshold.clamp(
            _minThreshold,
            1.0,
          ),
        );
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final settings = _settings;
    final enabled = widget.taskConfigured && !_saving;
    final autoEnabled = enabled && settings.autoMatch;
    final percent = '${(_threshold * 100).round()}%';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          '同步后，AI 可以为没有匹配上的作品给出匹配建议，在「待处理」里采用。',
          style: text.bodyMedium?.copyWith(color: CinemaColors.text2),
        ),
        const SizedBox(height: CinemaSpacing.heading),
        if (!widget.taskConfigured) ...[
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Icon(
                Icons.info_outline,
                size: 18,
                color: CinemaColors.amber,
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  '还没有配置「后台任务」模型，配置后才能使用匹配建议。',
                  style: text.bodyMedium,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
        ],
        FocusRing(
          child: SwitchListTile(
            key: const ValueKey('suggest-matches'),
            contentPadding: EdgeInsets.zero,
            value: settings.suggestMatches,
            onChanged: enabled ? (value) => _save(suggestMatches: value) : null,
            title: const Text('同步后自动分析未匹配的作品'),
          ),
        ),
        FocusRing(
          child: SwitchListTile(
            key: const ValueKey('auto-match'),
            contentPadding: EdgeInsets.zero,
            value: settings.autoMatch,
            onChanged: enabled ? (value) => _save(autoMatch: value) : null,
            title: const Text('AI 自动匹配'),
            subtitle: const Text(
              '达到置信度阈值，并通过年份、季集数、片名校验的建议才会自动写入，'
              '写入后可在条目页撤销。默认关闭。',
            ),
          ),
        ),
        Row(
          children: [
            Text(
              '置信度阈值',
              style: text.bodyMedium?.copyWith(
                color: autoEnabled ? null : CinemaColors.text2,
              ),
            ),
            const Spacer(),
            Text(
              percent,
              key: const ValueKey('threshold-percent'),
              style: text.titleSmall,
            ),
          ],
        ),
        Slider(
          key: const ValueKey('threshold-slider'),
          value: _threshold,
          min: _minThreshold,
          max: 1,
          divisions: 10,
          label: percent,
          onChanged: autoEnabled
              ? (value) => setState(() => _threshold = value)
              : null,
          onChangeEnd: autoEnabled
              ? (value) =>
                    _save(threshold: double.parse(value.toStringAsFixed(2)))
              : null,
        ),
        Text(
          '范围 50% 到 100%，默认 90%。阈值越高，自动写入的越少、越稳妥。',
          style: text.bodySmall?.copyWith(color: CinemaColors.text2),
        ),
      ],
    );
  }
}

// ───────────────────────── 语义索引 ─────────────────────────

class _SemanticSection extends ConsumerStatefulWidget {
  const _SemanticSection();

  @override
  ConsumerState<_SemanticSection> createState() => _SemanticSectionState();
}

class _SemanticSectionState extends ConsumerState<_SemanticSection> {
  Timer? _poll;
  bool _rebuilding = false;

  @override
  void initState() {
    super.initState();
    // 只在建立索引时刷新，建好了就停下，不一直请求
    _poll = Timer.periodic(const Duration(seconds: 5), (_) {
      if (ref.read(aiSemanticProvider).value?.building == true) {
        ref.invalidate(aiSemanticProvider);
      }
    });
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  Future<void> _rebuild() async {
    if (_rebuilding) return;
    final confirmed = await showConfirmDialog(
      context,
      title: '重建语义索引？',
      body: '会清空现有索引并由后台任务重新生成，生成期间会调用向量模型，条目多时需要一些时间。',
      action: '重建',
      destructive: true,
    );
    if (!confirmed || !mounted) return;
    setState(() => _rebuilding = true);
    final sync = _availabilitySync(context);
    try {
      await rebuildAiSemantic(ref.read(apiClientProvider));
      sync();
      ref.invalidate(aiSemanticProvider);
      if (mounted) showSnack(context, '已开始重建索引');
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    }
    if (mounted) setState(() => _rebuilding = false);
  }

  @override
  Widget build(BuildContext context) {
    final status = ref.watch(aiSemanticProvider);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const SectionTitle('语义索引', inset: false),
        _Part<AiSemanticStatus>(
          value: status,
          onRetry: () => ref.invalidate(aiSemanticProvider),
          builder: (status) => _semantic(context, status),
        ),
      ],
    );
  }

  Widget _semantic(BuildContext context, AiSemanticStatus status) {
    final text = Theme.of(context).textTheme;
    final failed = status.state == 'error';
    final line = switch (status.state) {
      'off' => '未启用。在「模型用途」里配置「向量」模型后才会建立索引，不配置也能按条件筛选。',
      'unavailable' => 'sqlite-vec 不可用，只能按条件筛选。',
      'building' => '正在建立索引 ${status.indexed}/${status.total}',
      'ready' =>
        status.indexed >= status.total && status.total > 0
            ? '已是最新（${status.indexed} 个条目）'
            : '已是最新',
      'error' => '向量模型调用失败：${status.error ?? '原因未知'}',
      _ => status.state,
    };
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Semantics(
          liveRegion: true,
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(
                failed ? Icons.error_outline : Icons.manage_search_outlined,
                size: 18,
                color: failed ? CinemaColors.error : CinemaColors.text2,
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  line,
                  key: const ValueKey('semantic-state'),
                  style: text.bodyMedium?.copyWith(
                    color: failed ? CinemaColors.error : null,
                  ),
                ),
              ),
            ],
          ),
        ),
        if (status.building) ...[
          const SizedBox(height: 8),
          LinearProgressIndicator(
            value: status.total == 0 ? null : status.indexed / status.total,
          ),
        ],
        if (status.model case final model? when status.state != 'off') ...[
          const SizedBox(height: 4),
          Text(
            '向量模型：$model',
            style: text.bodySmall?.copyWith(color: CinemaColors.text2),
          ),
        ],
        const SizedBox(height: 12),
        Align(
          alignment: Alignment.centerLeft,
          child: OutlinedButton.icon(
            onPressed: _rebuilding || !status.canRebuild ? null : _rebuild,
            icon: _rebuilding
                ? const SizedBox.square(
                    dimension: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.restart_alt),
            label: const Text('重建索引'),
          ),
        ),
      ],
    );
  }
}

// ───────────────────────── 用量 ─────────────────────────

class _UsagePart extends ConsumerWidget {
  const _UsagePart();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final text = Theme.of(context).textTheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const SectionTitle('用量', inset: false),
        Text(
          '最近 $aiUsageMonths 个月各用途的调用情况。仅记录，不做限额。',
          style: text.bodyMedium?.copyWith(color: CinemaColors.text2),
        ),
        const SizedBox(height: CinemaSpacing.heading),
        _Part<List<AiUsageRow>>(
          value: ref.watch(aiUsageProvider),
          onRetry: () => ref.invalidate(aiUsageProvider),
          builder: (rows) => rows.isEmpty
              ? Text(
                  '还没有调用记录。',
                  style: text.bodyMedium?.copyWith(color: CinemaColors.text2),
                )
              : _UsageTable(rows: rows),
        ),
      ],
    );
  }
}

class _UsageTable extends StatelessWidget {
  const _UsageTable({required this.rows});

  final List<AiUsageRow> rows;

  /// 2026-11 -> 2026 年 11 月
  static String _month(String month) {
    final parts = month.split('-');
    final number = parts.length == 2 ? int.tryParse(parts[1]) : null;
    return number == null ? month : '${parts[0]} 年 $number 月';
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final months = {for (final row in rows) row.month}.toList()
      ..sort((a, b) => b.compareTo(a));
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final month in months) ...[
          Padding(
            padding: const EdgeInsets.only(top: 8, bottom: 4),
            child: Semantics(
              header: true,
              child: Text(_month(month), style: text.titleSmall),
            ),
          ),
          for (final role in aiRoles)
            for (final row in rows.where(
              (r) => r.month == month && r.role == role,
            ))
              ListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                title: Text(aiRoleLabel(row.role)),
                subtitle: Text(
                  '${_formatCount(row.calls)} 次调用，'
                  '失败 ${_formatCount(row.failures)} 次'
                  ' · 输入 ${_formatCount(row.inputTokens)} / '
                  '输出 ${_formatCount(row.outputTokens)} token',
                ),
              ),
        ],
      ],
    );
  }
}
