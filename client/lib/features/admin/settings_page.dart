import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/providers.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/section_title.dart';
import '../../core/widgets/snack.dart';
import 'admin_widgets.dart';
import 'catalog_api.dart';
import 'settings_api.dart';

const _maxServerName = 64;
const _maxExtrasMinMb = 10240;
const _maxGraceDays = 365;

/// 「设置 › 服务器」下的四页，每页一个表单：
/// 常规（服务器名）、远程访问（对外访问地址）、资料库（附加内容阈值、
/// 失效文件保留天数）、网络（插件网络代理）。TMDB 的 Key 和代理在插件设置页维护。
///
/// 四页共用 [adminSettingsProvider]，保存后整份刷新，别页的输入不受影响
/// （表单按服务端值重建，见各页的 `ValueKey`）。
class ServerGeneralPage extends ConsumerWidget {
  const ServerGeneralPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return _FormPage(
      title: '常规',
      builder: (settings) => _ServerNameForm(
        key: ValueKey(settings.serverName),
        settings: settings,
      ),
    );
  }
}

class RemoteAccessPage extends ConsumerWidget {
  const RemoteAccessPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return _FormPage(
      title: '远程访问',
      builder: (settings) =>
          _PublicUrlForm(key: ValueKey(settings.publicUrl), settings: settings),
    );
  }
}

class LibraryDefaultsPage extends ConsumerWidget {
  const LibraryDefaultsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return _FormPage(
      title: '资料库',
      builder: (settings) => _LibraryForm(
        key: ValueKey((settings.extrasMinMb, settings.missingGraceDays)),
        settings: settings,
      ),
    );
  }
}

class PluginProxyPage extends ConsumerWidget {
  const PluginProxyPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return _FormPage(
      title: '网络',
      builder: (settings) => _PluginProxyForm(
        key: ValueKey(settings.pluginsProxy),
        settings: settings,
      ),
    );
  }
}

/// 服务器设置页的公共外壳：标题、加载/错误状态和内容最大宽。
class _FormPage extends ConsumerWidget {
  const _FormPage({required this.title, required this.builder});

  final String title;
  final Widget Function(AdminSettings settings) builder;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Scaffold(
      appBar: AppBar(title: Text(title)),
      body: AsyncView(
        value: ref.watch(adminSettingsProvider),
        onRetry: () => ref.invalidate(adminSettingsProvider),
        builder: (settings) => AdminListWidth(
          top: 8,
          bottom: 32,
          builder: (padding) => ListView(
            padding:
                padding +
                const EdgeInsets.symmetric(horizontal: CinemaSpacing.gutter),
            children: [builder(settings)],
          ),
        ),
      ),
    );
  }
}

/// 设置页各节的保存流程：防重复提交、保存后刷新、失败时在表单里显示原因。
mixin _SaveSection<T extends ConsumerStatefulWidget> on ConsumerState<T> {
  bool saving = false;
  String? error;

  /// 保存成功时返回 true。
  Future<bool> save(Future<AdminSettings> Function() call, String done) async {
    if (saving) return false;
    setState(() {
      saving = true;
      error = null;
    });
    var saved = false;
    try {
      await call();
      saved = true;
      // 先提示再刷新：刷新后这一节会按新值重建，旧的 context 就不在了
      if (mounted) showSnack(context, done);
      ref.invalidate(adminSettingsProvider);
      ref.invalidate(pendingSummaryProvider);
    } catch (e) {
      if (mounted) setState(() => error = ApiError.from(e).message);
    }
    if (mounted) setState(() => saving = false);
    return saved;
  }
}

class _Section extends StatelessWidget {
  const _Section({
    required this.title,
    required this.description,
    required this.children,
    required this.saving,
    required this.error,
    required this.onSave,
    this.extraAction,
  });

  final String title;
  final String description;
  final List<Widget> children;
  final bool saving;
  final String? error;
  final VoidCallback onSave;
  final Widget? extraAction;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SectionTitle(title, first: true, inset: false),
        Text(
          description,
          style: text.bodyMedium?.copyWith(color: CinemaColors.text2),
        ),
        const SizedBox(height: CinemaSpacing.heading),
        ...children,
        if (error != null) ...[
          const SizedBox(height: 8),
          Semantics(
            liveRegion: true,
            child: Text(
              error!,
              style: text.bodyMedium?.copyWith(color: CinemaColors.error),
            ),
          ),
        ],
        const SizedBox(height: 12),
        Row(
          children: [
            FilledButton.tonal(
              onPressed: saving ? null : onSave,
              child: saving
                  ? const SizedBox.square(
                      dimension: 18,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Text('保存'),
            ),
            if (extraAction != null) ...[
              const SizedBox(width: 8),
              extraAction!,
            ],
          ],
        ),
      ],
    );
  }
}

/// 常规：服务器名称。对外访问地址在「远程访问」里单独改。
class _ServerNameForm extends ConsumerStatefulWidget {
  const _ServerNameForm({super.key, required this.settings});

  final AdminSettings settings;

  @override
  ConsumerState<_ServerNameForm> createState() => _ServerNameFormState();
}

class _ServerNameFormState extends ConsumerState<_ServerNameForm>
    with _SaveSection<_ServerNameForm> {
  late final _name = TextEditingController(text: widget.settings.serverName);

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  void _save() {
    final name = _name.text.trim();
    if (name.isEmpty) {
      setState(() => error = '服务器名称不能为空');
      return;
    }
    save(
      () => saveServer(
        ref.read(apiClientProvider),
        name: name,
        publicUrl: widget.settings.publicUrl ?? '',
      ),
      '已保存',
    );
  }

  @override
  Widget build(BuildContext context) {
    return _Section(
      title: '服务器名称',
      description:
          '客户端添加和切换服务器时显示这个名称。'
          '服务器 ID ${widget.settings.serverId} 用来识别同一台服务器，不随地址变化。',
      saving: saving,
      error: error,
      onSave: _save,
      children: [
        TextField(
          controller: _name,
          maxLength: _maxServerName,
          decoration: const InputDecoration(labelText: '服务器名称'),
          onSubmitted: (_) => _save(),
        ),
      ],
    );
  }
}

/// 远程访问：对外访问地址。服务器名在这一页原样带回。
class _PublicUrlForm extends ConsumerStatefulWidget {
  const _PublicUrlForm({super.key, required this.settings});

  final AdminSettings settings;

  @override
  ConsumerState<_PublicUrlForm> createState() => _PublicUrlFormState();
}

class _PublicUrlFormState extends ConsumerState<_PublicUrlForm>
    with _SaveSection<_PublicUrlForm> {
  late final _url = TextEditingController(
    text: widget.settings.publicUrl ?? '',
  );

  @override
  void dispose() {
    _url.dispose();
    super.dispose();
  }

  void _save() {
    save(
      () => saveServer(
        ref.read(apiClientProvider),
        name: widget.settings.serverName,
        publicUrl: _url.text.trim(),
      ),
      '已保存',
    );
  }

  @override
  Widget build(BuildContext context) {
    return _Section(
      title: '对外访问地址',
      description:
          '外部播放器和 WebDAV 从这个地址取片，要在外网也能访问；'
          '不填时只能用 App 的内置播放器。留空保存表示清除。',
      saving: saving,
      error: error,
      onSave: _save,
      children: [
        TextField(
          controller: _url,
          keyboardType: TextInputType.url,
          autocorrect: false,
          decoration: const InputDecoration(
            labelText: '对外访问地址',
            hintText: 'https://movie.example.com',
            helperText: '例如反向代理后的 https 地址，不要带结尾的斜杠',
          ),
          onSubmitted: (_) => _save(),
        ),
      ],
    );
  }
}

class _LibraryForm extends ConsumerStatefulWidget {
  const _LibraryForm({super.key, required this.settings});

  final AdminSettings settings;

  @override
  ConsumerState<_LibraryForm> createState() => _LibraryFormState();
}

class _LibraryFormState extends ConsumerState<_LibraryForm>
    with _SaveSection<_LibraryForm> {
  late final _extras = TextEditingController(
    text: '${widget.settings.extrasMinMb}',
  );
  late final _grace = TextEditingController(
    text: '${widget.settings.missingGraceDays}',
  );

  @override
  void dispose() {
    _extras.dispose();
    _grace.dispose();
    super.dispose();
  }

  void _save() {
    final extras = int.tryParse(_extras.text.trim());
    if (extras == null || extras > _maxExtrasMinMb) {
      setState(() => error = '最小体积请填写 0 到 $_maxExtrasMinMb 之间的整数');
      return;
    }
    final grace = int.tryParse(_grace.text.trim());
    if (grace == null || grace < 1 || grace > _maxGraceDays) {
      setState(() => error = '保留天数请填写 1 到 $_maxGraceDays 之间的整数');
      return;
    }
    save(
      () => saveLibrarySettings(
        ref.read(apiClientProvider),
        extrasMinMb: extras,
        missingGraceDays: grace,
      ),
      '已保存，下次同步时生效',
    );
  }

  @override
  Widget build(BuildContext context) {
    return _Section(
      title: '资料库默认值',
      description:
          '电影资料库里小于最小体积的视频视为预告片、花絮等附加内容，不入库；'
          '文件名或目录名标明是附加内容的也会忽略，被忽略的视频可在「待处理」里恢复。'
          '来源上消失的文件先标记失效，过了保留天数再从库里删除，连同观看记录；'
          '期间移回原处可以恢复。修改后下次同步时生效。',
      saving: saving,
      error: error,
      onSave: _save,
      children: [
        TextField(
          controller: _extras,
          keyboardType: TextInputType.number,
          inputFormatters: [FilteringTextInputFormatter.digitsOnly],
          decoration: const InputDecoration(
            labelText: '最小体积',
            suffixText: 'MB',
            helperText: '填 0 表示不按大小判断',
          ),
          onSubmitted: (_) => _save(),
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _grace,
          keyboardType: TextInputType.number,
          inputFormatters: [FilteringTextInputFormatter.digitsOnly],
          decoration: const InputDecoration(
            labelText: '失效文件保留',
            suffixText: '天',
            helperText: '只删除库里的记录，不会删除来源上的文件',
          ),
          onSubmitted: (_) => _save(),
        ),
      ],
    );
  }
}

class _PluginProxyForm extends ConsumerStatefulWidget {
  const _PluginProxyForm({super.key, required this.settings});

  final AdminSettings settings;

  @override
  ConsumerState<_PluginProxyForm> createState() => _PluginProxyFormState();
}

class _PluginProxyFormState extends ConsumerState<_PluginProxyForm>
    with _SaveSection<_PluginProxyForm> {
  late final _proxy = TextEditingController(
    text: widget.settings.pluginsProxy ?? '',
  );

  @override
  void dispose() {
    _proxy.dispose();
    super.dispose();
  }

  void _save() {
    save(
      () => savePluginsProxy(
        ref.read(apiClientProvider),
        proxy: _proxy.text.trim(),
      ),
      '已保存，插件代理已更新',
    );
  }

  void _clear() {
    save(
      () => savePluginsProxy(ref.read(apiClientProvider), proxy: ''),
      '已清除插件代理',
    );
  }

  @override
  Widget build(BuildContext context) {
    return _Section(
      title: '插件网络代理',
      description:
          '所有插件的外网请求默认走这个代理，插件可以单独覆盖。'
          '服务器访问不了外网时填写；密码以 *** 显示，不修改就原样保留。',
      saving: saving,
      error: error,
      onSave: _save,
      extraAction: (widget.settings.pluginsProxy?.isNotEmpty ?? false)
          ? TextButton(
              onPressed: saving ? null : _clear,
              child: const Text('清除代理'),
            )
          : null,
      children: [
        TextField(
          controller: _proxy,
          autocorrect: false,
          maxLength: 1024,
          keyboardType: TextInputType.url,
          decoration: const InputDecoration(
            labelText: '全局代理地址（可选）',
            hintText: 'http://127.0.0.1:7890 或 socks5://…',
            helperText: '留空表示直接连接；插件在详情页的「网络」里可以单独设置',
            helperMaxLines: 2,
            counterText: '',
          ),
          onSubmitted: (_) => _save(),
        ),
      ],
    );
  }
}
