import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/motion.dart';
import '../../app/shell.dart';
import '../../core/widgets/rail_scroller.dart';
import '../../app/theme.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/focus_tile.dart';
import '../../core/api/api_error.dart';
import '../../core/format.dart';
import '../../core/providers.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/expandable_text.dart';
import '../../core/widgets/skeleton.dart';
import '../../core/widgets/snack.dart';
import '../../core/widgets/text_prompt.dart';
import '../library/art_image.dart';
import '../library/browse_api.dart';
import '../library/poster_card.dart';
import '../play/play_api.dart';
import '../subtitles/edit_subtitles_dialog.dart';
import '../watchlist/watchlist_button.dart';
import '../admin/catalog_api.dart';
import '../admin/item_actions.dart';
import '../auth/auth_controller.dart';
import 'episode_admin.dart';
import 'media_actions.dart';
import 'people_row.dart';
import 'related_row.dart';
import 'stream_rows.dart';
import 'rating_badges.dart';

part 'detail_header.dart';
part 'detail_season_bar.dart';
part 'detail_episodes.dart';

/// 从列表打开详情时带过来的信息：头部可以立刻显示，共享元素动画也有落点。
class DetailArgs {
  const DetailArgs({required this.card, required this.heroTag});

  final MediaCard card;
  final String heroTag;
}

/// 电影和剧集详情，见 DESIGN.md「布局 › 详情」：剧照铺满顶部渐隐（宽屏时海报在左）
/// → 片名、原名与元数据 → 简介两行 → 播放和操作 → 版本、音频、字幕三个下拉
/// → 演职人员 → 季与集 → 更多类似。单集页用同一套页头（下面的 Detail* 组件）。
class DetailPage extends ConsumerWidget {
  const DetailPage({super.key, required this.id, this.args});

  final int id;
  final DetailArgs? args;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final detail = ref.watch(mediaDetailProvider(id));
    final card = detail.value?.card ?? args?.card;
    return Scaffold(
      body: Stack(
        children: [
          LayoutBuilder(
            builder: (context, constraints) => _DetailScroll(
              width: constraints.maxWidth,
              heroTag: args?.heroTag ?? 'detail:$id',
              card: card,
              detail: detail,
              onRetry: () => ref.invalidate(mediaDetailProvider(id)),
            ),
          ),
          const DetailBackButton(),
        ],
      ),
    );
  }
}

/// 头部片名、简介和按钮一列的最大宽度，再宽简介一行太长不好读；
/// 正文其余部分（演职人员、季与集、更多类似）铺满整个宽度。
const detailTextMaxWidth = 760.0;

class _DetailScroll extends ConsumerStatefulWidget {
  const _DetailScroll({
    required this.width,
    required this.heroTag,
    required this.card,
    required this.detail,
    required this.onRetry,
  });

  final double width;
  final String heroTag;
  final MediaCard? card;
  final AsyncValue<MediaDetail> detail;
  final VoidCallback onRetry;

  @override
  ConsumerState<_DetailScroll> createState() => _DetailScrollState();
}

class _DetailScrollState extends ConsumerState<_DetailScroll> {
  /// 电影选的版本，为空时是第一个（最大的）
  int? _versionId;

  /// 电影选的外挂字幕，为空时按默认字幕语言自动选
  int? _subtitleId;

  /// 用户选的季；没选时显示推荐那一集所在的季
  int? _seasonId;

  EdgeInsets get _bodyPadding => detailBodyPadding(context, widget.width);

  SeasonSummary? _season(MediaDetail detail) {
    final seasons = detail.seasons;
    if (seasons.isEmpty) return null;
    final id = _seasonId ?? detail.nextEpisode?.seasonId;
    return seasons.where((s) => s.id == id).firstOrNull ?? seasons.first;
  }

  @override
  Widget build(BuildContext context) {
    final card = widget.card;
    final detail = widget.detail.value;
    final season = detail == null ? null : _season(detail);
    return CustomScrollView(
      slivers: [
        SliverToBoxAdapter(
          child: DetailHeader(
            width: widget.width,
            poster: card?.poster ?? Artwork.none,
            backdrop: DetailBackdrop(
              heroTag: widget.heroTag,
              // 没有剧照时用海报顶上，总比空着好
              art: card == null
                  ? null
                  : card.backdrop.isEmpty
                  ? card.poster
                  : card.backdrop,
              failed: widget.detail.hasError,
            ),
            aside: detail == null ? null : _Facts(detail: detail),
            child: card == null
                ? widget.detail.hasError
                      ? ErrorRetry(
                          message: ApiError.from(widget.detail.error!).message,
                          onRetry: widget.onRetry,
                        )
                      : const DetailBodySkeleton()
                : _Info(
                    card: card,
                    detail: detail,
                    state: widget.detail,
                    onRetry: widget.onRetry,
                    versionId: _versionId,
                    subtitleId: _subtitleId,
                    onVersion: (id) => setState(() {
                      _versionId = id;
                      // 字幕跟着版本走，换了版本重新自动选
                      _subtitleId = null;
                    }),
                    onSubtitle: (id) => setState(() => _subtitleId = id),
                    onSubtitleAdded: (subtitle) =>
                        _onSubtitleAdded(detail!.card.id, subtitle),
                    onPlay: _canPlay(detail) ? () => _playMain(detail!) : null,
                    admin:
                        ref.watch(authControllerProvider).value?.isAdmin ??
                        false,
                  ),
          ),
        ),
        if (detail != null &&
            (detail.directors.isNotEmpty ||
                detail.writers.isNotEmpty ||
                detail.cast.isNotEmpty))
          SliverToBoxAdapter(
            child: PeopleRow(
              directors: detail.directors,
              writers: detail.writers,
              cast: detail.cast,
              padding: _bodyPadding,
            ),
          ),
        if (detail != null && season != null) ...[
          SliverPadding(
            padding: _bodyPadding.copyWith(top: CinemaSpacing.section),
            sliver: SliverToBoxAdapter(
              child: _SeasonBar(
                itemId: detail.card.id,
                seasons: detail.seasons,
                selected: season,
                onSelected: (s) => setState(() => _seasonId = s.id),
              ),
            ),
          ),
          _Episodes(
            itemId: detail.card.id,
            seasonId: season.id,
            next: detail.card.watched ? null : detail.nextEpisode,
            firstSeason: season.id == detail.seasons.first.id,
            padding: _bodyPadding,
            onPlay: (e) => context.push(
              '/item/${detail.card.id}/episode/${e.id}?season=${season.id}',
            ),
          ),
        ],
        // 更多类似：空列表和加载失败都不占位，不影响详情页其余部分
        if (detail != null)
          SliverToBoxAdapter(
            child: RelatedRow(itemId: detail.card.id, padding: _bodyPadding),
          ),
        SliverToBoxAdapter(
          child: SizedBox(
            height:
                CinemaSpacing.section + MediaQuery.paddingOf(context).bottom,
          ),
        ),
      ],
    );
  }

  /// 在线字幕下载成功后立刻选中它，并重新拉详情拿到带 source 的字幕列表。
  /// 刷新失败时保留当前页面，下次打开详情会重新加载。
  Future<void> _onSubtitleAdded(int itemId, MediaSubtitle subtitle) async {
    if (!mounted) return;
    setState(() => _subtitleId = subtitle.id);
    await ref
        .refresh(mediaDetailProvider(itemId).future)
        .then<void>((_) {}, onError: (Object _) {});
  }

  MediaVersion? _selectedVersion(MediaDetail detail) =>
      detail.versions.where((v) => v.id == _versionId).firstOrNull ??
      detail.versions.firstOrNull;

  /// 剧集没有可播的集时不能播放
  bool _canPlay(MediaDetail? detail) =>
      detail != null &&
      (detail.card.kind == MediaKind.movie || detail.nextEpisode != null);

  /// 电影播放选中的版本；剧集播放接着看的一集，版本由服务端选。
  Future<void> _playMain(MediaDetail detail) => playMedia(
    context,
    detail,
    version: _selectedVersion(detail),
    subtitleId: _subtitleId,
  );
}

/// 宽屏页头右侧的信息栏（照搬 Plex）：类型、制作公司、国家 / 地区、上映日期，
/// 每项「小标题 + 内容」，没有数据的项不出。导演、编剧在下面的演职人员片架里，这里不重复。
class _Facts extends StatelessWidget {
  const _Facts({required this.detail});

  final MediaDetail detail;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final facts = [
      ('类型', detail.card.genres.join(' / ')),
      ('制作公司', detail.studios.take(3).join('、')),
      ('国家 / 地区', detail.countries.join('、')),
      ('上映日期', detail.releaseDate ?? ''),
    ].where((fact) => fact.$2.isNotEmpty).toList();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final (label, value) in facts) ...[
          Text(label, style: textTheme.labelSmall),
          const SizedBox(height: 2),
          Text(
            value,
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
            style: textTheme.bodyMedium!.copyWith(color: CinemaColors.text1),
          ),
          const SizedBox(height: CinemaSpacing.heading),
        ],
      ],
    );
  }
}

class _Info extends StatelessWidget {
  const _Info({
    required this.card,
    required this.detail,
    required this.state,
    required this.onRetry,
    required this.versionId,
    required this.subtitleId,
    required this.onVersion,
    required this.onSubtitle,
    required this.onSubtitleAdded,
    required this.onPlay,
    required this.admin,
  });

  final MediaCard card;
  final MediaDetail? detail;
  final AsyncValue<MediaDetail> state;
  final VoidCallback onRetry;
  final int? versionId;
  final int? subtitleId;
  final ValueChanged<int?> onVersion;
  final ValueChanged<int?> onSubtitle;
  final Future<void> Function(MediaSubtitle subtitle) onSubtitleAdded;
  final VoidCallback? onPlay;
  final bool admin;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final detail = this.detail;
    final original = card.originalTitle;
    final count = detail?.episodeCount ?? 0;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          card.title,
          style: textTheme.headlineMedium!.copyWith(
            color: CinemaColors.text0,
            fontWeight: FontWeight.w700,
          ),
        ),
        if (original != null && original != card.title)
          Text(original, style: textTheme.bodySmall),
        const SizedBox(height: 6),
        DetailMeta(
          parts: [
            if (card.year != null) '${card.year}',
            if (card.genres.isNotEmpty) card.genres.take(2).join(' / '),
            if (card.kind == MediaKind.series && count > 0) '$count 集',
            if (card.kind == MediaKind.movie && card.runtime != null)
              formatMinutes(card.runtime!),
          ],
          certification: detail?.contentRating,
          rating: card.rating,
          ratings: detail?.ratings ?? const [],
        ),
        if (state.hasError && detail == null)
          Padding(
            padding: const EdgeInsets.only(top: CinemaSpacing.section),
            child: ErrorRetry(
              message: ApiError.from(state.error!).message,
              onRetry: onRetry,
            ),
          )
        else if (detail == null)
          const DetailBodySkeleton()
        else ...[
          if (detail.overview case final overview?
              when overview.isNotEmpty) ...[
            const SizedBox(height: CinemaSpacing.heading),
            ExpandableText(
              overview,
              style: textTheme.bodyMedium!.copyWith(color: CinemaColors.text1),
            ),
          ],
          const SizedBox(height: CinemaSpacing.heading + 4),
          DetailActions(
            play: _PlayButton(detail: detail, onPressed: onPlay),
            // Plex 的顺序：播放 · 已看 · 稍后观看 · 编辑（仅管理员）· ⋯（所有人）
            buttons: [
              // 没有能播的版本时播放按钮是禁用的，默认焦点交给已看按钮
              _WatchedButton(card: detail.card, autofocus: onPlay == null),
              // 「稍后观看」只用于电影和整部剧集，详情页两种情况都有
              WatchlistButton(
                id: detail.card.id,
                watchlisted: detail.watchlisted,
              ),
              if (admin) _EditButton(card: detail.card),
              _MoreButton(detail: detail),
            ],
          ),
          if (detail.versions.isNotEmpty) ...[
            const SizedBox(height: CinemaSpacing.section),
            MediaStreams(
              versions: detail.versions,
              versionId: versionId,
              subtitleId: subtitleId,
              onVersion: onVersion,
              onSubtitle: onSubtitle,
              onSubtitleAdded: onSubtitleAdded,
              autoVersion: false,
            ),
          ],
        ],
      ],
    );
  }
}

/// 「⋯」：所有人都能用。普通用户是收藏、加入播放列表、获取信息；
/// 管理员另有修正匹配、刷新元数据、删除等，和海报长按是同一套菜单。
class _MoreButton extends ConsumerWidget {
  const _MoreButton({required this.detail});

  final MediaDetail detail;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return DetailMoreButton(
      onPressed: () => showItemActions(
        context,
        ref,
        detail.card,
        detail: detail,
        inDetail: true,
      ),
    );
  }
}

/// 管理员的编辑铅笔，打开条目编辑页（和「修正匹配」同一个入口）。
class _EditButton extends StatelessWidget {
  const _EditButton({required this.card});

  final MediaCard card;

  @override
  Widget build(BuildContext context) {
    return IconButton.filledTonal(
      tooltip: '编辑',
      iconSize: 24,
      style: IconButton.styleFrom(fixedSize: const Size.square(44)),
      onPressed: () => context.push('/settings/manage/items/${card.id}'),
      icon: const Icon(Icons.edit_outlined),
    );
  }
}

/// 整部标记已看 / 未看。剧集是全部集一起改。
class _WatchedButton extends ConsumerStatefulWidget {
  const _WatchedButton({required this.card, this.autofocus = false});

  final MediaCard card;
  final bool autofocus;

  @override
  ConsumerState<_WatchedButton> createState() => _WatchedButtonState();
}

class _WatchedButtonState extends ConsumerState<_WatchedButton> {
  bool _busy = false;

  Future<void> _toggle() async {
    setState(() => _busy = true);
    await markWatched(
      context,
      ref,
      scope: WatchedScope.item,
      id: widget.card.id,
      watched: !widget.card.watched,
      itemId: widget.card.id,
    );
    if (mounted) setState(() => _busy = false);
  }

  @override
  Widget build(BuildContext context) {
    return WatchedToggleButton(
      watched: widget.card.watched,
      busy: _busy,
      autofocus: widget.autofocus,
      onPressed: _toggle,
    );
  }
}

/// 主按钮：剧集写明要播的那一集。看到一半时改叫「继续播放」，下方显示进度。
class _PlayButton extends StatelessWidget {
  const _PlayButton({required this.detail, required this.onPressed});

  final MediaDetail detail;

  /// 为空时禁用：剧集还没有可播的集
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    final next = detail.nextEpisode;
    final progress = next != null ? next.progress : detail.progress;
    final verb = progress == null ? '播放' : '继续播放';
    return DetailPlayButton(
      label: next == null
          ? verb
          : '$verb ${episodeLabel(detail, next.seasonNumber, next.number)}',
      progress: progress,
      onPressed: onPressed,
    );
  }
}
