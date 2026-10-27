part of 'libraries_page.dart';

/// 编辑资料库里的封面区块：预览、自动/自定义标注，上传和恢复自动。
/// 操作立即生效（不随「保存」提交），成功后刷新管理端和用户侧的资料库列表。
class _CoverSection extends ConsumerStatefulWidget {
  const _CoverSection({required this.library});

  final AdminLibrary library;

  @override
  ConsumerState<_CoverSection> createState() => _CoverSectionState();
}

class _CoverSectionState extends ConsumerState<_CoverSection> {
  late Artwork _cover = widget.library.cover;
  late bool _custom = widget.library.coverCustom;
  bool _busy = false;

  Future<void> _run(Future<LibraryCover?> Function() action) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final result = await action();
      if (result == null) {
        if (mounted) setState(() => _busy = false);
        return;
      }
      ref
        ..invalidate(adminLibrariesProvider)
        ..invalidate(librariesProvider);
      if (mounted) {
        setState(() {
          _cover = result.cover;
          _custom = result.custom;
          _busy = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() => _busy = false);
        showApiErrorSnack(context, e);
      }
    }
  }

  Future<void> _upload() => _run(() async {
    final file = await ref.read(libraryCoverPickerProvider)();
    if (file == null) return null;
    return uploadLibraryCover(
      ref.read(apiClientProvider),
      widget.library.id,
      file,
      ref.read(apiBaseUrlProvider),
    );
  });

  Future<void> _reset() => _run(
    () => resetLibraryCover(
      ref.read(apiClientProvider),
      widget.library.id,
      ref.read(apiBaseUrlProvider),
    ),
  );

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final muted = text.bodySmall?.copyWith(color: CinemaColors.text2);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Text('封面', style: text.titleSmall),
            const SizedBox(width: 8),
            Text(_custom ? '自定义' : '自动', style: muted),
          ],
        ),
        const SizedBox(height: 8),
        ClipRRect(
          borderRadius: BorderRadius.circular(8),
          child: AspectRatio(
            aspectRatio: 16 / 9,
            child: ArtImage(art: _cover, icon: Icons.video_library_outlined),
          ),
        ),
        const SizedBox(height: 6),
        Text(
          _custom ? '使用上传的图片' : '自动取最近入库条目的背景图；建议上传横版 16:9 图片，不超过 8MB',
          style: muted,
        ),
        const SizedBox(height: 4),
        Wrap(
          spacing: 8,
          children: [
            TextButton.icon(
              onPressed: _busy ? null : _upload,
              icon: const Icon(Icons.upload_outlined),
              label: const Text('上传封面'),
            ),
            TextButton.icon(
              onPressed: _busy || !_custom ? null : _reset,
              icon: const Icon(Icons.restore),
              label: const Text('恢复自动'),
            ),
          ],
        ),
      ],
    );
  }
}
