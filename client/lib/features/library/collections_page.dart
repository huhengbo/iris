import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/shell.dart';
import '../../app/theme.dart';
import '../../app/tv.dart';
import '../../core/api/api_error.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/focus_tile.dart';
import '../../core/widgets/snack.dart';
import '../../core/widgets/text_prompt.dart';
import '../auth/auth_controller.dart';
import 'art_image.dart';
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
          : LayoutBuilder(
              builder: (context, constraints) {
                final layout = PosterGridLayout(
                  context,
                  constraints.maxWidth - gutter * 2,
                );
                return GridView.builder(
                  padding: EdgeInsets.fromLTRB(gutter, 8, gutter, 24),
                  gridDelegate: layout.delegate,
                  itemCount: items.length,
                  itemBuilder: (context, index) => _CollectionCard(
                    collection: items[index],
                    // 遥控器进来落在第一个合集上；手机不抢焦点
                    autofocus: index == 0 && isTelevision(context),
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

/// 合集卡：封面 + 名称 + 成员数，和影视墙的海报卡同尺寸。
class _CollectionCard extends StatelessWidget {
  const _CollectionCard({required this.collection, required this.autofocus});

  final CollectionSummary collection;
  final bool autofocus;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final count = collection.itemCount == 0
        ? '暂无影片'
        : '${collection.itemCount} 部';
    return FocusTile(
      autofocus: autofocus,
      semanticLabel: '${collection.title}，$count',
      onTap: () => context.push('/collections/${collection.id}'),
      builder: (context, highlight) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          AspectRatio(
            aspectRatio: 2 / 3,
            child: ArtFrame(
              highlight: highlight,
              radius: CinemaRadius.poster,
              child: ArtImage(
                art: collection.cover,
                icon: Icons.collections_bookmark_outlined,
              ),
            ),
          ),
          const SizedBox(height: 8),
          Text(
            collection.title,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: textTheme.bodySmall!.copyWith(
              color: highlight.focused
                  ? CinemaColors.text0
                  : CinemaColors.text1,
              fontWeight: highlight.focused ? FontWeight.w600 : null,
            ),
          ),
          Text(
            count,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: textTheme.labelSmall!.copyWith(color: CinemaColors.text2),
          ),
        ],
      ),
    );
  }
}

/// 合集详情页顶部：背景用合集的头图（压暗），左边封面，右边名称、成员数和简介。
class _CollectionHeader extends StatelessWidget {
  const _CollectionHeader({required this.detail, required this.gutter});

  final CollectionDetail detail;
  final double gutter;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final overview = detail.overview;
    return Stack(
      children: [
        if (!detail.backdrop.isEmpty)
          Positioned.fill(
            child: Stack(
              fit: StackFit.expand,
              children: [
                ArtImage(art: detail.backdrop),
                const DecoratedBox(
                  decoration: BoxDecoration(
                    gradient: LinearGradient(
                      begin: Alignment.topCenter,
                      end: Alignment.bottomCenter,
                      colors: [Color(0xB3000000), CinemaColors.bg],
                    ),
                  ),
                ),
              ],
            ),
          ),
        Padding(
          padding: EdgeInsets.fromLTRB(gutter, 12, gutter, 12),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SizedBox(
                width: 96,
                child: AspectRatio(
                  aspectRatio: 2 / 3,
                  child: ArtFrame(
                    highlight: const TileHighlight(
                      focused: false,
                      hovered: false,
                    ),
                    radius: CinemaRadius.poster,
                    child: ArtImage(
                      art: detail.cover,
                      icon: Icons.collections_bookmark_outlined,
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(detail.title, style: textTheme.titleLarge),
                    const SizedBox(height: 4),
                    Text(
                      '${detail.items.length} 部影片',
                      style: textTheme.labelMedium!.copyWith(
                        color: CinemaColors.text1,
                      ),
                    ),
                    if (overview != null) ...[
                      const SizedBox(height: 8),
                      Text(
                        overview,
                        maxLines: 6,
                        overflow: TextOverflow.ellipsis,
                        style: textTheme.bodyMedium,
                      ),
                    ],
                  ],
                ),
              ),
            ],
          ),
        ),
      ],
    );
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
                      else ...[
                        IconButton(
                          tooltip: '设为封面',
                          // 封面取一个成员的海报，所以只能选一项
                          onPressed: _selected.length == 1 ? _setCover : null,
                          icon: const Icon(Icons.image_outlined),
                        ),
                        IconButton(
                          tooltip: '移出合集',
                          onPressed: _selected.isEmpty ? null : _remove,
                          icon: const Icon(Icons.playlist_remove),
                        ),
                      ],
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
            // 名称在下面的头部里显示
            title: const Text('合集'),
            actions: [
              if (isAdmin && detail?.coverItemId != null)
                IconButton(
                  tooltip: '恢复默认封面',
                  onPressed: _busy ? null : () => _resetCover(),
                  icon: const Icon(Icons.restore),
                ),
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
          if (detail != null)
            SliverToBoxAdapter(
              child: _CollectionHeader(detail: detail, gutter: gutter),
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

  Future<void> _setCover() async {
    final itemId = _selected.single;
    final title = ref
        .read(collectionDetailProvider(_id))
        .value
        ?.items
        .firstWhere((m) => m.id == itemId)
        .title;
    await _changeCover(itemId, '已将「${title ?? '所选影片'}」设为封面');
  }

  Future<void> _resetCover() => _changeCover(null, '已恢复默认封面');

  Future<void> _changeCover(int? itemId, String done) async {
    setState(() => _busy = true);
    try {
      await ref.read(collectionsApiProvider).setCover(_id, itemId);
      final libraryId = ref
          .read(collectionDetailProvider(_id))
          .value
          ?.libraryId;
      ref.invalidate(collectionDetailProvider(_id));
      if (libraryId != null) {
        ref.invalidate(libraryCollectionsProvider(libraryId));
      }
      if (!mounted) return;
      _exitSelection();
      showSnack(context, done);
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
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
