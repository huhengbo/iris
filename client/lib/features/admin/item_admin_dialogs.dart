part of 'item_admin_page.dart';

class _MetadataEditDialog extends StatefulWidget {
  const _MetadataEditDialog({required this.metadata});

  final MetadataField metadata;

  @override
  State<_MetadataEditDialog> createState() => _MetadataEditDialogState();
}

class _MetadataEditDialogState extends State<_MetadataEditDialog> {
  late final TextEditingController _controller;
  late bool _locked;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(text: widget.metadata.value ?? '');
    _locked = widget.metadata.locked;
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    // 输入法占去半个屏幕时（TV、横屏）标题和输入框一起滚动
    scrollable: true,
    title: Text('编辑${widget.metadata.label}'),
    content: Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        TextField(
          controller: _controller,
          autofocus: true,
          maxLines: widget.metadata.field == 'overview' ? 6 : 1,
          decoration: InputDecoration(labelText: widget.metadata.label),
        ),
        FocusRing(
          child: CheckboxListTile(
            contentPadding: EdgeInsets.zero,
            value: _locked,
            onChanged: (value) => setState(() => _locked = value ?? false),
            title: const Text('锁定此字段'),
          ),
        ),
      ],
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.of(context).pop(),
        child: const Text('取消'),
      ),
      FilledButton(
        onPressed: () =>
            Navigator.of(context)
                .pop((value: _controller.text, locked: _locked)),
        child: const Text('保存'),
      ),
    ],
  );
}

class _ArtworkDialog extends StatefulWidget {
  const _ArtworkDialog({required this.label, required this.candidates});

  final String label;
  final List<String> candidates;

  @override
  State<_ArtworkDialog> createState() => _ArtworkDialogState();
}

class _ArtworkDialogState extends State<_ArtworkDialog> {
  final _url = TextEditingController();

  @override
  void dispose() {
    _url.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text('选择${widget.label}'),
    content: SizedBox(
      width: 520,
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (widget.candidates.isEmpty)
              const Text('元数据插件没有提供候选图，可以填写公开图片 URL。')
            else ...[
              const Text('元数据插件的候选图（选中后会下载到本地缓存）'),
              for (final (i, candidate) in widget.candidates.indexed)
                FocusRing(
                  child: ListTile(
                    autofocus: i == 0,
                    contentPadding: EdgeInsets.zero,
                    leading: const Icon(Icons.image_outlined),
                    title: Text(candidate),
                    onTap: () => Navigator.pop(context, (
                      source: 'provider',
                      value: candidate,
                    )),
                  ),
                ),
            ],
            const SizedBox(height: 8),
            TextField(
              controller: _url,
              autofocus: widget.candidates.isEmpty,
              decoration: const InputDecoration(labelText: '图片 URL'),
              keyboardType: TextInputType.url,
            ),
          ],
        ),
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('取消'),
      ),
      FilledButton(
        onPressed: () {
          final value = _url.text.trim();
          if (value.isNotEmpty) {
            Navigator.pop(context, (source: 'url', value: value));
          }
        },
        child: const Text('使用 URL'),
      ),
    ],
  );
}
