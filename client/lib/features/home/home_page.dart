import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/motion.dart';
import '../../app/shell.dart';
import '../../core/widgets/rail_scroller.dart';
import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/api/paging.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/focus_tile.dart';
import '../../core/widgets/skeleton.dart';
import '../../core/format.dart';
import '../ai/ai_entry_button.dart';
import '../library/art_image.dart';
import '../library/browse_api.dart';
import '../library/library_hub_shelf.dart';
import '../library/library_recommend_tab.dart' show queryForHub;
import '../library/poster_card.dart';
import '../../core/widgets/empty_state.dart';
import 'home_preferences.dart';

part 'home_rails.dart';

/// 首页：继续观看（Web 为最近播放）→ 稍后观看 →（可选）我的收藏 → 各资料库推荐行。
/// 版式见 DESIGN.md「布局」，模块取舍见 ADR 0014、0026。
class HomePage extends ConsumerWidget {
  const HomePage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final feed = ref.watch(homeFeedProvider);
    // 本机设置，读出来前按默认关闭，不挡首页其他内容
    final showFavorites = ref.watch(showHomeFavoritesProvider).value ?? false;
    // 后台刷新失败时 feed 带着旧值：继续显示旧内容，不用错误页顶掉它
    final cached = feed.value;
    final Widget body = switch (feed) {
      _ when cached != null && cached.isEmpty => const _EmptyHome(),
      _ when cached != null => _HomeContent(
        feed: cached,
        showFavorites: showFavorites,
        onRefresh: () => ref.refresh(homeFeedProvider.future),
      ),
      AsyncError(:final error) => ErrorRetry(
        message: ApiError.from(error).message,
        onRetry: () => ref.invalidate(homeFeedProvider),
      ),
      _ => const _HomeSkeleton(),
    };
    final content = AnimatedSwitcher(
      duration: Motion.short,
      child: KeyedSubtree(key: ValueKey(body.runtimeType), child: body),
    );
    // 手机还是宽屏由屏幕宽度决定，和 shell 的侧栏断点一致
    final wide = MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint;
    // 宽屏的稍后观看入口在侧栏（app/shell.dart），首页不再放图标
    if (wide) return Scaffold(body: content);
    return Scaffold(
      body: Column(
        children: [
          const _HomeTopBar(),
          Expanded(child: content),
        ],
      ),
    );
  }
}

/// 手机首页右上角的稍后观看入口；宽屏由侧栏承担。
class _HomeTopBar extends StatelessWidget {
  const _HomeTopBar();

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      bottom: false,
      child: SizedBox(
        height: 48,
        child: Row(
          mainAxisAlignment: MainAxisAlignment.end,
          children: [
            const AiEntryButton(),
            IconButton(
              tooltip: '稍后观看',
              onPressed: () => context.push('/watchlist'),
              icon: const Icon(Icons.bookmark_border),
            ),
            const SizedBox(width: 4),
          ],
        ),
      ),
    );
  }
}

/// 随宽度变化的尺寸。宽屏（≥ 840）的数值 TV 上同样适用。
class _Metrics {
  const _Metrics(this.width);

  final double width;

  bool get wide => width >= wideLayoutBreakpoint;
  double get gutter => CinemaSpacing.gutterFor(wide: wide);
  double get posterWidth => wide ? 140 : 104;

  /// 最近播放是横版剧照卡，手机上露出第二张提示可以横滑
  double get stillWidth => wide ? 280 : 220;
  double get railGap => wide ? 16 : 12;

  /// 区块标题行高 44（容得下「全部」按钮），标题字上下各空出约 10
  static const headerHeight = 44.0;
  static const headerSlack = 10.0;

  /// 片架上下的留白，给聚焦放大和阴影。上方贴着标题，留白算进「标题与内容」的
  /// 12 里；下方的算进区块间距 28 里
  static const railTop = CinemaSpacing.heading - headerSlack;
  static const railBottom = 10.0;

  /// 片架高度 = 图 + 间距 + 文字行高（随系统字号缩放）+ 上下留给聚焦放大的空间。
  double railHeight(
    BuildContext context, {
    required double art,
    required List<double> lines,
  }) {
    final scaler = MediaQuery.textScalerOf(context);
    final text = lines.fold(0.0, (sum, h) => sum + scaler.scale(h));
    return art + 8 + text.ceilToDouble() + railTop + railBottom + 2;
  }
}

class _HomeContent extends StatelessWidget {
  const _HomeContent({
    required this.feed,
    required this.showFavorites,
    required this.onRefresh,
  });

  final HomeFeed feed;
  final bool showFavorites;
  final Future<void> Function() onRefresh;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final m = _Metrics(constraints.maxWidth);
        return RefreshIndicator(
          onRefresh: onRefresh,
          child: CustomScrollView(
            physics: const AlwaysScrollableScrollPhysics(),
            slivers: [..._rows(context, m, feed, showFavorites)],
          ),
        );
      },
    );
  }

  List<Widget> _rows(
    BuildContext context,
    _Metrics m,
    HomeFeed feed,
    bool showFavorites,
  ) {
    final rows = <Widget>[];
    // 上一块是片架时，它下方的留白已经算进区块间距
    var afterRail = false;

    void addRecent() {
      if (feed.recent.isEmpty) return;
      rows
        ..add(
          _SectionHeader(
            title: feed.recent.any((r) => r.progress != null) ? '继续观看' : '最近播放',
            metrics: m,
            afterRail: afterRail,
          ),
        )
        ..add(
          SliverToBoxAdapter(
            child: _Rail(
              metrics: m,
              height: m.railHeight(
                context,
                art: m.stillWidth * 9 / 16,
                lines: _RecentCard.textLines,
              ),
              children: [
                for (final recent in feed.recent)
                  SizedBox(
                    width: m.stillWidth,
                    child: _RecentCard(recent: recent),
                  ),
              ],
            ),
          ),
        );
      afterRail = true;
    }

    void addWatchlist() {
      if (feed.watchlist.isEmpty) return;
      rows
        ..add(
          _SectionHeader(
            title: '稍后观看',
            metrics: m,
            afterRail: afterRail,
            onAll: () => context.push('/watchlist'),
          ),
        )
        ..add(
          SliverToBoxAdapter(
            child: _Rail(
              metrics: m,
              height: m.railHeight(
                context,
                art: m.posterWidth * 3 / 2,
                lines: PosterCard.textLines(subtitle: false),
              ),
              children: [
                for (final media in feed.watchlist)
                  SizedBox(
                    width: m.posterWidth,
                    child: PosterCard(
                      media: media,
                      heroTag: 'watchlist:${media.id}',
                    ),
                  ),
              ],
            ),
          ),
        );
      afterRail = true;
    }

    void addFavorites() {
      if (!showFavorites || feed.favorites.isEmpty) return;
      rows
        ..add(
          _SectionHeader(
            title: '我的收藏',
            metrics: m,
            afterRail: afterRail,
            onAll: () => context.push('/me/favorites'),
          ),
        )
        ..add(
          SliverToBoxAdapter(
            child: _Rail(
              metrics: m,
              height: m.railHeight(
                context,
                art: m.posterWidth * 3 / 2,
                lines: PosterCard.textLines(subtitle: false),
              ),
              children: [
                for (final media in feed.favorites)
                  SizedBox(
                    width: m.posterWidth,
                    child: PosterCard(
                      media: media,
                      heroTag: 'favorites:${media.id}',
                    ),
                  ),
              ],
            ),
          ),
        );
      afterRail = true;
    }

    // 继续观看和稍后观看固定在最前面，后面是服务端排好序的推荐行
    addRecent();
    addWatchlist();
    addFavorites();
    for (final shelf in feed.shelves) {
      rows.add(
        SliverToBoxAdapter(
          child: HubShelfView(
            hub: shelf.hub,
            posterWidth: m.posterWidth,
            gutter: m.gutter,
            heroPrefix:
                'home:${shelf.library.id}:${shelf.hub.kind}:${shelf.hub.arg ?? ''}',
            onAll: shelf.hub.kind == 'continue'
                ? null
                : () => context.go(
                    Uri(
                      path: '/library/${shelf.library.id}',
                      queryParameters: {
                        'tab': 'library',
                        ...queryForHub(shelf.library.id, shelf.hub).toParams(),
                      },
                    ).toString(),
                  ),
          ),
        ),
      );
    }
    rows.add(const SliverToBoxAdapter(child: SizedBox(height: 32)));
    return rows;
  }
}
