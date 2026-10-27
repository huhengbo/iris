// 详情页和海报卡共用的条目动作：播放、已看、收藏、稍后观看。
// 这几个动作都会改动多处数据，刷新范围集中在这里，避免两个入口各写一份。

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/format.dart';
import '../../core/providers.dart';
import '../../core/widgets/snack.dart';
import '../favorites/favorites_api.dart';
import '../library/browse_api.dart';
import '../play/play_api.dart';
import '../play/play_launcher.dart';
import '../watchlist/watchlist_api.dart';

/// 播放入口：电影播 [version]（为空时由服务端选），剧集播接着看的一集。
Future<void> playMedia(
  BuildContext context,
  MediaDetail detail, {
  MediaVersion? version,
  int? subtitleId,
}) async {
  final next = detail.nextEpisode;
  final target = next != null
      ? PlayTarget.episode(next.id)
      : version != null
      ? PlayTarget.version(version.id)
      : PlayTarget.item(detail.card.id);
  await play(
    context,
    PlayRequest(
      itemId: detail.card.id,
      target: target,
      subtitle: [
        detail.card.title,
        if (next != null) episodeLabel(detail, next.seasonNumber, next.number),
        ?version?.label,
      ].join(' · '),
      recoveryVersions: detail.versions,
      subtitleId: next == null ? subtitleId : null,
    ),
  );
}

/// 剧集只有一季时不写季号。
String episodeLabel(MediaDetail detail, int season, int episode) =>
    detail.seasons.length > 1 ? formatEpisode(season, episode) : '第 $episode 集';

/// 改已看标记，再刷新受影响的数据：详情（整部是否看完、接着看哪一集）、
/// 分集列表和首页。等刷新完再返回，按钮的忙碌状态覆盖整个过程。返回是否改成功。
Future<bool> markWatched(
  BuildContext context,
  WidgetRef ref, {
  required WatchedScope scope,
  required int id,
  required bool watched,
  required int itemId,
}) async {
  try {
    await setWatched(ref.read(apiClientProvider), scope, id, watched: watched);
  } catch (e) {
    if (context.mounted) showApiErrorSnack(context, e);
    return false;
  }
  if (!context.mounted) return true;
  // 海报墙、搜索结果和首页的「已看」标记都可能跟着变
  ref
    ..invalidate(homeFeedProvider)
    ..invalidate(seasonProvider)
    ..invalidate(libraryItemsProvider)
    ..invalidate(searchResultsProvider);
  // 刷新失败由页面自己显示，这里不再提示
  await ref
      .refresh(mediaDetailProvider(itemId).future)
      .then<void>((_) {}, onError: (Object _) {});
  return true;
}

/// 收藏 / 取消收藏，刷新首页、收藏页、海报墙和详情。
Future<void> setFavorite(
  WidgetRef ref,
  MediaCard card, {
  required bool favorite,
}) async {
  final kind = card.kind == MediaKind.movie
      ? FavoriteKind.movie
      : FavoriteKind.series;
  await ref
      .read(favoritesApiProvider)
      .setFavorite(kind, card.id, favorite: favorite);
  ref
    ..invalidate(homeFeedProvider)
    ..invalidate(favoritesProvider)
    ..invalidate(libraryItemsProvider)
    ..invalidate(mediaDetailProvider(card.id));
}

/// 加入 / 移出稍后观看，刷新首页、稍后观看页、海报墙和详情。
Future<void> setWatchlisted(
  WidgetRef ref,
  int itemId, {
  required bool watchlisted,
}) async {
  await ref
      .read(watchlistApiProvider)
      .setWatchlisted(itemId, watchlisted: watchlisted);
  ref
    ..invalidate(homeFeedProvider)
    ..invalidate(watchlistProvider)
    ..invalidate(libraryItemsProvider)
    ..invalidate(mediaDetailProvider(itemId));
}
