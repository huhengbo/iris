import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/shell.dart';
import '../../app/theme.dart';
import '../../app/tv.dart';
import '../../core/api/api_error.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/snack.dart';
import '../../core/widgets/text_prompt.dart';
import '../auth/auth_controller.dart';
import 'browse_api.dart';
import 'collections_api.dart';
import 'poster_card.dart';
import '../../core/widgets/empty_state.dart';

export 'collections_api.dart'
    show collectionDetailProvider, libraryCollectionsProvider;

class CollectionsPage extends ConsumerStatefulWidget {
  const CollectionsPage({
    super.key,
    required this.libraryId,
    this.embedded = false,
  });

  final int libraryId;

  /// 嵌在资料库页的「合集」标签里：不要自己的 AppBar，新建入口放到列表上方
  final bool embedded;

  @override
  ConsumerState<CollectionsPage> createState() => _CollectionsPageState();
}

class _CollectionsPageState extends ConsumerState<CollectionsPage> {
  bool _creating = false;

  int get _libraryId => widget.libraryId;

  @override
  Widget build(BuildContext context) {
    final collections = ref.watch(libraryCollectionsProvider(_libraryId));
    final isAdmin = ref.watch(authControllerProvider).value?.isAdmin == true;
    final gutter = CinemaSpacing.gutterFor(
      wide: MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint,
    );
    final body = AsyncView(
      value: collections,
      onRetry: () => ref.invalidate(libraryCollectionsProvider(_libraryId)),
      builder: (items) => items.isEmpty
          ? EmptyState(
              icon: Icons.collections_bookmark_outlined,
              title: '还没有合集',
              message: isAdmin
                  ? '新建合集后，再把系列或同主题的影片加进来。'
                  : '管理员可以按系列或主题把影片整理到一起。',
            )
          : ListView.separated(
              padding: const EdgeInsets.only(top: 8, bottom: 24),
              itemCount: items.length,
              separatorBuilder: (_, _) => const Divider(height: 1),
              itemBuilder: (context, index) {
                final item = items[index] as Map<String, dynamic>;
                final overview = (item['overview'] as String?)?.trim();
                return FocusRing(
                  child: ListTile(
                    // 遥控器进来落在第一个合集上；手机不抢焦点
                    autofocus: index == 0 && isTelevision(context),
                    contentPadding: EdgeInsets.symmetric(horizontal: gutter),
                    leading: const Icon(Icons.collections_bookmark_outlined),
                    title: Text(item['title'] as String? ?? '未命名合集'),
                    subtitle: overview == null || overview.isEmpty
                        ? null
                        : Text(
                            overview,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                          ),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () => context.push('/collections/${item['id']}'),
                  ),
                );
              },
            ),
    );
    if (widget.embedded) {
      return Column(
        children: [
          if (isAdmin)
            Padding(
              padding: EdgeInsets.fromLTRB(gutter - 8, 4, gutter, 0),
              child: Align(
                alignment: Alignment.centerLeft,
                child: _creating
                    ? const Padding(
                        padding: EdgeInsets.all(14),
                        child: SizedBox.square(
                          dimension: 20,
                          child: CircularProgressIndicator(strokeWidth: 2),
                        ),
                      )
                    : TextButton.icon(
                        onPressed: _create,
                        icon: const Icon(Icons.add),
                        label: const Text('新建合集'),
                      ),
              ),
            ),
          Expanded(child: body),
        ],
      );
    }
    return Scaffold(
      appBar: AppBar(
        title: const Text('合集'),
        actions: [
          if (isAdmin)
            _creating
                ? const Padding(
                    padding: EdgeInsets.all(14),
                    child: SizedBox.square(
                      dimension: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    ),
                  )
                : IconButton(
                    tooltip: '新建合集',
                    onPressed: _create,
                    icon: const Icon(Icons.add),
                  ),
        ],
      ),
      body: body,
    );
  }

  Future<void> _create() async {
    final title = await showTextPrompt(
      context,
      title: '新建合集',
      label: '名称',
      action: '创建',
    );
    if (title == null || title.isEmpty || !mounted) return;
    setState(() => _creating = true);
    try {
      await ref.read(collectionsApiProvider).create(_libraryId, title);
      ref.invalidate(libraryCollectionsProvider(_libraryId));
      if (mounted) showSnack(context, '已创建合集「$title」');
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    } finally {
      if (mounted) setState(() => _creating = false);
    }
  }
}

class CollectionDetailPage extends ConsumerStatefulWidget {
  const CollectionDetailPage({super.key, required this.collectionId});

  final int collectionId;

  @override
  ConsumerState<CollectionDetailPage> createState() =>
      _CollectionDetailPageState();
}

class _CollectionDetailPageState extends ConsumerState<CollectionDetailPage> {
  /// 管理员选中要移出合集的条目
  bool _selectionMode = false;
  final _selected = <int>{};
  bool _busy = false;

  int get _id => widget.collectionId;

  void _exitSelection() => setState(() {
    _selectionMode = false;
    _selected.clear();
  });

  @override
  Widget build(BuildContext context) {
    final value = ref.watch(collectionDetailProvider(_id));
    final detail = value.value;
    final isAdmin = ref.watch(authControllerProvider).value?.isAdmin == true;
    final gutter = CinemaSpacing.gutterFor(
      wide: MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint,
    );
    final overview = detail?.overview;
    final members = detail?.items ?? const <MediaCard>[];
    return Scaffold(
      // 批量操作栏和资料库海报墙的一致：已选数量 + 全选 + 操作 + 退出
      bottomNavigationBar: _selectionMode && isAdmin
          ? SafeArea(
              child: Material(
                color: CinemaColors.surface2,
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(16, 8, 8, 8),
                  child: Row(
                    children: [
                      Expanded(
                        child: Text(
                          '已选 ${_selected.length} 项',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      TextButton(
                        onPressed: _busy || members.isEmpty
                            ? null
                            : () => setState(() {
                                if (_selected.length == members.length) {
                                  _selected.clear();
                                } else {
                                  _selected.addAll(members.map((m) => m.id));
                                }
                              }),
                        child: Text(
                          members.isNotEmpty &&
                                  _selected.length == members.length
                              ? '取消全选'
                              : '全选',
                        ),
                      ),
                      if (_busy)
                        const Padding(
                          padding: EdgeInsets.all(12),
                          child: SizedBox.square(
                            dimension: 20,
                            child: CircularProgressIndicator(strokeWidth: 2),
                          ),
                        )
                      else
                        IconButton(
                          tooltip: '移出合集',
                          onPressed: _selected.isEmpty ? null : _remove,
                          icon: const Icon(Icons.playlist_remove),
                        ),
                      IconButton(
                        tooltip: '退出选择',
                        onPressed: _busy ? null : _exitSelection,
                        icon: const Icon(Icons.close),
                      ),
                    ],
                  ),
                ),
              ),
            )
          : null,
      body: CustomScrollView(
        slivers: [
          SliverAppBar(
            pinned: true,
            leading: BackButton(
              onPressed: () =>
                  context.canPop() ? context.pop() : context.go('/'),
            ),
            title: Text(detail?.title ?? '合集'),
            actions: [
              if (isAdmin && members.isNotEmpty)
                IconButton(
                  tooltip: _selectionMode ? '退出选择' : '选择条目',
                  onPressed: _busy
                      ? null
                      : _selectionMode
                      ? _exitSelection
                      : () => setState(() => _selectionMode = true),
                  icon: Icon(_selectionMode ? Icons.close : Icons.checklist),
                ),
            ],
          ),
          if (overview != null && overview.isNotEmpty)
            SliverToBoxAdapter(
              child: Padding(
                padding: EdgeInsets.fromLTRB(gutter, 4, gutter, 8),
                child: Text(
                  overview,
                  style: Theme.of(context).textTheme.bodyMedium,
                ),
              ),
            ),
          PagedPosterGrid(
            value: value.whenData(
              (detail) =>
                  CardList(items: detail.items, total: detail.items.length),
            ),
            heroPrefix: 'collection:$_id',
            padding: EdgeInsets.fromLTRB(gutter, 8, gutter, 24),
            subtitle: (media) => media.year?.toString(),
            onRetry: () => ref.invalidate(collectionDetailProvider(_id)),
            onLoadMore: () {},
            onRetryMore: () {},
            selectionMode: _selectionMode,
            selectedIds: _selected,
            onSelected: (id) => setState(() {
              if (_busy) return;
              if (!_selected.add(id)) _selected.remove(id);
            }),
            empty: EmptyState(
              icon: Icons.collections_bookmark_outlined,
              title: '合集还是空的',
              message: isAdmin ? '在影片详情页的「更多操作」里选「加入合集」。' : '管理员整理后，影片会出现在这里。',
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _remove() async {
    final count = _selected.length;
    final confirmed = await showConfirmDialog(
      context,
      title: '把 $count 项移出合集？',
      body: '只是从合集里拿掉，影片仍在资料库里。',
      action: '移出',
      destructive: true,
    );
    if (!confirmed || !mounted) return;
    final api = ref.read(collectionsApiProvider);
    setState(() => _busy = true);
    var removed = 0;
    String? failure;
    for (final itemId in [..._selected]) {
      try {
        await api.removeItem(_id, itemId);
        removed++;
      } catch (error) {
        failure = ApiError.from(error).message;
      }
    }
    ref.invalidate(collectionDetailProvider(_id));
    if (!mounted) return;
    setState(() {
      _busy = false;
      _selectionMode = false;
      _selected.clear();
    });
    showSnack(
      context,
      failure == null ? '已移出 $removed 项' : '已移出 $removed 项，其余失败：$failure',
    );
  }
}
