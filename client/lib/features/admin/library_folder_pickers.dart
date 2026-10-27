part of 'libraries_page.dart';

/// 目录列表里带上来源名，多个来源的目录同名时也能分清
String _folderLabel(LibraryFolder folder) =>
    '${folder.sourceName}:${folder.path}';

bool _sameSource(LibraryFolder folder, ResourceSourceInfo source) =>
    folder.sourceId == source.id;

/// 添加目录前先选来源；不可用的来源列出来但不能选，并说明原因。
class _SourceChooser extends StatelessWidget {
  const _SourceChooser({required this.sources});

  final List<ResourceSourceInfo> sources;

  @override
  Widget build(BuildContext context) => SimpleDialog(
    title: const Text('从哪个来源添加目录'),
    children: [
      for (final source in sources)
        FocusRing(
          child: ListTile(
            enabled: source.usable,
            leading: const Icon(Icons.storage_outlined),
            title: Text(source.name),
            subtitle: switch (source.unusableReason) {
              final String reason => Text(reason),
              _ when source.pluginName != source.name => Text(
                source.pluginName,
              ),
              _ => null,
            },
            onTap: () => Navigator.of(context).pop(source),
          ),
        ),
    ],
  );
}

/// 来源的目录选择器：通过来源插件浏览目录。来源根目录也可以选。
/// 与已添加的目录、或其他资料库在同一来源里的目录重叠时不能选；
/// 来源不允许作为资料库目录的位置由插件在浏览时拒绝。
class SourceFolderPickerDialog extends ConsumerStatefulWidget {
  const SourceFolderPickerDialog({
    super.key,
    required this.source,
    required this.taken,
    this.libraryId,
  });

  final ResourceSourceInfo source;

  /// 正在编辑的资料库里已添加的同一来源的目录路径
  final List<String> taken;

  /// 正在编辑的资料库。它自己的目录以 [taken] 为准
  final int? libraryId;

  @override
  ConsumerState<SourceFolderPickerDialog> createState() =>
      _SourceFolderPickerDialogState();
}

class _SourceFolderPickerDialogState
    extends ConsumerState<SourceFolderPickerDialog> {
  final List<DirCrumb> _crumbs = [];

  ({int sourceId, String? locator}) get _key => (
    sourceId: widget.source.id,
    locator: _crumbs.isEmpty ? null : _crumbs.last.id,
  );

  /// 不能选的原因；可以选时返回 null
  String? _blocked(String path, List<AdminLibrary> libraries) {
    for (final taken in widget.taken) {
      if (taken == path) return '已添加';
      if (_overlaps(taken, path)) return '与已添加的目录 $taken 重叠';
    }
    for (final library in libraries) {
      if (library.id == widget.libraryId) continue;
      for (final folder in library.folders) {
        if (folder.sourceId != widget.source.id) continue;
        if (folder.path == path) return '已属于资料库「${library.name}」';
        if (_overlaps(folder.path, path)) {
          return '与资料库「${library.name}」的目录 ${folder.path} 重叠';
        }
      }
    }
    return null;
  }

  void _pick(String? locator, String path) => Navigator.of(context).pop(
    LibraryFolder(
      path: path,
      sourceId: widget.source.id,
      sourceName: widget.source.name,
      pluginId: widget.source.pluginId,
      locator: locator,
    ),
  );

  @override
  Widget build(BuildContext context) {
    final listing = ref.watch(sourceListingProvider(_key));
    final libraries =
        ref.watch(adminLibrariesProvider).value ?? const <AdminLibrary>[];
    final muted = Theme.of(context).textTheme.bodyMedium
        ?.copyWith(color: CinemaColors.text2);
    return AlertDialog(
      title: Text('选择「${widget.source.name}」里的目录'),
      contentPadding: const EdgeInsets.fromLTRB(8, 16, 8, 0),
      content: SizedBox(
        width: 520,
        height: 440,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _Breadcrumb(
              crumbs: _crumbs,
              onTap: (depth) => setState(() => _crumbs.length = depth),
            ),
            const Divider(height: 1),
            Expanded(
              child: AsyncView(
                value: listing,
                onRetry: () => ref.invalidate(sourceListingProvider(_key)),
                builder: (data) {
                  final here = !data.selectable
                      ? '不能选择这个目录'
                      : _blocked(data.path, libraries);
                  return ListView(
                    children: [
                      FocusRing(
                        child: ListTile(
                          leading: const Icon(Icons.drive_folder_upload),
                          title: Text('选择当前目录（${data.path}）'),
                          subtitle: here == null ? null : Text(here),
                          enabled: here == null,
                          onTap: () => _pick(data.locator, data.path),
                        ),
                      ),
                      const Divider(height: 1),
                      if (data.entries.isEmpty)
                        Padding(
                          padding: const EdgeInsets.all(24),
                          child: Center(child: Text('这里没有子目录', style: muted)),
                        ),
                      for (final entry in data.entries)
                        _entryTile(entry, data.childPath(entry), libraries),
                    ],
                  );
                },
              ),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('取消'),
        ),
      ],
    );
  }

  Widget _entryTile(
    SourceEntry entry,
    String path,
    List<AdminLibrary> libraries,
  ) {
    final blocked = _blocked(path, libraries);
    return FocusRing(
      child: ListTile(
        leading: const Icon(Icons.folder_outlined),
        title: Text(entry.name, maxLines: 2, overflow: TextOverflow.ellipsis),
        subtitle: blocked == null ? null : Text(blocked),
        onTap: () => setState(
          () => _crumbs.add(DirCrumb(id: entry.locator, name: entry.name)),
        ),
        trailing: TextButton(
          onPressed: blocked != null ? null : () => _pick(entry.locator, path),
          child: Text('选择', semanticsLabel: '选择 ${entry.name}'),
        ),
      ),
    );
  }
}

bool _overlaps(String a, String b) =>
    a.startsWith('$b/') || b.startsWith('$a/');

class _Breadcrumb extends StatelessWidget {
  const _Breadcrumb({required this.crumbs, required this.onTap});

  final List<DirCrumb> crumbs;

  /// 回到第 depth 层，0 是根目录
  final ValueChanged<int> onTap;

  @override
  Widget build(BuildContext context) {
    final names = ['根目录', ...crumbs.map((c) => c.name)];
    // 路径短时靠左；超出宽度时 reverse 让当前目录始终可见
    return Align(
      alignment: Alignment.centerLeft,
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        reverse: true,
        padding: const EdgeInsets.symmetric(horizontal: 8),
        child: Row(
          children: [
            for (var i = 0; i < names.length; i++) ...[
              if (i > 0)
                const Icon(
                  Icons.chevron_right,
                  size: 18,
                  color: CinemaColors.text2,
                ),
              // 当前所在目录不可点，用文字粗细区分
              TextButton(
                onPressed: i == names.length - 1 ? null : () => onTap(i),
                style: TextButton.styleFrom(
                  foregroundColor: CinemaColors.text2,
                  disabledForegroundColor: CinemaColors.text0,
                ),
                child: Text(
                  names[i],
                  style: i == names.length - 1
                      ? const TextStyle(fontWeight: FontWeight.w700)
                      : null,
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}
