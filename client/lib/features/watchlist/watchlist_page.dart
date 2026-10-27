import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/shell.dart' show wideLayoutBreakpoint;
import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/skeleton.dart';
import '../../core/widgets/snack.dart';
import '../library/browse_api.dart';
import '../library/poster_card.dart';
import 'watchlist_api.dart';

/// 稍后观看：按加入时间倒序的海报网格，每张卡可以移出。
/// 空状态说明怎么加入、什么时候自动移出。
class WatchlistPage extends StatelessWidget {
  const WatchlistPage({super.key});

  @override
  Widget build(BuildContext context) {
    final wide = MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint;
    final gutter = CinemaSpacing.gutterFor(wide: wide);
    return Scaffold(
      // 宽屏从侧栏进来，和首页平级，不放返回
      appBar: AppBar(
        title: const Text('稍后观看'),
        automaticallyImplyLeading: !wide,
        titleSpacing: wide ? gutter : null,
      ),
      body: CustomScrollView(
        slivers: [
          _WatchlistGrid(padding: EdgeInsets.fromLTRB(gutter, 8, gutter, 0)),
        ],
      ),
    );
  }
}

/// 海报网格沿用收藏页的版式和分页：首屏骨架、出错重试、空状态，
/// 滚到底部附近自动取下一页。
class _WatchlistGrid extends ConsumerWidget {
  const _WatchlistGrid({required this.padding});

  final EdgeInsets padding;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final value = ref.watch(watchlistProvider);
    final list = value.value;
    if (list == null) {
      return value.hasError
          ? SliverFillRemaining(
              hasScrollBody: false,
              child: ErrorRetry(
                message: ApiError.from(value.error!).message,
                onRetry: () => ref.invalidate(watchlistProvider),
              ),
            )
          : _skeleton(padding);
    }
    if (list.items.isEmpty) {
      return const SliverFillRemaining(
        hasScrollBody: false,
        child: EmptyState(
          icon: Icons.bookmark_border,
          title: '稍后观看是空的',
          message: '在电影或剧集详情页点书签图标加入，看完会自动移出。',
        ),
      );
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
                subtitle: false,
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
                      (_) => ref.read(watchlistProvider.notifier).loadMore(),
                    );
                  }
                  return _WatchlistCard(media: list.items[i]);
                },
              );
            },
          ),
        ),
        SliverToBoxAdapter(
          child: _Footer(
            list: list,
            onRetry: () => ref.read(watchlistProvider.notifier).retryMore(),
          ),
        ),
      ],
    );
  }

  Widget _skeleton(EdgeInsets padding) => SliverPadding(
    padding: padding,
    sliver: SliverLayoutBuilder(
      builder: (context, constraints) {
        final layout = PosterGridLayout(
          context,
          constraints.crossAxisExtent,
          subtitle: false,
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

/// 一张海报卡，右上角是移出按钮。卡片本身还是通用的 PosterCard，
/// 不在这里改它的菜单和长按行为。
class _WatchlistCard extends ConsumerWidget {
  const _WatchlistCard({required this.media});

  final MediaCard media;

  Future<void> _remove(BuildContext context, WidgetRef ref) async {
    final notifier = ref.read(watchlistProvider.notifier);
    // 卡片移出后自己的 context 就失效了，提示挂在进入时的 ScaffoldMessenger 上
    final messenger = ScaffoldMessenger.of(context);
    try {
      await notifier.remove(media);
    } catch (error) {
      if (context.mounted) showApiErrorSnack(context, error);
      return;
    }
    messenger
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(
          content: const Text('已移出稍后观看'),
          action: SnackBarAction(
            label: '撤销',
            onPressed: () async {
              try {
                await notifier.restore(media);
              } catch (error) {
                messenger
                  ..hideCurrentSnackBar()
                  ..showSnackBar(
                    SnackBar(content: Text(ApiError.from(error).message)),
                  );
              }
            },
          ),
        ),
      );
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Stack(
      clipBehavior: Clip.none,
      children: [
        PosterCard(media: media, heroTag: 'watchlist:${media.id}'),
        Positioned(
          top: 4,
          right: 4,
          child: IconButton(
            key: ValueKey('watchlist-remove-${media.id}'),
            tooltip: '移出稍后观看',
            onPressed: () => _remove(context, ref),
            style: IconButton.styleFrom(
              backgroundColor: CinemaColors.scrim,
              foregroundColor: CinemaColors.text0,
              fixedSize: const Size.square(32),
              minimumSize: const Size.square(32),
              padding: EdgeInsets.zero,
            ),
            icon: const Icon(Icons.bookmark_remove, size: 18),
          ),
        ),
      ],
    );
  }
}

class _Footer extends StatelessWidget {
  const _Footer({required this.list, required this.onRetry});

  final WatchlistList list;
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
