import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart' show ScrollCacheExtent;
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/motion.dart';
import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/api/paging.dart' show Artwork;
import '../../core/format.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/empty_state.dart';
import '../library/art_image.dart';
import '../library/browse_api.dart';
import 'tv_widgets.dart';

/// 聚焦展示区当前展示的条目：遥控器移到哪张卡片，上方就展示它的剧照、简介。
class _Spot {
  const _Spot(this.media, [this.note]);

  final MediaCard media;

  /// 继续观看时的一行进度说明，如「第 1 季 第 47 集 · 剩 35 分钟」
  final String? note;

  @override
  bool operator ==(Object other) =>
      other is _Spot && other.media.id == media.id && other.note == note;

  @override
  int get hashCode => Object.hash(media.id, note);
}

/// 电视首页：上半屏是「聚焦展示区」（剧照 + 片名 + 简介，跟着焦点换），
/// 下半屏是一排排卡片。不是手机首页放大：没有页内的精选翻页和按钮，
/// 遥控器只做两件事——上下换一排，左右挑一张，按确定进详情。
class TvHomePage extends ConsumerStatefulWidget {
  const TvHomePage({super.key});

  @override
  ConsumerState<TvHomePage> createState() => _TvHomePageState();
}

class _TvHomePageState extends ConsumerState<TvHomePage> {
  final _spot = ValueNotifier<_Spot?>(null);

  /// 焦点停稳后才去取简介，快速划过时不发请求
  final _overviewId = ValueNotifier<int?>(null);
  Timer? _settle;

  @override
  void dispose() {
    _settle?.cancel();
    _spot.dispose();
    _overviewId.dispose();
    super.dispose();
  }

  void _focus(MediaCard media, [String? note]) {
    final spot = _Spot(media, note);
    if (_spot.value == spot) return;
    _spot.value = spot;
    _settle?.cancel();
    _settle = Timer(const Duration(milliseconds: 400), () {
      _overviewId.value = media.id;
    });
  }

  @override
  Widget build(BuildContext context) {
    final feed = ref.watch(homeFeedProvider);
    // 后台刷新失败时 feed 带着旧值：继续显示旧内容，不用错误页顶掉它
    final cached = feed.value;
    if (cached == null) {
      return switch (feed) {
        AsyncError(:final error) => ErrorRetry(
          message: ApiError.from(error).message,
          onRetry: () => ref.invalidate(homeFeedProvider),
        ),
        _ => const Center(child: CircularProgressIndicator()),
      };
    }
    final shelves = _shelves(cached);
    // 没有继续观看、稍后观看和任何推荐行时显示空状态
    if (shelves.isEmpty) {
      return const EmptyState(
        icon: Icons.video_library_outlined,
        title: '还没有内容',
        message: '在管理端添加资料库并同步后，这里会出现你的影视。',
      );
    }
    final first = shelves.firstOrNull?.first;
    if (_spot.value == null && first != null) {
      // 和第一张卡获得焦点时传给 _focus 的参数一致：没有播过进度就不写字，
      // 否则首帧会先显示年份再淡出，开局多一次没必要的交叉淡入。
      _spot.value = _Spot(
        first.media,
        first.progress == null ? null : first.note,
      );
    }
    return Stack(
      fit: StackFit.expand,
      children: [
        _Backdrop(spot: _spot),
        Column(
          children: [
            _SpotlightArea(spot: _spot, overviewId: _overviewId),
            Expanded(
              child: RefreshIndicator(
                onRefresh: () => ref.refresh(homeFeedProvider.future),
                child: ListView.builder(
                  scrollCacheExtent: const ScrollCacheExtent.pixels(900),
                  padding: EdgeInsets.fromLTRB(
                    34,
                    0,
                    MediaQuery.paddingOf(context).right,
                    MediaQuery.paddingOf(context).bottom + 24,
                  ),
                  itemCount: shelves.length,
                  itemBuilder: (context, i) => Padding(
                    padding: const EdgeInsets.only(bottom: 8),
                    child: shelves[i].build(i == 0),
                  ),
                ),
              ),
            ),
          ],
        ),
      ],
    );
  }

  /// 首页的几排与手机首页一致：继续观看 → 稍后观看 → 各资料库推荐行。
  /// 数据全部来自 homeFeedProvider，推荐行的种类和顺序由服务端按资料库配置
  /// 排好后放在 feed.shelves（手机首页见 features/home/home_page.dart 的 `_rows`）。
  List<_ShelfData> _shelves(HomeFeed feed) {
    final result = <_ShelfData>[];

    /// 继续观看的一条：剧照、集数和剩余时间。
    _Entry playEntry(RecentPlay play) {
      final progress = play.progress;
      final episode = play.episode;
      final note = [
        if (episode == null) ...[
          if (play.item.year != null) '${play.item.year}',
          '电影',
        ] else
          formatEpisode(episode.seasonNumber, episode.number),
        if (progress != null) '剩 ${progress.remainingMinutes} 分钟',
      ].join(' · ');
      return _Entry(
        play.item,
        art: [
          play.still,
          play.item.backdrop,
          play.item.poster,
        ].firstWhere((a) => !a.isEmpty, orElse: () => Artwork.none),
        note: note,
        progress: progress?.fraction,
      );
    }

    void addRecent() {
      if (feed.recent.isEmpty) return;
      result.add(
        _ShelfData(
          title: feed.recent.any((r) => r.progress != null) ? '继续观看' : '最近播放',
          kind: _ShelfKind.still,
          entries: [for (final play in feed.recent) playEntry(play)],
          onFocus: _focus,
        ),
      );
    }

    void addWatchlist() {
      if (feed.watchlist.isEmpty) return;
      result.add(
        _ShelfData(
          title: '稍后观看',
          kind: _ShelfKind.poster,
          entries: [
            for (final media in feed.watchlist)
              _Entry(
                media,
                art: media.poster,
                note: media.year == null ? '' : '${media.year}',
              ),
          ],
          onFocus: _focus,
        ),
      );
    }

    /// 一个资料库推荐行。继续观看行用剧照卡（片名下面写集数和剩余时间），
    /// 其余行用海报卡；取哪些片和手机首页的 HubShelfView 一致。
    void addHub(HubRow hub) {
      if (hub.recent.isNotEmpty) {
        // 继续观看行只显示条目列表里也有的，和手机 HubShelfView 一致
        final plays = _playsIn(hub);
        if (plays.isEmpty) return;
        result.add(
          _ShelfData(
            title: hub.title,
            kind: _ShelfKind.still,
            entries: [for (final play in plays) playEntry(play)],
            onFocus: _focus,
          ),
        );
        return;
      }
      if (hub.items.isEmpty) return;
      result.add(
        _ShelfData(
          title: hub.title,
          kind: _ShelfKind.poster,
          entries: [
            for (final media in hub.items)
              _Entry(
                media,
                art: media.poster,
                note: media.year == null ? '' : '${media.year}',
              ),
          ],
          onFocus: _focus,
        ),
      );
    }

    addRecent();
    addWatchlist();
    // 服务端已经按资料库和行位置排好序
    for (final shelf in feed.shelves) {
      addHub(shelf.hub);
    }
    return result;
  }
}

/// 推荐行里的继续观看条目：仍按 [HubRow.recent] 的顺序，只保留条目列表里也有的，
/// 和手机首页 HubShelfView 的 `_covers` 一致。
List<RecentPlay> _playsIn(HubRow hub) {
  if (hub.recent.isEmpty) return const [];
  if (hub.items.isEmpty) return hub.recent;
  return [
    for (final play in hub.recent)
      if (hub.items.any((item) => item.id == play.item.id)) play,
  ];
}

enum _ShelfKind { still, poster }

class _Entry {
  const _Entry(
    this.media, {
    required this.art,
    required this.note,
    this.progress,
  });

  final MediaCard media;
  final Artwork art;
  final String note;
  final double? progress;
}

class _ShelfData {
  const _ShelfData({
    required this.title,
    required this.kind,
    required this.entries,
    required this.onFocus,
  });

  final String title;
  final _ShelfKind kind;
  final List<_Entry> entries;
  final void Function(MediaCard media, [String? note]) onFocus;

  _Entry? get first => entries.firstOrNull;

  Widget build(bool autofocusFirst) {
    final still = kind == _ShelfKind.still;
    return TvShelf(
      title: title,
      height: still
          ? TvMetrics.stillWidth * 9 / 16 + TvStill.textHeight
          : TvMetrics.posterWidth * 3 / 2 + TvPoster.textHeight(),
      children: [
        for (final (i, e) in entries.indexed)
          if (still)
            TvStill(
              media: e.media,
              art: e.art,
              note: e.note,
              progress: e.progress,
              autofocus: autofocusFirst && i == 0,
              onFocus: (m) => onFocus(m, e.progress == null ? null : e.note),
            )
          else
            TvPoster(
              media: e.media,
              autofocus: autofocusFirst && i == 0,
              onFocus: (m) => onFocus(m),
            ),
      ],
    );
  }
}

/// 展示区后面的剧照：跟着焦点换，淡入淡出；左侧和底部压暗，文字和下面的卡片都读得清。
class _Backdrop extends StatelessWidget {
  const _Backdrop({required this.spot});

  final ValueNotifier<_Spot?> spot;

  @override
  Widget build(BuildContext context) {
    return Stack(
      fit: StackFit.expand,
      children: [
        ValueListenableBuilder<_Spot?>(
          valueListenable: spot,
          builder: (context, spot, _) {
            final art = spot == null
                ? Artwork.none
                : [
                    spot.media.backdrop,
                    spot.media.poster,
                  ].firstWhere((a) => !a.isEmpty, orElse: () => Artwork.none);
            return AnimatedSwitcher(
              duration: Motion.long,
              child: SizedBox.expand(
                key: ValueKey(spot?.media.id),
                child: art.isEmpty
                    ? const ColoredBox(color: CinemaColors.bg)
                    : ArtImage(art: art),
              ),
            );
          },
        ),
        const DecoratedBox(
          decoration: BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.centerLeft,
              end: Alignment.centerRight,
              colors: [Color(0xF2120E0B), Color(0xB3120E0B), Color(0x00120E0B)],
              stops: [0, .42, .85],
            ),
          ),
        ),
        const DecoratedBox(
          decoration: BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
              colors: [Color(0x00120E0B), Color(0xE6120E0B), Color(0xFF120E0B)],
              stops: [.2, .52, .72],
            ),
          ),
        ),
      ],
    );
  }
}

/// 展示区：片名、元数据、（停稳后取到的）简介。
class _SpotlightArea extends StatelessWidget {
  const _SpotlightArea({required this.spot, required this.overviewId});

  final ValueNotifier<_Spot?> spot;
  final ValueNotifier<int?> overviewId;

  static const height = 380.0;

  @override
  Widget build(BuildContext context) {
    final safe = MediaQuery.paddingOf(context);
    return SizedBox(
      height: height + safe.top,
      child: Padding(
        padding: EdgeInsets.fromLTRB(40, safe.top, safe.right + 48, 8),
        child: Align(
          alignment: Alignment.bottomLeft,
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 780),
            child: ValueListenableBuilder<_Spot?>(
              valueListenable: spot,
              builder: (context, spot, _) => spot == null
                  ? const SizedBox.shrink()
                  : AnimatedSwitcher(
                      duration: Motion.short,
                      child: KeyedSubtree(
                        key: ValueKey(spot),
                        child: _SpotText(spot: spot, overviewId: overviewId),
                      ),
                    ),
            ),
          ),
        ),
      ),
    );
  }
}

class _SpotText extends ConsumerWidget {
  const _SpotText({required this.spot, required this.overviewId});

  final _Spot spot;
  final ValueNotifier<int?> overviewId;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final textTheme = Theme.of(context).textTheme;
    final media = spot.media;
    final meta = [
      if (media.year != null) '${media.year}',
      media.kind == MediaKind.movie ? '电影' : '剧集',
      if (media.genres.isNotEmpty) media.genres.take(3).join(' / '),
      if (media.rating != null && media.rating! > 0)
        'TMDB ${media.rating!.toStringAsFixed(1)}',
    ].join('  ·  ');
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (spot.note != null) ...[
          Text(
            spot.note!,
            style: textTheme.titleMedium!.copyWith(
              color: CinemaColors.amber,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 6),
        ],
        Text(
          media.title,
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
          style: textTheme.displaySmall!.copyWith(
            color: CinemaColors.text0,
            fontWeight: FontWeight.w700,
            height: 1.1,
          ),
        ),
        const SizedBox(height: 10),
        Text(
          meta,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: textTheme.titleMedium!.copyWith(color: CinemaColors.text1),
        ),
        ValueListenableBuilder<int?>(
          valueListenable: overviewId,
          builder: (context, id, _) {
            final overview = id == media.id
                ? ref.watch(mediaDetailProvider(media.id)).value?.overview
                : null;
            if (overview == null || overview.trim().isEmpty) {
              return const SizedBox(height: 8);
            }
            return Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Text(
                overview.trim(),
                maxLines: 3,
                overflow: TextOverflow.ellipsis,
                style: textTheme.titleMedium!.copyWith(
                  color: CinemaColors.text1,
                  height: 1.5,
                ),
              ),
            );
          },
        ),
      ],
    );
  }
}
