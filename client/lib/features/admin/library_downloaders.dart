import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/snack.dart';
import 'library_downloaders_api.dart';

/// 下载器列表的三种角色，也是 PUT 请求体里的键。
enum DownloaderRole {
  metadata('metadata', '元数据下载器'),
  images('images', '图片获取器'),
  subtitles('subtitles', '字幕下载器');

  const DownloaderRole(this.key, this.label);

  final String key;
  final String label;
}

/// Emby 式的下载器列表：每行可以勾选，拖动柄或上移/下移改变顺序。
/// 每次改动只把当前这一个列表发给服务端；保存期间这一组控件全部禁用，
/// 失败时回滚到改动前并显示服务端给出的原因。
class LibraryDownloaderList extends ConsumerStatefulWidget {
  const LibraryDownloaderList({
    super.key,
    required this.libraryId,
    required this.role,
    required this.items,
    this.onSaved,
  });

  final int libraryId;
  final DownloaderRole role;
  final List<LibraryDownloader> items;

  /// 保存成功后通知页面重新拉取，三个列表和字幕选项才能保持一致。
  final Future<void> Function()? onSaved;

  @override
  ConsumerState<LibraryDownloaderList> createState() =>
      _LibraryDownloaderListState();
}

class _LibraryDownloaderListState extends ConsumerState<LibraryDownloaderList> {
  late List<LibraryDownloader> _items = [...widget.items];
  bool _saving = false;

  @override
  void didUpdateWidget(covariant LibraryDownloaderList oldWidget) {
    super.didUpdateWidget(oldWidget);
    // 页面刷新回来时采用服务端顺序；保存过程中先保留本地的新值
    if (!_saving && !_sameItems(widget.items, _items)) {
      _items = [...widget.items];
    }
  }

  Future<void> _save(List<LibraryDownloader> next) async {
    if (_saving) return;
    final previous = _items;
    setState(() {
      _saving = true;
      _items = next;
    });
    try {
      final result = await saveLibraryDownloaders(
        ref.read(apiClientProvider),
        widget.libraryId,
        metadata: widget.role == DownloaderRole.metadata ? next : null,
        images: widget.role == DownloaderRole.images ? next : null,
        subtitles: widget.role == DownloaderRole.subtitles ? next : null,
      );
      if (!mounted) return;
      setState(() => _items = _itemsFor(result));
      await widget.onSaved?.call();
      if (mounted) showSnack(context, '已保存「${widget.role.label}」');
    } catch (error) {
      if (mounted) {
        setState(() => _items = previous);
        showApiErrorSnack(context, error);
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  List<LibraryDownloader> _itemsFor(LibraryDownloaders value) =>
      switch (widget.role) {
        DownloaderRole.metadata => value.metadata,
        DownloaderRole.images => value.images,
        DownloaderRole.subtitles => value.subtitles,
      };

  void _toggle(LibraryDownloader item, bool enabled) {
    _save([
      for (final row in _items)
        row.id == item.id ? row.copyWith(enabled: enabled) : row,
    ]);
  }

  void _move(int from, int to) {
    if (from == to || to < 0 || to >= _items.length) return;
    final next = [..._items];
    final item = next.removeAt(from);
    next.insert(to, item);
    _save(next);
  }

  /// 行副标题：插件状态、不可用原因，以及元数据主插件不能补缺的提示。
  /// 主插件是第一个勾选的条目，停用的行排在前面也不影响。
  String? _subtitle(LibraryDownloader item, int index) {
    final primary = _items.indexWhere((row) => row.enabled);
    final parts = [
      if (!item.pluginEnabled) '插件已停用',
      if (item.unavailableReason case final reason? when reason.isNotEmpty)
        reason,
      if (widget.role == DownloaderRole.metadata &&
          index == primary &&
          !item.canMatch)
        '只能补缺，不能放在第一位',
    ];
    return parts.isEmpty ? null : parts.join(' · ');
  }

  @override
  Widget build(BuildContext context) {
    return ReorderableListView.builder(
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      buildDefaultDragHandles: false,
      itemCount: _items.length,
      onReorderItem: _saving ? (_, _) {} : _move,
      itemBuilder: (context, index) {
        final item = _items[index];
        final subtitle = _subtitle(item, index);
        return FocusRing(
          key: ValueKey(item.id),
          child: CheckboxListTile(
            controlAffinity: ListTileControlAffinity.leading,
            value: item.enabled,
            onChanged: _saving
                ? null
                : (checked) => _toggle(item, checked ?? false),
            title: Text(item.name),
            subtitle: subtitle == null ? null : Text(subtitle),
            secondary: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                IconButton(
                  tooltip: '上移 ${item.name}',
                  onPressed: _saving || index == 0
                      ? null
                      : () => _move(index, index - 1),
                  icon: const Icon(Icons.keyboard_arrow_up),
                ),
                IconButton(
                  tooltip: '下移 ${item.name}',
                  onPressed: _saving || index == _items.length - 1
                      ? null
                      : () => _move(index, index + 1),
                  icon: const Icon(Icons.keyboard_arrow_down),
                ),
                ReorderableDragStartListener(
                  index: index,
                  enabled: !_saving,
                  child: const Tooltip(
                    message: '拖动排序',
                    child: Padding(
                      padding: EdgeInsets.symmetric(horizontal: 12),
                      child: Icon(Icons.drag_handle),
                    ),
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
  }
}

bool _sameItems(List<LibraryDownloader> a, List<LibraryDownloader> b) {
  if (a.length != b.length) return false;
  for (var i = 0; i < a.length; i++) {
    final left = a[i], right = b[i];
    if (left.id != right.id ||
        left.name != right.name ||
        left.enabled != right.enabled ||
        left.pluginEnabled != right.pluginEnabled ||
        left.unavailableReason != right.unavailableReason ||
        left.canMatch != right.canMatch) {
      return false;
    }
  }
  return true;
}
