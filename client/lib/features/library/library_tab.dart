import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/shell.dart';
import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/format.dart';
import '../../core/providers.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/picker_sheet.dart';
import '../../core/widgets/snack.dart';
import '../../core/widgets/text_prompt.dart';
import '../admin/catalog_api.dart';
import '../auth/auth_controller.dart';
import 'art_image.dart';
import 'browse_api.dart';
import 'library_preferences.dart';
import 'poster_card.dart';

/// 资料库「资料库」标签：Plex 式工具栏（筛选、排序、网格/列表）和海报墙。
/// 排序、筛选和首字母跳转都写在地址里，刷新、返回和分享链接都能还原。
class LibraryBrowseTab extends ConsumerStatefulWidget {
  const LibraryBrowseTab({
    super.key,
    required this.libraryId,
    required this.params,
    required this.detail,
  });

  final int libraryId;
  final Map<String, String> params;
  final LibraryDetail detail;

  @override
  ConsumerState<LibraryBrowseTab> createState() => _LibraryBrowseTabState();
}

class _LibraryBrowseTabState extends ConsumerState<LibraryBrowseTab> {
  bool _selectionMode = false;
  final Set<int> _selected = {};

  /// 「全选」：选中当前筛选出的全部条目（包括还没加载的），批量操作按筛选条件提交
  bool _allMatching = false;
  bool _busy = false;
  final _scroll = ScrollController();

  LibraryQuery get _query =>
      LibraryQuery.fromUri(widget.libraryId, widget.params);

  @override
  void dispose() {
    _scroll.dispose();
    super.dispose();
  }

  void _exitSelection() => setState(() {
    _selectionMode = false;
    _allMatching = false;
    _selected.clear();
  });

  /// 只改排序和筛选，切回页面顶部；tab 参数固定写成 library。
  void _update(LibraryQuery next) {
    if (_allMatching) setState(() => _allMatching = false);
    final params = {'tab': 'library', ...next.toParams()};
    Router.neglect(
      context,
      () => context.go(
        Uri(
          path: GoRouterState.of(context).uri.path,
          queryParameters: params,
        ).toString(),
      ),
    );
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) _scroll.jumpTo(0);
    });
  }

  Future<void> _pickFilter(_FilterDimension dimension) async {
    final query = _query;
    switch (dimension) {
      case _FilterDimension.genre:
        final picked = await showChoicePicker<String?>(
          context,
          title: '类型',
          current: query.genre,
          choices: [
            (value: null, label: '全部类型', subtitle: null),
            for (final genre in widget.detail.genres)
              (value: genre, label: genre, subtitle: null),
          ],
        );
        if (picked != null) _update(query.copyWith(genre: (picked.$1,)));
        break;
      case _FilterDimension.year:
        final picked = await showChoicePicker<int?>(
          context,
          title: '年份',
          current: query.year,
          choices: [
            (value: null, label: '全部年份', subtitle: null),
            for (final year in widget.detail.years)
              (value: year, label: '$year', subtitle: null),
          ],
        );
        if (picked != null) _update(query.copyWith(year: (picked.$1,)));
        break;
      case _FilterDimension.watched:
        final picked = await showChoicePicker<bool?>(
          context,
          title: '观看状态',
          current: query.watched,
          choices: const [
            (value: null, label: '全部', subtitle: null),
            (value: false, label: '未看', subtitle: null),
            (value: true, label: '已看', subtitle: null),
          ],
        );
        if (picked != null) _update(query.copyWith(watched: (picked.$1,)));
        break;
      case _FilterDimension.favorite:
        final picked = await showChoicePicker<bool?>(
          context,
          title: '收藏',
          current: query.favorite,
          choices: const [
            (value: null, label: '全部', subtitle: null),
            (value: true, label: '已收藏', subtitle: null),
          ],
        );
        if (picked != null) _update(query.copyWith(favorite: (picked.$1,)));
        break;
      case _FilterDimension.watchlist:
        final picked = await showChoicePicker<bool?>(
          context,
          title: '稍后观看',
          current: query.watchlist,
          choices: const [
            (value: null, label: '全部', subtitle: null),
            (value: true, label: '稍后观看', subtitle: '只看加入稍后观看的条目'),
          ],
        );
        if (picked != null) _update(query.copyWith(watchlist: (picked.$1,)));
        break;
      case _FilterDimension.status:
        final picked = await showChoicePicker<ProblemStatus?>(
          context,
          title: '问题',
          current: query.status,
          choices: [
            (value: null, label: '全部', subtitle: null),
            for (final status in ProblemStatus.values)
              (value: status, label: status.label, subtitle: null),
          ],
        );
        if (picked != null) _update(query.copyWith(status: (picked.$1,)));
        break;
    }
  }

  void _updateLetter(String letter) {
    final query = _query;
    _update(query.copyWith(letter: (query.letter == letter ? null : letter,)));
  }

  void _setViewMode(LibraryViewMode mode) async {
    try {
      await ref.read(libraryViewModeProvider.notifier).set(mode);
    } catch (_) {
      if (mounted) showSnack(context, '没能记住视图选择，下次打开会恢复原来的视图');
    }
  }

  @override
  Widget build(BuildContext context) {
    final query = _query;
    final items = ref.watch(libraryItemsProvider(query));
    final notifier = ref.read(libraryItemsProvider(query).notifier);
    final isAdmin = ref.watch(authControllerProvider).value?.isAdmin == true;
    final wide = MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint;
    final gutter = CinemaSpacing.gutterFor(wide: wide);
    final mode =
        ref.watch(libraryViewModeProvider).value ?? LibraryViewMode.grid;
    final loaded = items.value?.items ?? const <MediaCard>[];
    final total = items.value?.total ?? 0;

    final empty = query.filtered
        ? EmptyState(
            icon: Icons.filter_alt_off_outlined,
            title: '没有符合条件的影片',
            message: '换个筛选条件试试',
            action: FilledButton.tonal(
              onPressed: () => _update(query.cleared()),
              child: const Text('清除筛选'),
            ),
          )
        : const EmptyState(
            icon: Icons.video_library_outlined,
            title: '这个资料库还没有影片',
            message: '扫描完成并匹配元数据后，影片会出现在这里',
          );

    return Column(
      children: [
        Expanded(
          child: Row(
            children: [
              Expanded(
                child: RefreshIndicator(
                  onRefresh: () async {
                    ref.invalidate(libraryProvider(widget.libraryId));
                    final _ = await ref.refresh(
                      libraryItemsProvider(_query).future,
                    );
                  },
                  child: CustomScrollView(
                    controller: _scroll,
                    physics: const AlwaysScrollableScrollPhysics(),
                    slivers: [
                      SliverToBoxAdapter(
                        child: _Toolbar(
                          query: query,
                          genres: widget.detail.genres,
                          years: widget.detail.years,
                          isAdmin: isAdmin,
                          mode: mode,
                          gutter: gutter,
                          onChanged: _update,
                          onFilter: _pickFilter,
                          onViewMode: _setViewMode,
                        ),
                      ),
                      if (mode == LibraryViewMode.grid)
                        PagedPosterGrid(
                          value: items,
                          heroPrefix: 'library:${widget.libraryId}',
                          padding: EdgeInsets.fromLTRB(gutter, 8, gutter, 0),
                          subtitle: (media) => _subtitle(media, query.sort),
                          onRetry: () =>
                              ref.invalidate(libraryItemsProvider(query)),
                          onLoadMore: notifier.loadMore,
                          onRetryMore: notifier.retryMore,
                          selectionMode: _selectionMode,
                          selectedIds: _allMatching
                              ? {for (final media in loaded) media.id}
                              : _selected,
                          onSelected: (id) => _toggleSelected(id, loaded),
                          empty: empty,
                        )
                      else
                        _MediaList(
                          value: items,
                          gutter: gutter,
                          heroPrefix: 'library:${widget.libraryId}',
                          selectionMode: _selectionMode,
                          selectedIds: _allMatching
                              ? {for (final media in loaded) media.id}
                              : _selected,
                          onSelected: (id) => _toggleSelected(id, loaded),
                          onLoadMore: notifier.loadMore,
                          onRetry: () =>
                              ref.invalidate(libraryItemsProvider(query)),
                          onRetryMore: notifier.retryMore,
                          empty: empty,
                        ),
                    ],
                  ),
                ),
              ),
              // 按名称排序时右侧是 A-Z 跳转条；中文片名归到「#」
              if (query.sort == SortKey.name)
                _AzBar(active: query.letter, onSelected: _updateLetter),
            ],
          ),
        ),
        if (_selectionMode && isAdmin)
          _SelectionBar(
            count: _allMatching ? total : _selected.length,
            allMatching: _allMatching,
            filtered: query.filtered,
            // 后端批量筛选还没有收藏、稍后观看和首字母条件，不能让「全选筛选结果」误选整个资料库
            canSelectAllMatching:
                query.favorite == null &&
                query.watchlist == null &&
                query.letter == null,
            busy: _busy,
            onAction: (action) => _batchAction(action, query, total),
            onSelectAll: () => setState(() {
              _allMatching = !_allMatching;
              _selected.clear();
            }),
            onClose: _exitSelection,
          ),
      ],
    );
  }

  void _toggleSelected(int id, List<MediaCard> loaded) {
    if (_busy) return;
    setState(() {
      // 全选后点某一张：改回逐个选择，从已加载的全部里去掉这一张
      if (_allMatching) {
        _allMatching = false;
        _selected
          ..addAll(loaded.map((media) => media.id))
          ..remove(id);
      } else if (!_selected.add(id)) {
        _selected.remove(id);
      }
    });
  }

  Future<void> _batchAction(
    _BatchAction action,
    LibraryQuery query,
    int total,
  ) async {
    if (_busy ||
        (!_allMatching && _selected.isEmpty) ||
        (_allMatching &&
            (query.favorite != null ||
                query.watchlist != null ||
                query.letter != null))) {
      return;
    }
    final payload = <String, dynamic>{};
    if (action == _BatchAction.labelsAdd ||
        action == _BatchAction.labelsRemove) {
      final label = await showTextPrompt(
        context,
        title: action.label,
        label: '标签',
        action: action.label,
      );
      if (label == null || label.isEmpty) return;
      payload['label'] = label;
    }
    // 全选会作用到还没加载出来的条目，提交前说清范围
    if (_allMatching) {
      if (!mounted) return;
      final confirmed = await showConfirmDialog(
        context,
        title: '${action.label}全部 $total 项？',
        body: query.filtered
            ? '会作用于当前筛选出的全部条目，包括还没加载出来的。'
            : '会作用于这个资料库的全部条目，包括还没加载出来的。',
        action: action.label,
      );
      if (!confirmed) return;
    }
    setState(() => _busy = true);
    try {
      await batchItems(
        ref.read(apiClientProvider),
        _allMatching ? const [] : _selected.toList(),
        action.value,
        payload: payload,
        filter: _allMatching
            ? {
                'library_id': query.libraryId,
                if (query.genre != null) 'genre': query.genre,
                if (query.year != null) 'year': query.year,
                if (query.watched != null) 'watched': query.watched,
                // favorite、watchlist、letter 筛选不支持「全选筛选结果」，按钮已禁用
                if (query.status != null) 'status': query.status!.queryValue,
              }
            : null,
      );
      ref.invalidate(libraryItemsProvider(query));
      if (!mounted) return;
      setState(() => _busy = false);
      _exitSelection();
      showSnack(context, '${action.label}任务已提交');
    } catch (error) {
      if (!mounted) return;
      setState(() => _busy = false);
      showApiErrorSnack(context, error);
    }
  }
}

/// 海报下的补充信息随排序方式变化：按评分排时显示评分，其余显示年份。
String? _subtitle(MediaCard media, SortKey sort) {
  if (sort == SortKey.rating && media.rating != null && media.rating! > 0) {
    return '${media.rating!.toStringAsFixed(1)} 分';
  }
  return media.year?.toString();
}

enum _FilterDimension { genre, year, watched, favorite, watchlist, status }

enum _BatchAction {
  watched('标记已看', 'watched'),
  unwatched('标记未看', 'unwatched'),
  refresh('刷新元数据', 'refresh'),
  labelsAdd('添加标签', 'labels_add'),
  labelsRemove('移除标签', 'labels_remove');

  const _BatchAction(this.label, this.value);

  final String label;
  final String value;
}

class _Toolbar extends StatelessWidget {
  const _Toolbar({
    required this.query,
    required this.genres,
    required this.years,
    required this.isAdmin,
    required this.mode,
    required this.gutter,
    required this.onChanged,
    required this.onFilter,
    required this.onViewMode,
  });

  final LibraryQuery query;
  final List<String> genres;
  final List<int> years;
  final bool isAdmin;
  final LibraryViewMode mode;
  final double gutter;
  final ValueChanged<LibraryQuery> onChanged;
  final ValueChanged<_FilterDimension> onFilter;
  final ValueChanged<LibraryViewMode> onViewMode;

  @override
  Widget build(BuildContext context) {
    final ascending = query.isAscending;
    final orderLabel = switch (query.sort) {
      SortKey.name => ascending ? '名称 A 到 Z' : '名称 Z 到 A',
      SortKey.rating => ascending ? '评分从低到高' : '评分从高到低',
      _ => ascending ? '从早到晚' : '从新到旧',
    };
    final filters = _filterChips();
    return Padding(
      padding: EdgeInsets.fromLTRB(gutter, 8, gutter, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: SingleChildScrollView(
                  scrollDirection: Axis.horizontal,
                  child: Row(
                    children: [
                      _MenuButton<_FilterDimension>(
                        label: '筛选',
                        icon: Icons.filter_list,
                        tooltip: '筛选',
                        active: filters.isNotEmpty,
                        options: [
                          (_FilterDimension.genre, '类型'),
                          if (years.isNotEmpty) (_FilterDimension.year, '年份'),
                          (_FilterDimension.watched, '观看状态'),
                          (_FilterDimension.favorite, '收藏'),
                          (_FilterDimension.watchlist, '稍后观看'),
                          if (isAdmin) (_FilterDimension.status, '问题'),
                        ],
                        onSelected: onFilter,
                      ),
                      const SizedBox(width: 8),
                      _MenuChip<SortKey>(
                        label: query.sort.label,
                        icon: Icons.sort,
                        tooltip: '排序方式',
                        selected: query.sort,
                        options: [
                          for (final key in SortKey.values) (key, key.label),
                        ],
                        onSelected: (key) =>
                            onChanged(query.copyWith(sort: key)),
                      ),
                      const SizedBox(width: 4),
                      IconButton(
                        tooltip: orderLabel,
                        onPressed: () =>
                            onChanged(query.copyWith(ascending: !ascending)),
                        icon: Icon(
                          ascending ? Icons.arrow_upward : Icons.arrow_downward,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
              // 网格 / 列表切换，选择记在本机
              IconButton(
                tooltip: '网格视图',
                isSelected: mode == LibraryViewMode.grid,
                onPressed: () => onViewMode(LibraryViewMode.grid),
                icon: const Icon(Icons.grid_view),
                selectedIcon: const Icon(
                  Icons.grid_view,
                  color: CinemaColors.amber,
                ),
              ),
              IconButton(
                tooltip: '列表视图',
                isSelected: mode == LibraryViewMode.list,
                onPressed: () => onViewMode(LibraryViewMode.list),
                icon: const Icon(Icons.view_list_outlined),
                selectedIcon: const Icon(
                  Icons.view_list,
                  color: CinemaColors.amber,
                ),
              ),
            ],
          ),
          if (filters.isNotEmpty) ...[
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final chip in filters)
                  InputChip(
                    label: Text(chip.label),
                    onDeleted: chip.onDelete,
                    deleteIcon: const Icon(Icons.close, size: 16),
                    visualDensity: VisualDensity.compact,
                  ),
                TextButton(
                  onPressed: () => onChanged(query.cleared()),
                  child: const Text('全部清除'),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }

  List<({String label, VoidCallback onDelete})> _filterChips() {
    final chips = <({String label, VoidCallback onDelete})>[];
    if (query.genre case final genre?) {
      chips.add((
        label: '类型：$genre',
        onDelete: () => onChanged(query.copyWith(genre: (null,))),
      ));
    }
    if (query.year case final year?) {
      chips.add((
        label: '年份：$year',
        onDelete: () => onChanged(query.copyWith(year: (null,))),
      ));
    }
    if (query.watched case final watched?) {
      chips.add((
        label: watched ? '已看' : '未看',
        onDelete: () => onChanged(query.copyWith(watched: (null,))),
      ));
    }
    if (query.favorite == true) {
      chips.add((
        label: '已收藏',
        onDelete: () => onChanged(query.copyWith(favorite: (null,))),
      ));
    }
    if (query.watchlist == true) {
      chips.add((
        label: '稍后观看',
        onDelete: () => onChanged(query.copyWith(watchlist: (null,))),
      ));
    }
    if (query.status case final status?) {
      chips.add((
        label: '问题：${status.label}',
        onDelete: () => onChanged(query.copyWith(status: (null,))),
      ));
    }
    if (query.letter case final letter?) {
      chips.add((
        label: letter == '#' ? '首字母：其他' : '首字母：$letter',
        onDelete: () => onChanged(query.copyWith(letter: (null,))),
      ));
    }
    return chips;
  }
}

/// 胶囊按钮的键盘 / 遥控器聚焦描边。
BoxDecoration? _focusDecoration(BuildContext context) {
  if (!Focus.of(context).hasFocus ||
      FocusManager.instance.highlightMode != FocusHighlightMode.traditional) {
    return null;
  }
  return BoxDecoration(
    borderRadius: BorderRadius.circular(CinemaRadius.pill),
    border: Border.all(color: CinemaColors.amber, width: 2),
  );
}

/// 没有内置值、只用来展开下一级的胶囊按钮。
class _MenuButton<T> extends StatelessWidget {
  const _MenuButton({
    required this.label,
    required this.icon,
    required this.tooltip,
    required this.active,
    required this.options,
    required this.onSelected,
  });

  final String label;
  final IconData icon;
  final String tooltip;
  final bool active;
  final List<(T, String)> options;
  final ValueChanged<T> onSelected;

  @override
  Widget build(BuildContext context) {
    final color = active ? CinemaColors.amber : CinemaColors.text1;
    return PopupMenuButton<T>(
      tooltip: tooltip,
      borderRadius: BorderRadius.circular(CinemaRadius.card),
      onSelected: onSelected,
      itemBuilder: (_) => [
        for (final (value, text) in options)
          PopupMenuItem(value: value, child: Text(text)),
      ],
      child: Builder(
        builder: (context) => Container(
          height: 36,
          padding: const EdgeInsets.symmetric(horizontal: 12),
          decoration: BoxDecoration(
            color: active ? CinemaColors.amberSoft : CinemaColors.surface1,
            borderRadius: BorderRadius.circular(CinemaRadius.pill),
            border: Border.all(
              color: active ? CinemaColors.amberLine : CinemaColors.line,
            ),
          ),
          foregroundDecoration: _focusDecoration(context),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon, size: 18, color: color),
              const SizedBox(width: 6),
              Text(
                label,
                style: Theme.of(context).textTheme.labelLarge!
                    .copyWith(color: color),
              ),
              const SizedBox(width: 2),
              Icon(Icons.expand_more, size: 18, color: color),
            ],
          ),
        ),
      ),
    );
  }
}

/// 胶囊形的下拉菜单按钮。生效中的筛选用琥珀浅底标出。
class _MenuChip<T> extends StatelessWidget {
  const _MenuChip({
    required this.label,
    required this.tooltip,
    required this.selected,
    required this.options,
    required this.onSelected,
    this.icon,
    this.active = false,
  });

  final String label;
  final String tooltip;
  final IconData? icon;
  final bool active;
  final T selected;
  final List<(T, String)> options;
  final ValueChanged<T> onSelected;

  @override
  Widget build(BuildContext context) {
    final color = active ? CinemaColors.amber : CinemaColors.text1;
    return PopupMenuButton<(T,)>(
      tooltip: tooltip,
      borderRadius: BorderRadius.circular(CinemaRadius.pill),
      initialValue: (selected,),
      // 弹出菜单的值不能是 null，用单元素记录包一层
      onSelected: (value) => onSelected(value.$1),
      itemBuilder: (_) => [
        for (final (value, text) in options)
          CheckedPopupMenuItem(
            value: (value,),
            checked: value == selected,
            child: Text(text),
          ),
      ],
      child: Builder(
        builder: (context) => Container(
          height: 36,
          padding: const EdgeInsets.symmetric(horizontal: 12),
          decoration: BoxDecoration(
            color: active ? CinemaColors.amberSoft : CinemaColors.surface1,
            borderRadius: BorderRadius.circular(CinemaRadius.pill),
            border: Border.all(
              color: active ? CinemaColors.amberLine : CinemaColors.line,
            ),
          ),
          foregroundDecoration: _focusDecoration(context),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (icon != null) ...[
                Icon(icon, size: 18, color: color),
                const SizedBox(width: 6),
              ],
              Text(
                label,
                style: Theme.of(context).textTheme.labelLarge!
                    .copyWith(color: color),
              ),
              const SizedBox(width: 2),
              Icon(Icons.expand_more, size: 18, color: color),
            ],
          ),
        ),
      ),
    );
  }
}

/// A-Z 跳转条：字母 + 「#」（非拉丁字母）。点当前字母取消跳转。
class _AzBar extends StatelessWidget {
  const _AzBar({required this.active, required this.onSelected});

  final String? active;
  final ValueChanged<String> onSelected;

  static const _letters = [..._alphabet, '#'];

  static const _alphabet = [
    'A',
    'B',
    'C',
    'D',
    'E',
    'F',
    'G',
    'H',
    'I',
    'J',
    'K',
    'L',
    'M',
    'N',
    'O',
    'P',
    'Q',
    'R',
    'S',
    'T',
    'U',
    'V',
    'W',
    'X',
    'Y',
    'Z',
  ];

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: '首字母跳转',
      child: SizedBox(
        width: 26,
        child: Column(
          children: [
            for (final letter in _letters)
              Expanded(
                child: Tooltip(
                  message: letter == '#' ? '其他' : letter,
                  waitDuration: const Duration(milliseconds: 500),
                  child: GestureDetector(
                    behavior: HitTestBehavior.opaque,
                    onTap: () => onSelected(letter),
                    child: Center(
                      child: Text(
                        letter,
                        style: Theme.of(context).textTheme.labelSmall!.copyWith(
                          color: letter == active
                              ? CinemaColors.amber
                              : CinemaColors.text2,
                          fontWeight: letter == active ? FontWeight.w700 : null,
                        ),
                      ),
                    ),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

/// 列表视图：小海报、片名、年份、时长、评分和已看状态。分页逻辑和网格一致。
class _MediaList extends StatelessWidget {
  const _MediaList({
    required this.value,
    required this.gutter,
    required this.heroPrefix,
    required this.selectionMode,
    required this.selectedIds,
    required this.onSelected,
    required this.onLoadMore,
    required this.onRetry,
    required this.onRetryMore,
    required this.empty,
  });

  final AsyncValue<CardList> value;
  final double gutter;
  final String heroPrefix;
  final bool selectionMode;
  final Set<int> selectedIds;
  final ValueChanged<int> onSelected;
  final VoidCallback onLoadMore;
  final VoidCallback onRetry;
  final VoidCallback onRetryMore;
  final Widget empty;

  @override
  Widget build(BuildContext context) {
    final list = value.value;
    if (list == null) {
      return value.hasError
          ? SliverFillRemaining(
              hasScrollBody: false,
              child: ErrorRetry(
                message: ApiError.from(value.error!).message,
                onRetry: onRetry,
              ),
            )
          : const SliverPadding(
              padding: EdgeInsets.symmetric(horizontal: 20, vertical: 16),
              sliver: SliverToBoxAdapter(
                child: Center(child: CircularProgressIndicator()),
              ),
            );
    }
    if (list.items.isEmpty) {
      return SliverFillRemaining(hasScrollBody: false, child: empty);
    }
    return SliverList.builder(
      itemCount: list.items.length + 1,
      itemBuilder: (context, index) {
        if (index == list.items.length) {
          return _footer(context, list);
        }
        if (index == list.items.length - 4 &&
            list.hasMore &&
            !list.loadingMore &&
            list.moreError == null) {
          WidgetsBinding.instance.addPostFrameCallback((_) => onLoadMore());
        }
        final media = list.items[index];
        return Padding(
          padding: EdgeInsets.fromLTRB(gutter, 4, gutter, 4),
          child: FocusRing(
            child: InkWell(
              borderRadius: BorderRadius.circular(CinemaRadius.card),
              onTap: selectionMode
                  ? () => onSelected(media.id)
                  : () => openDetail(context, media, '$heroPrefix:${media.id}'),
              child: Padding(
                padding: const EdgeInsets.all(6),
                child: Row(
                  children: [
                    ClipRRect(
                      borderRadius: BorderRadius.circular(CinemaRadius.small),
                      child: SizedBox(
                        width: 48,
                        height: 72,
                        child: ArtImage(
                          art: media.poster,
                          icon: Icons.movie_outlined,
                        ),
                      ),
                    ),
                    const SizedBox(width: 14),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            media.title,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: Theme.of(context).textTheme.bodyMedium,
                          ),
                          const SizedBox(height: 4),
                          Text(
                            [
                              if (media.year != null) '${media.year}',
                              if (media.runtime != null)
                                formatMinutes(media.runtime!),
                              if (media.rating != null && media.rating! > 0)
                                '${media.rating!.toStringAsFixed(1)} 分',
                            ].join(' · '),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: Theme.of(context).textTheme.bodySmall!
                                .copyWith(color: CinemaColors.text2),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 8),
                    if (selectionMode)
                      Icon(
                        selectedIds.contains(media.id)
                            ? Icons.check_box
                            : Icons.check_box_outline_blank,
                        color: selectedIds.contains(media.id)
                            ? CinemaColors.amber
                            : CinemaColors.text2,
                      )
                    else if (media.watched)
                      const Text(
                        '已看',
                        style: TextStyle(color: CinemaColors.amber),
                      )
                    else
                      Text('未看', style: Theme.of(context).textTheme.bodySmall),
                  ],
                ),
              ),
            ),
          ),
        );
      },
    );
  }

  Widget _footer(BuildContext context, CardList list) {
    if (list.loadingMore) {
      return const Padding(
        padding: EdgeInsets.all(20),
        child: Center(
          child: SizedBox.square(
            dimension: 20,
            child: CircularProgressIndicator(strokeWidth: 2),
          ),
        ),
      );
    }
    if (list.moreError != null) {
      return Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          children: [
            Text(
              ApiError.from(list.moreError!).message,
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 8),
            TextButton(onPressed: onRetryMore, child: const Text('重试')),
          ],
        ),
      );
    }
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 32),
      child: Center(
        child: Text(
          '共 ${list.total} 部',
          style: Theme.of(context).textTheme.bodySmall,
        ),
      ),
    );
  }
}

class _SelectionBar extends StatelessWidget {
  const _SelectionBar({
    required this.count,
    required this.allMatching,
    required this.filtered,
    required this.canSelectAllMatching,
    required this.busy,
    required this.onAction,
    required this.onSelectAll,
    required this.onClose,
  });

  final int count;

  /// 已全选筛选结果
  final bool allMatching;
  final bool filtered;
  final bool canSelectAllMatching;
  final bool busy;
  final ValueChanged<_BatchAction> onAction;
  final VoidCallback onSelectAll;
  final VoidCallback onClose;

  @override
  Widget build(BuildContext context) => SafeArea(
    child: Material(
      color: CinemaColors.surface2,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 8, 8, 8),
        child: Row(
          children: [
            Expanded(
              child: Text(
                allMatching ? '已选全部 $count 项' : '已选 $count 项',
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ),
            TextButton(
              onPressed: busy || !canSelectAllMatching ? null : onSelectAll,
              child: Text(
                allMatching
                    ? '取消全选'
                    : filtered
                    ? '全选筛选结果'
                    : '全选',
              ),
            ),
            if (busy)
              const Padding(
                padding: EdgeInsets.all(12),
                child: SizedBox.square(
                  dimension: 20,
                  child: CircularProgressIndicator(strokeWidth: 2),
                ),
              )
            else
              // 带 icon 的 PopupMenuButton 是 IconButton，聚焦描边来自主题
              PopupMenuButton<_BatchAction>(
                tooltip: '批量操作',
                enabled: count > 0,
                onSelected: onAction,
                itemBuilder: (_) => [
                  for (final action in _BatchAction.values)
                    PopupMenuItem(value: action, child: Text(action.label)),
                ],
                icon: const Icon(Icons.more_horiz),
              ),
            IconButton(
              tooltip: '退出选择',
              onPressed: busy ? null : onClose,
              icon: const Icon(Icons.close),
            ),
          ],
        ),
      ),
    ),
  );
}
