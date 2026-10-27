import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/motion.dart';
import '../../app/shell.dart' show wideLayoutBreakpoint;
import '../../app/theme.dart';
import '../../app/tv.dart';
import '../../core/api/api_error.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/focus_tile.dart';
import '../../core/widgets/skeleton.dart';
import '../../core/widgets/snack.dart';
import '../admin/item_actions.dart';
import '../auth/auth_controller.dart';
import '../detail/detail_page.dart';
import '../detail/media_actions.dart';
import '../play/play_api.dart';
import 'art_image.dart';
import 'browse_api.dart';

void openDetail(BuildContext context, MediaCard media, String heroTag) {
  context.push(
    '/item/${media.id}',
    extra: DetailArgs(card: media, heroTag: heroTag),
  );
}

/// 海报 + 片名（+ 一行补充信息）。宽度随父级。
/// 悬停、键盘或遥控器聚焦时盖一层操作：中央播放、底部已看 / 编辑 / ⋯（照搬 Plex）。
/// 触屏没有悬停，长按海报打开和 ⋯ 同一个菜单；TV 不加这一层，菜单在详情页。
class PosterCard extends ConsumerWidget {
  const PosterCard({
    super.key,
    required this.media,
    required this.heroTag,
    this.subtitle,
    this.autofocus = false,
    this.selected = false,
    this.onSelect,
  });

  final MediaCard media;
  final String heroTag;

  /// 片名下的一行，如年份或饰演的角色
  final String? subtitle;

  final bool autofocus;
  final bool selected;
  final VoidCallback? onSelect;

  /// 片名和补充信息的行高，用来算片架和网格的高度
  static List<double> textLines({required bool subtitle}) => [
    20,
    if (subtitle) 18,
  ];

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final textTheme = Theme.of(context).textTheme;
    final subtitle = this.subtitle;
    final tile = FocusTile(
      autofocus: autofocus,
      semanticLabel: [
        media.title,
        ?subtitle,
        if (media.watched) '已看',
      ].join('，'),
      onTap: onSelect ?? () => openDetail(context, media, heroTag),
      // 所有人都能用长按打开同一个菜单；多选时点按是勾选，不弹菜单
      onLongPress: onSelect == null
          ? () => openCardActions(context, ref, media)
          : null,
      builder: (context, highlight) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          AspectRatio(
            aspectRatio: 2 / 3,
            child: ArtFrame(
              highlight: highlight,
              radius: CinemaRadius.poster,
              dimmed: media.watched,
              badge: media.watched ? const WatchedBadge() : null,
              child: Stack(
                fit: StackFit.expand,
                children: [
                  ArtHero(
                    tag: heroTag,
                    child: ArtImage(art: media.poster),
                  ),
                  if (onSelect != null)
                    Positioned(
                      top: 8,
                      right: 8,
                      child: IgnorePointer(
                        child: DecoratedBox(
                          decoration: BoxDecoration(
                            color: CinemaColors.scrim,
                            borderRadius: BorderRadius.circular(
                              CinemaRadius.small,
                            ),
                          ),
                          child: Icon(
                            selected
                                ? Icons.check_box
                                : Icons.check_box_outline_blank,
                            color: selected
                                ? CinemaColors.amber
                                : CinemaColors.text0,
                          ),
                        ),
                      ),
                    ),
                ],
              ),
            ),
          ),
          const SizedBox(height: 8),
          Text(
            media.title,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: textTheme.bodySmall!.copyWith(
              color: highlight.focused
                  ? CinemaColors.text0
                  : CinemaColors.text1,
              fontWeight: highlight.focused ? FontWeight.w600 : null,
            ),
          ),
          if (subtitle != null)
            Text(
              subtitle,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: textTheme.labelSmall!.copyWith(color: CinemaColors.text2),
            ),
        ],
      ),
    );
    // 多选时点卡片是勾选，不加悬停层；TV 没有悬停，菜单在详情页
    if (onSelect != null || isTelevision(context)) return tile;
    return _PosterOverlay(media: media, heroTag: heroTag, child: tile);
  }
}

/// 海报卡上的「⋯」和长按：先取详情，再打开和详情页同一套菜单。
/// 收藏、稍后观看的状态和可播放的版本都从详情来，列表卡片上没有这些字段。
Future<void> openCardActions(
  BuildContext context,
  WidgetRef ref,
  MediaCard media,
) async {
  final MediaDetail detail;
  try {
    detail = await ref.read(mediaDetailProvider(media.id).future);
  } catch (error) {
    if (context.mounted) showApiErrorSnack(context, error);
    return;
  }
  if (!context.mounted) return;
  await showItemActions(
    context,
    ref,
    media,
    detail: detail,
    withWatchlist: true,
  );
}

/// 海报上的操作层，照 Plex：鼠标悬停、键盘或遥控器聚焦到这张卡时出现。
/// 按钮放在 FocusTile 外面做兄弟节点，避免嵌套在可聚焦的卡片里导致方向键到不了；
/// 隐藏时仍可聚焦，Tab 走到它时会显示出来。
class _PosterOverlay extends ConsumerStatefulWidget {
  const _PosterOverlay({
    required this.media,
    required this.heroTag,
    required this.child,
  });

  final MediaCard media;
  final String heroTag;
  final Widget child;

  @override
  ConsumerState<_PosterOverlay> createState() => _PosterOverlayState();
}

class _PosterOverlayState extends ConsumerState<_PosterOverlay> {
  bool _hovered = false;
  bool _focusWithin = false;

  /// 操作层有 4 个按钮，海报墙一屏几十张卡，全部常驻构建会拖慢滚动；
  /// 第一次悬停或聚焦时才建，之后留着（淡出动画和 Tab 顺序都要它在）。
  bool _actionsBuilt = false;

  @override
  void initState() {
    super.initState();
    FocusManager.instance.addHighlightModeListener(_highlightModeChanged);
  }

  @override
  void dispose() {
    FocusManager.instance.removeHighlightModeListener(_highlightModeChanged);
    super.dispose();
  }

  void _highlightModeChanged(FocusHighlightMode _) => setState(() {});

  @override
  Widget build(BuildContext context) {
    // 触屏点过之后焦点可能还留在卡片上，只在键盘、遥控器操作时算聚焦
    final visible =
        _hovered ||
        _focusWithin &&
            FocusManager.instance.highlightMode ==
                FocusHighlightMode.traditional;
    _actionsBuilt = _actionsBuilt || visible;
    final admin = ref.watch(authControllerProvider).value?.isAdmin == true;
    return MouseRegion(
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      child: Focus(
        canRequestFocus: false,
        skipTraversal: true,
        onFocusChange: (v) => setState(() => _focusWithin = v),
        child: LayoutBuilder(
          builder: (context, constraints) => Stack(
            clipBehavior: Clip.none,
            children: [
              widget.child,
              if (_actionsBuilt)
                Positioned(
                  left: 0,
                  right: 0,
                  top: 0,
                  // 海报是 2:3，操作层只盖住海报，不盖片名
                  height: constraints.maxWidth * 1.5,
                  child: IgnorePointer(
                    ignoring: !visible,
                    child: AnimatedOpacity(
                      opacity: visible ? 1 : 0,
                      duration: Motion.focus,
                      child: _PosterActions(
                        media: widget.media,
                        heroTag: widget.heroTag,
                        admin: admin,
                      ),
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

/// 操作层的内容：遮罩、中央播放键、底部已看 / 编辑 / ⋯。
/// 遮罩不拦点击：点在按钮以外的地方仍旧进详情。
class _PosterActions extends ConsumerStatefulWidget {
  const _PosterActions({
    required this.media,
    required this.heroTag,
    required this.admin,
  });

  final MediaCard media;
  final String heroTag;
  final bool admin;

  @override
  ConsumerState<_PosterActions> createState() => _PosterActionsState();
}

class _PosterActionsState extends ConsumerState<_PosterActions> {
  final _menuFocus = FocusNode(debugLabel: 'poster-menu');

  bool _playBusy = false;
  bool _menuBusy = false;
  bool _watchedBusy = false;

  /// 点击后的目标值；等列表刷新带回新状态再清掉，避免勾选闪回
  bool? _pendingWatched;

  bool get _watched => _pendingWatched ?? widget.media.watched;

  @override
  void didUpdateWidget(covariant _PosterActions oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (_pendingWatched != null && widget.media.watched == _pendingWatched) {
      _pendingWatched = null;
    }
  }

  @override
  void dispose() {
    _menuFocus.dispose();
    super.dispose();
  }

  /// 电影直接播，剧集播推荐的下一集；取不到详情时进详情页。
  Future<void> _play() async {
    setState(() => _playBusy = true);
    try {
      final detail = await ref.read(
        mediaDetailProvider(widget.media.id).future,
      );
      if (!mounted) return;
      // 剧集没有可播的集时放进详情页，让用户自己选季和集
      if (detail.card.kind == MediaKind.series && detail.nextEpisode == null) {
        openDetail(context, widget.media, widget.heroTag);
        return;
      }
      await playMedia(context, detail, version: detail.versions.firstOrNull);
    } catch (_) {
      if (!mounted) return;
      openDetail(context, widget.media, widget.heroTag);
    } finally {
      if (mounted) setState(() => _playBusy = false);
    }
  }

  Future<void> _toggleWatched() async {
    final next = !_watched;
    setState(() {
      _watchedBusy = true;
      _pendingWatched = next;
    });
    final ok = await markWatched(
      context,
      ref,
      scope: WatchedScope.item,
      id: widget.media.id,
      watched: next,
      itemId: widget.media.id,
    );
    if (!mounted) return;
    setState(() {
      _watchedBusy = false;
      if (!ok) _pendingWatched = null;
    });
  }

  Future<void> _menu() async {
    // 打开菜单前要先取一次详情；这期间不放转圈，否则弹层里的动画一直不停
    if (_menuBusy) return;
    _menuBusy = true;
    await openCardActions(context, ref, widget.media);
    _menuBusy = false;
    // 操作可能让列表重建，按钮已卸载时不再抢焦点
    if (mounted) _menuFocus.requestFocus();
  }

  @override
  Widget build(BuildContext context) {
    return ClipRRect(
      borderRadius: BorderRadius.circular(CinemaRadius.poster),
      child: Stack(
        fit: StackFit.expand,
        children: [
          IgnorePointer(
            child: DecoratedBox(
              decoration: BoxDecoration(
                gradient: LinearGradient(
                  begin: Alignment.topCenter,
                  end: Alignment.bottomCenter,
                  colors: [
                    CinemaColors.scrim.withValues(alpha: .45),
                    CinemaColors.scrim.withValues(alpha: 0),
                    CinemaColors.scrim,
                  ],
                  stops: const [0, .45, 1],
                ),
              ),
            ),
          ),
          Center(
            // 聚焦描边由主题的 IconButton 样式给出，不再套 FocusRing：
            // FocusRing 的焦点节点会插在按钮和可聚焦节点之间，Tab 会在卡片和按钮之间来回跳
            child: IconButton.filled(
              key: const ValueKey('poster-play'),
              tooltip: '播放',
              onPressed: _playBusy ? () {} : _play,
              iconSize: 32,
              style: IconButton.styleFrom(
                fixedSize: const Size.square(52),
                minimumSize: const Size.square(52),
                padding: EdgeInsets.zero,
                backgroundColor: CinemaColors.scrim,
                foregroundColor: CinemaColors.text0,
              ),
              icon: _playBusy
                  ? const SizedBox.square(
                      dimension: 20,
                      child: CircularProgressIndicator(
                        strokeWidth: 2,
                        color: CinemaColors.text0,
                      ),
                    )
                  : const Icon(Icons.play_arrow_rounded),
            ),
          ),
          Positioned(
            left: 6,
            right: 6,
            bottom: 6,
            child: FittedBox(
              fit: BoxFit.scaleDown,
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  _OverlayButton(
                    buttonKey: const ValueKey('poster-watched'),
                    tooltip: _watched ? '标记未看' : '标记已看',
                    icon: _watched
                        ? Icons.check_circle
                        : Icons.check_circle_outline,
                    color: _watched ? CinemaColors.amber : null,
                    busy: _watchedBusy,
                    onPressed: _toggleWatched,
                  ),
                  if (widget.admin) ...[
                    const SizedBox(width: 4),
                    _OverlayButton(
                      buttonKey: const ValueKey('poster-edit'),
                      tooltip: '编辑',
                      icon: Icons.edit_outlined,
                      onPressed: () => context.push(
                        '/settings/manage/items/${widget.media.id}',
                      ),
                    ),
                  ],
                  const SizedBox(width: 4),
                  _OverlayButton(
                    buttonKey: const ValueKey('poster-menu'),
                    tooltip: '更多操作',
                    icon: Icons.more_horiz,
                    focusNode: _menuFocus,
                    onPressed: _menu,
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// 操作层底部的圆形图标按钮：scrim 底，压在任意海报上都看得清。
class _OverlayButton extends StatelessWidget {
  const _OverlayButton({
    required this.buttonKey,
    required this.tooltip,
    required this.icon,
    required this.onPressed,
    this.color,
    this.busy = false,
    this.focusNode,
  });

  final Key buttonKey;
  final String tooltip;
  final IconData icon;
  final VoidCallback onPressed;
  final Color? color;
  final bool busy;
  final FocusNode? focusNode;

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: IconButton(
        key: buttonKey,
        focusNode: focusNode,
        // 忙碌时不禁用：禁用的按钮会丢掉遥控器焦点
        onPressed: busy ? () {} : onPressed,
        iconSize: 20,
        style: IconButton.styleFrom(
          fixedSize: const Size.square(34),
          minimumSize: const Size.square(34),
          padding: EdgeInsets.zero,
          tapTargetSize: MaterialTapTargetSize.shrinkWrap,
          backgroundColor: CinemaColors.scrim,
          foregroundColor: color ?? CinemaColors.text0,
        ),
        icon: busy
            ? const SizedBox.square(
                dimension: 16,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: CinemaColors.text0,
                ),
              )
            : Icon(icon),
      ),
    );
  }
}

/// 海报和剧照右上角的已看标记。压在任意画面上都要看得清，所以带深色底。
class WatchedBadge extends StatelessWidget {
  const WatchedBadge({super.key});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: 22,
      height: 22,
      decoration: const BoxDecoration(
        color: CinemaColors.scrim,
        shape: BoxShape.circle,
      ),
      child: const Icon(Icons.check, size: 15, color: CinemaColors.amber),
    );
  }
}

/// 海报网格的列数和格子尺寸：手机上三列起，宽屏海报约 150 宽。
class PosterGridLayout {
  PosterGridLayout(BuildContext context, double width, {bool subtitle = true})
    : gap = width >= wideLayoutBreakpoint ? 20 : 12,
      _lines = PosterCard.textLines(subtitle: subtitle),
      _scaler = MediaQuery.textScalerOf(context) {
    final maxTile = width >= wideLayoutBreakpoint ? 160.0 : 132.0;
    columns = ((width + gap) / (maxTile + gap)).ceil().clamp(3, 12);
    tileWidth = (width - gap * (columns - 1)) / columns;
  }

  final double gap;
  final List<double> _lines;
  final TextScaler _scaler;
  late final int columns;
  late final double tileWidth;

  double get tileHeight =>
      tileWidth * 3 / 2 +
      8 +
      _lines.fold(0.0, (sum, h) => sum + _scaler.scale(h)).ceilToDouble();

  SliverGridDelegate get delegate => SliverGridDelegateWithFixedCrossAxisCount(
    crossAxisCount: columns,
    mainAxisSpacing: gap + 4,
    crossAxisSpacing: gap,
    mainAxisExtent: tileHeight,
  );
}

/// 分页加载的海报网格（sliver）：首屏骨架、出错重试、空状态，滚到底部附近自动取下一页。
class PagedPosterGrid extends StatelessWidget {
  const PagedPosterGrid({
    super.key,
    required this.value,
    required this.heroPrefix,
    required this.padding,
    required this.empty,
    required this.onRetry,
    required this.onLoadMore,
    required this.onRetryMore,
    this.subtitle,
    this.selectionMode = false,
    this.selectedIds = const {},
    this.onSelected,
  });

  final AsyncValue<CardList> value;
  final String heroPrefix;
  final EdgeInsets padding;
  final Widget empty;
  final VoidCallback onRetry;
  final VoidCallback onLoadMore;
  final VoidCallback onRetryMore;
  final String? Function(MediaCard media)? subtitle;
  final bool selectionMode;
  final Set<int> selectedIds;
  final ValueChanged<int>? onSelected;

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
          : _skeleton();
    }
    if (list.items.isEmpty) {
      return SliverFillRemaining(hasScrollBody: false, child: empty);
    }
    return SliverMainAxisGroup(
      slivers: [
        SliverPadding(
          padding: padding,
          sliver: SliverLayoutBuilder(
            builder: (context, constraints) {
              final layout = PosterGridLayout(
                context,
                constraints.crossAxisExtent,
                subtitle: subtitle != null,
              );
              return SliverGrid.builder(
                gridDelegate: layout.delegate,
                itemCount: list.items.length,
                itemBuilder: (context, i) {
                  if (i == list.items.length - layout.columns * 4 &&
                      list.hasMore &&
                      !list.loadingMore &&
                      list.moreError == null) {
                    // 不能在构建过程中改状态，等这一帧画完再取
                    WidgetsBinding.instance.addPostFrameCallback(
                      (_) => onLoadMore(),
                    );
                  }
                  final media = list.items[i];
                  return PosterCard(
                    // 遥控器进来就落在第一张海报上；页面已有焦点（如正在输入）时不抢
                    autofocus: i == 0 && isTelevision(context),
                    media: media,
                    heroTag: '$heroPrefix:${media.id}',
                    subtitle: subtitle?.call(media),
                    selected: selectedIds.contains(media.id),
                    onSelect: selectionMode && onSelected != null
                        ? () => onSelected!(media.id)
                        : null,
                  );
                },
              );
            },
          ),
        ),
        SliverToBoxAdapter(
          child: _Footer(list: list, onRetry: onRetryMore),
        ),
      ],
    );
  }

  Widget _skeleton() => SliverPadding(
    padding: padding,
    sliver: SliverLayoutBuilder(
      builder: (context, constraints) {
        final layout = PosterGridLayout(
          context,
          constraints.crossAxisExtent,
          subtitle: subtitle != null,
        );
        return SliverGrid.builder(
          gridDelegate: layout.delegate,
          itemCount: layout.columns * 4,
          itemBuilder: (_, _) => Semantics(
            label: '正在加载',
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                AspectRatio(
                  aspectRatio: 2 / 3,
                  child: Skeleton(
                    borderRadius: BorderRadius.circular(CinemaRadius.poster),
                  ),
                ),
                const SizedBox(height: 10),
                FractionallySizedBox(
                  widthFactor: 0.7,
                  child: SizedBox(
                    height: 12,
                    child: Skeleton(
                      borderRadius: BorderRadius.circular(CinemaRadius.tiny),
                    ),
                  ),
                ),
              ],
            ),
          ),
        );
      },
    ),
  );
}

class _Footer extends StatelessWidget {
  const _Footer({required this.list, required this.onRetry});

  final CardList list;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final Widget child;
    if (list.moreError != null) {
      child = Row(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Flexible(
            child: Text(
              ApiError.from(list.moreError!).message,
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
          const SizedBox(width: 8),
          TextButton(onPressed: onRetry, child: const Text('重试')),
        ],
      );
    } else if (list.loadingMore) {
      child = const SizedBox.square(
        dimension: 24,
        child: CircularProgressIndicator(strokeWidth: 2.5),
      );
    } else if (list.hasMore) {
      // 页脚不在屏幕上时也会构建，没在取的时候不放一直转的指示器
      child = const SizedBox(height: 24);
    } else {
      child = Text(
        '共 ${list.total} 部',
        style: Theme.of(context).textTheme.labelSmall!
            .copyWith(color: CinemaColors.text2),
      );
    }
    return Padding(
      padding: EdgeInsets.fromLTRB(
        CinemaSpacing.gutter,
        24,
        CinemaSpacing.gutter,
        24 + MediaQuery.paddingOf(context).bottom,
      ),
      child: Center(child: child),
    );
  }
}
