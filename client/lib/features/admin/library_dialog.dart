part of 'libraries_page.dart';

/// 新建或编辑资料库。编辑时分页：通用 / 添加文件夹 / 高级，照搬 Plex 的 Manage Library。
/// 类型只在新建时可选；目录先选来源，再在来源的目录浏览器里挑。
/// 下载器和资料库行为开关在「高级」里，改了立即保存（Emby 做法），不随「保存」提交。
class LibraryDialog extends ConsumerStatefulWidget {
  const LibraryDialog({super.key, this.library});

  final AdminLibrary? library;

  @override
  ConsumerState<LibraryDialog> createState() => _LibraryDialogState();
}

class _LibraryDialogState extends ConsumerState<LibraryDialog>
    with SingleTickerProviderStateMixin {
  late final _name = TextEditingController(text: widget.library?.name);
  late String _kind = widget.library?.kind ?? 'movie';
  late final List<LibraryFolder> _folders = [...?widget.library?.folders];
  late String? _subtitleLanguage = widget.library?.subtitleLanguage;
  late String _metadataProvider =
      widget.library?.metadataProvider ?? defaultMetadataProvider;
  late final TabController _tabs = TabController(length: 3, vsync: this);
  bool _submitting = false;
  String? _nameError;
  String? _folderError;
  String? _error;

  /// 通用页的元数据语言。null 表示还没动过，用服务端当前值。
  String? _metadataLanguage;
  bool _languageTouched = false;

  bool get _editing => widget.library != null;
  int? get _libraryId => widget.library?.id;

  @override
  void dispose() {
    _name.dispose();
    _tabs.dispose();
    super.dispose();
  }

  /// 服务端的元数据语言；还没取到时按默认值显示，不挡其他字段。
  String _effectiveLanguage(Map<String, String>? values) => _languageTouched
      ? _metadataLanguage ?? 'zh-CN'
      : values?['metadata_language'] ?? 'zh-CN';

  Future<void> _addFolder() async {
    final List<ResourceSourceInfo> sources;
    try {
      sources = await ref.read(resourceSourcesProvider.future);
    } catch (e) {
      if (mounted) setState(() => _error = ApiError.from(e).message);
      return;
    }
    if (!mounted) return;
    final usable = [
      for (final source in sources)
        if (source.usable) source,
    ];
    if (usable.isEmpty) {
      setState(
        () => _error = sources.isEmpty
            ? '还没有资源来源，先到「资源来源」里新建'
            : '资源来源都已停用，先到「资源来源」里启用',
      );
      return;
    }
    // 只有一个可用来源时直接进目录浏览器
    final source = usable.length == 1
        ? usable.single
        : await showDialog<ResourceSourceInfo>(
            context: context,
            builder: (_) => _SourceChooser(sources: sources),
          );
    if (source == null || !mounted) return;
    final taken = [
      for (final f in _folders)
        if (_sameSource(f, source)) f.path,
    ];
    final folder = await showDialog<LibraryFolder>(
      context: context,
      builder: (_) => SourceFolderPickerDialog(
        source: source,
        taken: taken,
        libraryId: widget.library?.id,
      ),
    );
    if (folder != null && mounted) {
      setState(() {
        _folders.add(folder);
        _folderError = null;
        _error = null;
      });
    }
  }

  Future<void> _submit() async {
    if (_submitting) return;
    final name = _name.text.trim();
    setState(() {
      _nameError = name.isEmpty ? '请输入名称' : null;
      _folderError = _folders.isEmpty ? '至少添加一个目录' : null;
      _error = null;
    });
    if (_nameError != null) {
      _tabs.animateTo(0);
      return;
    }
    if (_folderError != null) {
      _tabs.animateTo(1);
      return;
    }
    final original = widget.library;
    final settings = _editing
        ? ref.read(libraryAdvancedSettingsProvider(original!.id)).value
        : null;
    final language = _effectiveLanguage(settings);
    final languageChanged =
        _editing && language != (settings?['metadata_language'] ?? 'zh-CN');
    final nameChanged = original == null || original.name != name;
    final foldersChanged =
        original == null ||
        !listEquals(
          [for (final f in _folders) f.key],
          [for (final f in original.folders) f.key],
        );
    final subtitleChanged =
        original == null || original.subtitleLanguage != _subtitleLanguage;
    final providerChanged =
        original == null || original.metadataProvider != _metadataProvider;
    if (!nameChanged &&
        !foldersChanged &&
        !subtitleChanged &&
        !providerChanged &&
        !languageChanged) {
      Navigator.of(context).pop(false);
      return;
    }
    setState(() => _submitting = true);
    final dio = ref.read(apiClientProvider);
    try {
      if (original == null) {
        await createLibrary(
          dio,
          name: name,
          kind: _kind,
          folders: _folders,
          metadataProvider: _metadataProvider,
        );
      } else {
        // 只改了元数据语言时不用再 PATCH 资料库本身
        if (nameChanged ||
            foldersChanged ||
            subtitleChanged ||
            providerChanged) {
          await updateLibrary(
            dio,
            original.id,
            name: nameChanged ? name : null,
            folders: foldersChanged ? _folders : null,
            subtitleLanguage: subtitleChanged ? _subtitleLanguage : null,
            setSubtitleLanguage: subtitleChanged,
            metadataProvider: providerChanged ? _metadataProvider : null,
          );
        }
        if (languageChanged) {
          await saveLibrarySetting(
            dio,
            original.id,
            'metadata_language',
            language,
          );
        }
      }
      if (mounted) Navigator.of(context).pop(true);
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
    final text = Theme.of(context).textTheme;
    final muted = text.bodySmall?.copyWith(color: CinemaColors.text2);
    // 对话框打开期间保持来源和高级设置，切换分页时不用再等请求
    ref.watch(resourceSourcesProvider);
    final advanced = _editing
        ? ref.watch(libraryAdvancedSettingsProvider(widget.library!.id))
        : const AsyncData<Map<String, String>>(<String, String>{});
    final height = (MediaQuery.sizeOf(context).height * .62).clamp(
      320.0,
      520.0,
    );
    return AlertDialog(
      title: Text(_editing ? '编辑资料库' : '新建资料库'),
      content: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 560),
        child: SizedBox(
          width: double.maxFinite,
          height: height,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TabBar(
                controller: _tabs,
                tabs: const [
                  Tab(text: '通用'),
                  Tab(text: '添加文件夹'),
                  Tab(text: '高级'),
                ],
              ),
              if (_error != null)
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: Semantics(
                    container: true,
                    liveRegion: true,
                    child: Text(
                      _error!,
                      style: text.bodyMedium?.copyWith(
                        color: CinemaColors.error,
                      ),
                    ),
                  ),
                ),
              const SizedBox(height: 8),
              Expanded(
                child: TabBarView(
                  controller: _tabs,
                  children: [
                    _generalTab(text, muted, advanced),
                    _foldersTab(text, muted),
                    _advancedTab(text, muted, advanced),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: _submitting
              ? null
              : () => Navigator.of(context).pop(false),
          child: const Text('取消'),
        ),
        FilledButton(
          onPressed: _submitting ? null : _submit,
          child: _submitting
              ? const SizedBox.square(
                  dimension: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : Text(_editing ? '保存' : '创建'),
        ),
      ],
    );
  }

  Widget _generalTab(
    TextTheme text,
    TextStyle? muted,
    AsyncValue<Map<String, String>> advanced,
  ) {
    final settings = advanced.value;
    return ListView(
      padding: const EdgeInsets.only(top: 16),
      children: [
        TextField(
          controller: _name,
          autofocus: !_editing,
          inputFormatters: [LengthLimitingTextInputFormatter(_maxNameLength)],
          decoration: InputDecoration(
            labelText: '名称',
            hintText: '例如：电影、动画、纪录片',
            errorText: _nameError,
          ),
          onSubmitted: (_) => _submit(),
        ),
        const SizedBox(height: 16),
        SegmentedButton<String>(
          segments: const [
            ButtonSegment(value: 'movie', label: Text('电影')),
            ButtonSegment(value: 'series', label: Text('剧集')),
          ],
          selected: {_kind},
          onSelectionChanged: _editing || _submitting
              ? null
              : (s) => setState(() => _kind = s.first),
        ),
        const SizedBox(height: 6),
        Text(
          _editing ? '类型创建后不能修改' : '剧集资料库按「剧名 / 季 / 集」识别，创建后不能修改类型',
          style: muted,
        ),
        const SizedBox(height: 20),
        AppDropdownField<String>(
          label: '语言',
          value: _effectiveLanguage(settings),
          helperText: _editing ? '元数据、简介和图片优先用这种语言' : '创建后可以在「通用」里修改',
          items: const [
            DropdownMenuItem(value: 'zh-CN', child: Text('简体中文')),
            DropdownMenuItem(value: 'zh-TW', child: Text('繁体中文')),
            DropdownMenuItem(value: 'en-US', child: Text('英语')),
            DropdownMenuItem(value: 'ja-JP', child: Text('日语')),
          ],
          onChanged: !_editing || _submitting
              ? null
              : (value) {
                  if (value == null) return;
                  setState(() {
                    _metadataLanguage = value;
                    _languageTouched = true;
                  });
                },
        ),
        if (!_editing) ...[
          const SizedBox(height: 16),
          _MetadataProviderField(
            value: _metadataProvider,
            editing: false,
            onChanged: _submitting
                ? null
                : (value) => setState(() => _metadataProvider = value),
          ),
        ],
        if (_editing) ...[
          const SizedBox(height: 16),
          _CoverSection(library: widget.library!),
        ],
      ],
    );
  }

  Widget _foldersTab(TextTheme text, TextStyle? muted) {
    return ListView(
      padding: const EdgeInsets.only(top: 16),
      children: [
        Text('一个资料库可以包含多个来源里的目录；同一个目录不能属于两个资料库。', style: muted),
        const SizedBox(height: 12),
        if (_folders.isEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Text('还没有添加目录', style: muted),
          ),
        for (final folder in _folders)
          Row(
            children: [
              const Icon(
                Icons.folder_outlined,
                size: 20,
                color: CinemaColors.text2,
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      folder.path,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                    Text(folder.sourceName, style: muted),
                  ],
                ),
              ),
              IconButton(
                tooltip: '移除 ${folder.path}',
                onPressed: _submitting
                    ? null
                    : () => setState(() {
                        _folders.remove(folder);
                        _folderError = null;
                      }),
                icon: const Icon(Icons.close),
              ),
            ],
          ),
        if (_folderError != null)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(
              _folderError!,
              style: text.bodyMedium?.copyWith(color: CinemaColors.error),
            ),
          ),
        Align(
          alignment: Alignment.centerLeft,
          child: TextButton.icon(
            onPressed: _submitting ? null : _addFolder,
            icon: const Icon(Icons.add),
            label: const Text('添加目录'),
          ),
        ),
      ],
    );
  }

  Widget _advancedTab(
    TextTheme text,
    TextStyle? muted,
    AsyncValue<Map<String, String>> advanced,
  ) {
    if (!_editing) {
      return ListView(
        padding: const EdgeInsets.only(top: 24),
        children: [Text('下载器、资料库行为和字幕下载会在创建后出现在这里。', style: muted)],
      );
    }
    return ListView(
      key: const ValueKey('library-advanced-tab'),
      padding: const EdgeInsets.only(top: 8, bottom: 24),
      children: [
        AsyncView(
          value: advanced,
          onRetry: () =>
              ref.invalidate(libraryAdvancedSettingsProvider(_libraryId!)),
          builder: (values) => _BehaviorSettings(
            libraryId: _libraryId!,
            values: values,
            onSaved: () => ref.refresh(
              libraryAdvancedSettingsProvider(_libraryId!).future,
            ),
          ),
        ),
        AsyncView(
          value: ref.watch(libraryDownloadersProvider(_libraryId!)),
          onRetry: () =>
              ref.invalidate(libraryDownloadersProvider(_libraryId!)),
          builder: (value) => _DownloadersSection(
            id: _libraryId!,
            value: value,
            onSaved: () =>
                ref.refresh(libraryDownloadersProvider(_libraryId!).future),
            subtitleLanguage: _subtitleLanguage,
            onSubtitleLanguage: _submitting
                ? null
                : (value) => setState(() => _subtitleLanguage = value),
          ),
        ),
      ],
    );
  }
}

/// 资料库用哪个元数据插件刮削。新建时可以选择；编辑时由「高级」里的下载器列表维护。
class _MetadataProviderField extends ConsumerWidget {
  const _MetadataProviderField({
    required this.value,
    required this.editing,
    required this.onChanged,
  });

  final String value;
  final bool editing;
  final ValueChanged<String>? onChanged;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final muted = Theme.of(context).textTheme.bodySmall
        ?.copyWith(color: CinemaColors.text2);
    final async = ref.watch(metadataProvidersProvider);
    final providers = async.value;
    final selected = providers?.where((p) => p.id == value).firstOrNull;
    // 编辑时下载器顺序和补缺插件在「高级」里管理，这里只显示当前主插件
    if (editing) {
      final reason = providers == null
          ? null
          : selected == null
          ? '这个插件已经卸载'
          : !selected.enabled
          ? '这个插件已停用'
          : selected.unavailableReason;
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          InputDecorator(
            decoration: const InputDecoration(labelText: '元数据插件'),
            child: Text(selected?.label ?? value),
          ),
          const SizedBox(height: 4),
          Text('在「高级」里调整', style: muted),
          if (reason != null)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text(reason, style: muted),
            ),
        ],
      );
    }
    if (providers == null) {
      return Text(
        async.hasError
            ? '元数据插件列表加载失败：${ApiError.from(async.error!).message}'
            : '正在加载元数据插件…',
        style: muted,
      );
    }
    final reason = selected == null
        ? '这个插件已经卸载'
        : !selected.enabled
        ? '这个插件已停用'
        : selected.unavailableReason;
    return AppDropdownField<String>(
      label: '元数据插件',
      value: value,
      helperText: reason ?? '同步时用它匹配作品、下载海报和简介；补缺插件和图片获取器在「高级」里调整',
      items: [
        for (final provider in providers)
          DropdownMenuItem(
            value: provider.id,
            enabled: provider.enabled || provider.id == value,
            child: Text(
              provider.enabled ? provider.label : '${provider.label} · 已停用',
              overflow: TextOverflow.ellipsis,
            ),
          ),
        if (selected == null)
          DropdownMenuItem(
            value: value,
            child: Text('$value（未安装）', overflow: TextOverflow.ellipsis),
          ),
      ],
      onChanged: onChanged == null
          ? null
          : (id) {
              if (id != null) onChanged!(id);
            },
    );
  }
}
