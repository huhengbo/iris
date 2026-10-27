import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/shell.dart';
import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/focus_ring.dart';
import '../library/art_image.dart';
import '../library/browse_api.dart';
import '../library/poster_card.dart';
import 'favorites_api.dart';

/// 我的收藏：电影、剧集、单集、人物四组。每组的加载、空状态和错误重试各自独立。
class FavoritesPage extends StatelessWidget {
  const FavoritesPage({super.key});

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: FavoriteKind.values.length,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('我的收藏'),
          bottom: TabBar(
            tabs: [
              for (final kind in FavoriteKind.values) Tab(text: kind.label),
            ],
          ),
        ),
        body: TabBarView(
          children: [
            for (final kind in FavoriteKind.values) _FavoritesTab(kind: kind),
          ],
        ),
      ),
    );
  }
}

class _FavoritesTab extends StatelessWidget {
  const _FavoritesTab({required this.kind});

  final FavoriteKind kind;

  @override
  Widget build(BuildContext context) {
    return switch (kind) {
      FavoriteKind.movie || FavoriteKind.series => _MediaTab(kind: kind),
      FavoriteKind.episode || FavoriteKind.person => _PeopleTab(kind: kind),
    };
  }
}

/// 电影和剧集沿用影视墙的海报网格和分页。
class _MediaTab extends ConsumerWidget {
  const _MediaTab({required this.kind});

  final FavoriteKind kind;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final value = ref.watch(favoritesProvider(kind));
    final notifier = ref.read(favoritesProvider(kind).notifier);
    final gutter = CinemaSpacing.gutterFor(
      wide: MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint,
    );
    // 海报网格只认识卡片列表，收藏数据在这里换成同一种形式
    final cards = value.whenData(
      (list) => CardList(
        items: [
          for (final entry in list.items)
            if (entry is FavoriteMediaEntry) entry.card,
        ],
        total: list.total,
        loadingMore: list.loadingMore,
        moreError: list.moreError,
      ),
    );
    return CustomScrollView(
      slivers: [
        PagedPosterGrid(
          value: cards,
          heroPrefix: 'favorites:${kind.name}',
          padding: EdgeInsets.fromLTRB(gutter, 8, gutter, 0),
          subtitle: (media) => media.year?.toString(),
          onRetry: () => ref.invalidate(favoritesProvider(kind)),
          onLoadMore: notifier.loadMore,
          onRetryMore: notifier.retryMore,
          empty: EmptyState(
            icon: Icons.favorite_border,
            title: '还没有收藏',
            message: '在${kind.label}详情页点「收藏」，之后就能在这里找到。',
          ),
        ),
      ],
    );
  }
}

/// 单集和人物是列表：单集带剧照，人物带头像。
class _PeopleTab extends ConsumerWidget {
  const _PeopleTab({required this.kind});

  final FavoriteKind kind;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final value = ref.watch(favoritesProvider(kind));
    return switch (value) {
      AsyncData(:final value) when value.items.isEmpty => EmptyState(
        icon: Icons.favorite_border,
        title: '还没有收藏',
        message: '在${kind.label}详情页点「收藏」，之后就能在这里找到。',
      ),
      AsyncData(:final value) => _EntryList(kind: kind, list: value),
      AsyncError(:final error) => ErrorRetry(
        message: ApiError.from(error).message,
        kind: ApiError.from(error).kind,
        onRetry: () => ref.invalidate(favoritesProvider(kind)),
      ),
      _ => const Center(child: CircularProgressIndicator()),
    };
  }
}

class _EntryList extends ConsumerWidget {
  const _EntryList({required this.kind, required this.list});

  final FavoriteKind kind;
  final FavoriteList list;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final gutter = CinemaSpacing.gutterFor(
      wide: MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint,
    );
    return ListView.builder(
      padding: EdgeInsets.only(top: 4, bottom: 12, left: gutter, right: gutter),
      itemCount: list.items.length + 1,
      itemBuilder: (context, index) {
        if (index == list.items.length) {
          return _Footer(
            list: list,
            onRetry: () =>
                ref.read(favoritesProvider(kind).notifier).retryMore(),
          );
        }
        // 快到底时提前取下一页；loadMore 自己会挡掉重复请求
        if (index >= list.items.length - 4) {
          WidgetsBinding.instance.addPostFrameCallback((_) {
            ref.read(favoritesProvider(kind).notifier).loadMore();
          });
        }
        return switch (list.items[index]) {
          final FavoriteEpisodeEntry entry => _EpisodeTile(entry: entry),
          final FavoritePersonEntry entry => _PersonTile(entry: entry),
          // 电影和剧集走海报网格，不会出现在这里
          _ => const SizedBox.shrink(),
        };
      },
    );
  }
}

class _EpisodeTile extends StatelessWidget {
  const _EpisodeTile({required this.entry});

  final FavoriteEpisodeEntry entry;

  @override
  Widget build(BuildContext context) {
    final name = entry.name;
    return FocusRing(
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 4),
        leading: SizedBox(
          width: 88,
          child: AspectRatio(
            aspectRatio: 16 / 9,
            child: ClipRRect(
              borderRadius: BorderRadius.circular(CinemaRadius.small),
              child: ArtImage(art: entry.still),
            ),
          ),
        ),
        title: Text(entry.title, maxLines: 1, overflow: TextOverflow.ellipsis),
        subtitle: Text(
          [
            entry.label,
            if (name != null && name != entry.title) name,
          ].join(' · '),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
        trailing: const Icon(Icons.chevron_right),
        onTap: () => context.push(
          '/item/${entry.item.id}/episode/${entry.episode.id}'
          '?season=${entry.episode.seasonId}',
        ),
      ),
    );
  }
}

class _PersonTile extends StatelessWidget {
  const _PersonTile({required this.entry});

  final FavoritePersonEntry entry;

  @override
  Widget build(BuildContext context) {
    final original = entry.originalName;
    return FocusRing(
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 4),
        leading: ClipOval(
          child: SizedBox.square(
            dimension: 48,
            child: ArtImage(art: entry.profile, icon: Icons.person_outline),
          ),
        ),
        title: Text(entry.name, maxLines: 1, overflow: TextOverflow.ellipsis),
        subtitle: original == null || original == entry.name
            ? null
            : Text(original, maxLines: 1, overflow: TextOverflow.ellipsis),
        trailing: const Icon(Icons.chevron_right),
        onTap: () => context.push('/person/${entry.id}'),
      ),
    );
  }
}

class _Footer extends StatelessWidget {
  const _Footer({required this.list, required this.onRetry});

  final FavoriteList list;
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
        '共 ${list.total} 项',
        style: Theme.of(context).textTheme.labelSmall!
            .copyWith(color: CinemaColors.text2),
      );
    }
    return Padding(
      padding: EdgeInsets.fromLTRB(
        0,
        24,
        0,
        24 + MediaQuery.paddingOf(context).bottom,
      ),
      child: Center(child: child),
    );
  }
}
