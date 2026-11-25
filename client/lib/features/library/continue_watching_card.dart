import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/motion.dart';
import '../../app/theme.dart';
import '../../app/tv.dart';
import '../../core/api/paging.dart';
import '../../core/format.dart';
import '../../core/widgets/focus_tile.dart';
import '../auth/auth_controller.dart';
import '../detail/media_actions.dart';
import '../play/play_api.dart';
import 'art_image.dart';
import 'browse_api.dart';
import 'poster_card.dart';

/// 继续观看（Web 为最近播放）横版 16:9 剧照卡，首页与资料库「推荐」标签共用。
/// 看到一半的在剧照底部画进度条，并写「剩 N 分钟」；桌面悬停或键盘聚焦时显示
/// 中央播放键及底部快捷操作（已看切换、移除继续观看、`⋯` 菜单），触屏支持长按、
/// 桌面支持鼠标右键唤出操作菜单。
class ContinueWatchingCard extends ConsumerWidget {
  const ContinueWatchingCard({
    super.key,
    required this.recent,
    required this.heroTag,
    this.autofocus = false,
  });

  final RecentPlay recent;
  final String heroTag;
  final bool autofocus;

  static const textLines = [20.0, 18.0];

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final textTheme = Theme.of(context).textTheme;
    final media = recent.item;
    final episode = recent.episode;
    final progress = recent.progress;
    final note = [
      if (episode == null) ...[
        if (media.year != null) '${media.year}',
        '电影',
      ] else
        formatEpisode(episode.seasonNumber, episode.number),
      if (progress != null) '剩 ${progress.remainingMinutes} 分钟',
    ].join(' · ');
    final art = [
      recent.still,
      media.backdrop,
      media.poster,
    ].firstWhere((a) => !a.isEmpty, orElse: () => Artwork.none);

    Widget buildTile({
      required bool externalHover,
      required double focusScale,
    }) => FocusTile(
      autofocus: autofocus,
      semanticLabel: '${media.title}，$note',
      focusScale: focusScale,
      hovered: externalHover,
      onTap: () => openDetail(context, media, heroTag),
      onLongPress: isTelevision(context)
          ? null
          : () => openCardActions(context, ref, media, withRemoveResume: true),
      builder: (context, highlight) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          AspectRatio(
            aspectRatio: 16 / 9,
            child: ArtFrame(
              highlight: highlight,
              radius: CinemaRadius.poster,
              dimmed: media.watched,
              badge: media.watched
                  ? const WatchedBadge()
                  : media.kind == MediaKind.series &&
                        (media.unwatchedCount ?? 0) > 0
                  ? UnwatchedCountBadge(count: media.unwatchedCount!)
                  : null,
              child: Stack(
                fit: StackFit.expand,
                children: [
                  ArtHero(
                    tag: heroTag,
                    radius: CinemaRadius.poster,
                    child: ArtImage(art: art),
                  ),
                  if (progress != null)
                    Positioned(
                      left: 8,
                      right: 8,
                      bottom: 8,
                      child: ProgressLine(value: progress.fraction),
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
          Text(
            note,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: textTheme.labelSmall!.copyWith(color: CinemaColors.text2),
          ),
        ],
      ),
    );

    if (isTelevision(context)) {
      return buildTile(externalHover: false, focusScale: 1.05);
    }
    return _ContinueWatchingOverlay(
      media: media,
      heroTag: heroTag,
      hasProgress: progress != null,
      tileBuilder: (hovered) =>
          buildTile(externalHover: hovered, focusScale: 1),
    );
  }
}

/// 16:9 继续观看卡的桌面悬停 / 键盘聚焦操作层与右键菜单。
class _ContinueWatchingOverlay extends ConsumerStatefulWidget {
  const _ContinueWatchingOverlay({
    required this.media,
    required this.heroTag,
    required this.hasProgress,
    required this.tileBuilder,
  });

  final MediaCard media;
  final String heroTag;
  final bool hasProgress;
  final Widget Function(bool hovered) tileBuilder;

  @override
  ConsumerState<_ContinueWatchingOverlay> createState() =>
      _ContinueWatchingOverlayState();
}

class _ContinueWatchingOverlayState
    extends ConsumerState<_ContinueWatchingOverlay> {
  bool _hovered = false;
  bool _focusWithin = false;
  bool _tileFocused = false;
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
    final traditional =
        FocusManager.instance.highlightMode == FocusHighlightMode.traditional;
    final tileFocused = _tileFocused && traditional;
    final visible = _hovered || (_focusWithin && traditional);
    _actionsBuilt = _actionsBuilt || visible;
    final reduced = Motion.reduced(context);
    final lifted = _hovered && !tileFocused && !reduced;
    final admin = ref.watch(authControllerProvider).value?.isAdmin == true;
    return GestureDetector(
      behavior: HitTestBehavior.translucent,
      onSecondaryTapUp: (_) =>
          openCardActions(context, ref, widget.media, withRemoveResume: true),
      child: MouseRegion(
        onEnter: (_) => setState(() => _hovered = true),
        onExit: (_) => setState(() => _hovered = false),
        child: Focus(
          canRequestFocus: false,
          skipTraversal: true,
          onFocusChange: (v) {
            if (v != _focusWithin) setState(() => _focusWithin = v);
          },
          child: AnimatedScale(
            scale: tileFocused && !reduced ? 1.05 : 1,
            duration: reduced ? Duration.zero : Motion.focus,
            curve: Motion.emphasized,
            child: LayoutBuilder(
              builder: (context, constraints) => Stack(
                clipBehavior: Clip.none,
                children: [
                  Focus(
                    canRequestFocus: false,
                    skipTraversal: true,
                    onFocusChange: (v) {
                      if (v != _tileFocused) setState(() => _tileFocused = v);
                    },
                    child: widget.tileBuilder(_hovered),
                  ),
                  if (_actionsBuilt)
                    Positioned(
                      left: 0,
                      right: 0,
                      top: 0,
                      height: constraints.maxWidth * 9 / 16,
                      child: AnimatedContainer(
                        duration: reduced ? Duration.zero : Motion.focus,
                        curve: Motion.emphasized,
                        transform: Matrix4.translationValues(
                          0,
                          lifted ? -4 : 0,
                          0,
                        ),
                        child: IgnorePointer(
                          ignoring: !visible,
                          child: AnimatedOpacity(
                            opacity: visible ? 1 : 0,
                            duration: Motion.focus,
                            curve: Motion.emphasized,
                            child: _ContinueWatchingActions(
                              media: widget.media,
                              heroTag: widget.heroTag,
                              hasProgress: widget.hasProgress,
                              admin: admin,
                            ),
                          ),
                        ),
                      ),
                    ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _ContinueWatchingActions extends ConsumerStatefulWidget {
  const _ContinueWatchingActions({
    required this.media,
    required this.heroTag,
    required this.hasProgress,
    required this.admin,
  });

  final MediaCard media;
  final String heroTag;
  final bool hasProgress;
  final bool admin;

  @override
  ConsumerState<_ContinueWatchingActions> createState() =>
      _ContinueWatchingActionsState();
}

class _ContinueWatchingActionsState
    extends ConsumerState<_ContinueWatchingActions> {
  final _menuFocus = FocusNode(debugLabel: 'continue-menu');

  bool _playBusy = false;
  bool _menuBusy = false;
  bool _watchedBusy = false;
  bool _removeBusy = false;
  bool? _pendingWatched;

  bool get _watched => _pendingWatched ?? widget.media.watched;

  @override
  void didUpdateWidget(covariant _ContinueWatchingActions oldWidget) {
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

  Future<void> _play() async {
    setState(() => _playBusy = true);
    try {
      final detail = await ref.read(
        mediaDetailProvider(widget.media.id).future,
      );
      if (!mounted) return;
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

  Future<void> _remove() async {
    if (_removeBusy) return;
    setState(() => _removeBusy = true);
    await hideFromContinueWatching(context, ref, widget.media);
    if (mounted) setState(() => _removeBusy = false);
  }

  Future<void> _menu() async {
    if (_menuBusy) return;
    _menuBusy = true;
    await openCardActions(context, ref, widget.media, withRemoveResume: true);
    _menuBusy = false;
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
                    CinemaColors.scrim.withValues(alpha: .4),
                    CinemaColors.scrim.withValues(alpha: 0),
                    CinemaColors.scrim,
                  ],
                  stops: const [0, .4, 1],
                ),
              ),
            ),
          ),
          Center(
            child: IconButton.filled(
              key: const ValueKey('continue-play'),
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
            left: 8,
            right: 8,
            bottom: widget.hasProgress ? 14 : 6,
            child: Align(
              alignment: Alignment.bottomRight,
              child: FittedBox(
                fit: BoxFit.scaleDown,
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    CardOverlayButton(
                      buttonKey: const ValueKey('continue-watched'),
                      tooltip: _watched ? '标记未看' : '标记已看',
                      icon: _watched
                          ? Icons.check_circle
                          : Icons.check_circle_outline,
                      color: _watched ? CinemaColors.amber : null,
                      busy: _watchedBusy,
                      onPressed: _toggleWatched,
                    ),
                    const SizedBox(width: 4),
                    CardOverlayButton(
                      buttonKey: const ValueKey('continue-remove'),
                      tooltip: '从继续观看移除',
                      icon: Icons.visibility_off_outlined,
                      busy: _removeBusy,
                      onPressed: _remove,
                    ),
                    if (widget.admin) ...[
                      const SizedBox(width: 4),
                      CardOverlayButton(
                        buttonKey: const ValueKey('continue-edit'),
                        tooltip: '编辑',
                        icon: Icons.edit_outlined,
                        onPressed: () => context.push(
                          '/settings/manage/items/${widget.media.id}',
                        ),
                      ),
                    ],
                    const SizedBox(width: 4),
                    CardOverlayButton(
                      buttonKey: const ValueKey('continue-menu'),
                      tooltip: '更多操作',
                      icon: Icons.more_horiz,
                      focusNode: _menuFocus,
                      onPressed: _menu,
                    ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
