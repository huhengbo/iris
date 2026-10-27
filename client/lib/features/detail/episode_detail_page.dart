import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/widgets/rail_scroller.dart';
import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/format.dart';
import '../../core/providers.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/expandable_text.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/focus_tile.dart';
import '../../core/widgets/picker_sheet.dart';
import '../../core/widgets/snack.dart';
import '../auth/auth_controller.dart';
import '../favorites/favorite_button.dart';
import '../favorites/favorites_api.dart';
import '../library/art_image.dart';
import '../library/browse_api.dart';
import '../library/poster_card.dart';
import '../play/play_api.dart';
import '../play/play_launcher.dart';
import '../play/playlist_actions.dart';
import '../subtitles/edit_subtitles_dialog.dart';
import 'detail_page.dart';
import 'episode_admin.dart';
import 'people_row.dart';
import 'stream_rows.dart';

part 'episode_season_strip.dart';

/// 单集详情，和剧集详情是同一套页头（见 [DetailHeader]）：单集剧照铺满顶部渐隐
/// （宽屏时整部剧的海报在左）→ 剧名、集名与元数据 → 简介两行 → 播放和操作
/// → 版本、画面、音频、字幕 → 演职人员 → 本季其他集。
class EpisodeDetailPage extends ConsumerStatefulWidget {
  const EpisodeDetailPage({
    super.key,
    required this.itemId,
    required this.seasonId,
    required this.episodeId,
  });

  final int itemId;
  final int seasonId;
  final int episodeId;

  @override
  ConsumerState<EpisodeDetailPage> createState() => _EpisodeDetailPageState();
}

class _EpisodeDetailPageState extends ConsumerState<EpisodeDetailPage> {
  /// 为空时由服务端选版本
  int? _versionId;

  /// 为空时按默认字幕语言自动选
  int? _subtitleId;
  bool _busy = false;

  void _retry() => ref
    ..invalidate(mediaDetailProvider(widget.itemId))
    ..invalidate(seasonProvider(widget.seasonId));

  @override
  Widget build(BuildContext context) {
    final detail = ref.watch(mediaDetailProvider(widget.itemId));
    final season = ref.watch(seasonProvider(widget.seasonId));
    return Scaffold(
      body: Stack(
        children: [
          LayoutBuilder(
            builder: (context, constraints) {
              final width = constraints.maxWidth;
              return switch ((detail, season)) {
                (
                  AsyncData(value: final media),
                  AsyncData(value: final season),
                ) =>
                  _body(width, media, season),
                (AsyncError(:final error), _) ||
                (_, AsyncError(:final error)) => _placeholder(
                  width,
                  failed: true,
                  child: ErrorRetry(
                    message: ApiError.from(error).message,
                    onRetry: _retry,
                  ),
                ),
                // 和剧集详情一样：剧照和正文都是骨架
                _ => _placeholder(width, child: const DetailBodySkeleton()),
              };
            },
          ),
          const DetailBackButton(),
        ],
      ),
    );
  }

  /// 加载中或出错时的页头，版面和加载完一样，内容不跳
  Widget _placeholder(
    double width, {
    bool failed = false,
    required Widget child,
  }) {
    return CustomScrollView(
      slivers: [
        SliverToBoxAdapter(
          child: DetailHeader(
            width: width,
            backdrop: DetailBackdrop(art: null, failed: failed),
            child: child,
          ),
        ),
      ],
    );
  }

  Widget _body(double width, MediaDetail media, SeasonDetail season) {
    final index = season.episodes.indexWhere((e) => e.id == widget.episodeId);
    if (index < 0) {
      return EmptyState(
        icon: Icons.movie_outlined,
        title: '这一集不存在',
        message: '可能已经删除，或者移到了其他季。',
        action: FilledButton.tonal(
          autofocus: true,
          onPressed: () => context.go('/item/${widget.itemId}'),
          child: const Text('查看整部剧'),
        ),
      );
    }
    final episode = season.episodes[index];
    final padding = detailBodyPadding(context, width);
    final onPlay = episode.versions.isEmpty
        ? null
        : () => _play(media, season, episode);
    final still = episode.still;
    final backdrop = media.card.backdrop;
    return CustomScrollView(
      slivers: [
        SliverToBoxAdapter(
          child: DetailHeader(
            width: width,
            poster: media.card.poster,
            backdrop: DetailBackdrop(
              // 没有单集剧照时用整部剧的背景图，再没有用海报
              art: !still.isEmpty
                  ? still
                  : !backdrop.isEmpty
                  ? backdrop
                  : media.card.poster,
            ),
            child: _Info(
              media: media,
              season: season,
              episode: episode,
              versionId: _versionId,
              subtitleId: _subtitleId,
              busy: _busy,
              admin: ref.watch(authControllerProvider).value?.isAdmin ?? false,
              onPlay: onPlay,
              onToggleWatched: () => _toggleWatched(episode),
              onVersion: (id) => setState(() {
                _versionId = id;
                _subtitleId = null;
              }),
              onSubtitle: (id) => setState(() => _subtitleId = id),
              onSubtitleAdded: _onSubtitleAdded,
              onMore: () => _more(episode),
            ),
          ),
        ),
        if (media.directors.isNotEmpty ||
            media.writers.isNotEmpty ||
            media.cast.isNotEmpty)
          SliverToBoxAdapter(
            child: PeopleRow(
              directors: media.directors,
              writers: media.writers,
              cast: media.cast,
              padding: padding,
            ),
          ),
        if (season.episodes.length > 1)
          SliverToBoxAdapter(
            child: _SeasonStrip(
              season: season,
              current: index,
              padding: padding,
              onTap: (e) => context.replace(
                '/item/${widget.itemId}/episode/${e.id}?season=${season.id}',
              ),
            ),
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

  /// 在线字幕下载成功后选中它，并重新拉剧集和本季数据，让新的字幕出现在列表里。
  Future<void> _onSubtitleAdded(MediaSubtitle subtitle) async {
    if (!mounted) return;
    setState(() => _subtitleId = subtitle.id);
    await Future.wait([
      ref.refresh(mediaDetailProvider(widget.itemId).future),
      ref.refresh(seasonProvider(widget.seasonId).future),
    ]).then<void>((_) {}, onError: (Object _) {});
  }

  /// 版本和字幕在页面上已经选好，播放器选择里不再重复选版本；
  /// 版本只在签发失败时作为备选。
  Future<void> _play(MediaDetail media, SeasonDetail season, Episode episode) {
    final versionId = _versionId;
    return play(
      context,
      PlayRequest(
        itemId: media.card.id,
        target: versionId == null
            ? PlayTarget.episode(episode.id)
            : PlayTarget.version(versionId),
        subtitle: [
          media.card.title,
          formatEpisode(season.number, episode.number),
          ?episode.versions.where((v) => v.id == versionId).firstOrNull?.label,
        ].join(' · '),
        recoveryVersions: episode.versions,
        subtitleId: _subtitleId,
      ),
    );
  }

  Future<void> _toggleWatched(Episode episode) async {
    setState(() => _busy = true);
    try {
      await setWatched(
        ref.read(apiClientProvider),
        WatchedScope.episode,
        episode.id,
        watched: !episode.watched,
      );
      ref
        ..invalidate(homeFeedProvider)
        ..invalidate(seasonProvider(widget.seasonId))
        ..invalidate(mediaDetailProvider(widget.itemId));
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// 管理员的「⋯」：和剧集详情的「⋯」同样的弹层和写法，只列单集能做的操作，
  /// 顺序也一样（编辑 → 刷新元数据 → 删除）。
  Future<void> _more(Episode episode) async {
    final action = await showPickerSheet<String>(
      context,
      builder: (context) => Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          PickerHeader(
            title: '更多操作',
            subtitle: episode.name ?? '第 ${episode.number} 集',
          ),
          for (final (i, (value, icon, label)) in const [
            ('edit', Icons.edit_outlined, '编辑'),
            ('subtitles', Icons.subtitles_outlined, '编辑字幕'),
            ('refresh', Icons.refresh, '刷新元数据'),
            ('delete', Icons.delete_outline, '删除单集'),
          ].indexed)
            PickerOption(
              autofocus: i == 0,
              icon: icon,
              title: label,
              onTap: () => Navigator.pop(context, value),
            ),
        ],
      ),
    );
    if (!mounted) return;
    switch (action) {
      case 'edit':
        await editEpisode(
          context,
          ref,
          episode: episode,
          seasonId: widget.seasonId,
        );
      case 'subtitles':
        await showEditSubtitlesDialog(
          context,
          title: episode.name ?? '第 ${episode.number} 集',
          versions: episode.versions,
          onChanged: () {
            ref
              ..invalidate(seasonProvider(widget.seasonId))
              ..invalidate(mediaDetailProvider(widget.itemId));
          },
        );
      case 'refresh':
        await refreshEpisodeMetadata(
          context,
          ref,
          episodeId: episode.id,
          seasonId: widget.seasonId,
        );
      case 'delete':
        final deleted = await deleteEpisodeFromDetail(
          context,
          ref,
          episode: episode,
          itemId: widget.itemId,
          seasonId: widget.seasonId,
        );
        // 直接打开的单集页没有上一页，回到剧集详情
        if (!deleted || !mounted) return;
        context.canPop() ? context.pop() : context.go('/item/${widget.itemId}');
    }
  }
}

class _Info extends StatelessWidget {
  const _Info({
    required this.media,
    required this.season,
    required this.episode,
    required this.versionId,
    required this.subtitleId,
    required this.busy,
    required this.admin,
    required this.onPlay,
    required this.onToggleWatched,
    required this.onVersion,
    required this.onSubtitle,
    required this.onSubtitleAdded,
    required this.onMore,
  });

  final MediaDetail media;
  final SeasonDetail season;
  final Episode episode;
  final int? versionId;
  final int? subtitleId;
  final bool busy;
  final bool admin;
  final VoidCallback? onPlay;
  final VoidCallback onToggleWatched;
  final ValueChanged<int?> onVersion;
  final ValueChanged<int?> onSubtitle;
  final Future<void> Function(MediaSubtitle subtitle) onSubtitleAdded;
  final VoidCallback onMore;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final name = episode.name;
    final airDate = DateTime.tryParse(episode.airDate ?? '');
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        // 剧名点回整部剧的详情
        Align(
          alignment: Alignment.centerLeft,
          child: FocusRing(
            radius: CinemaRadius.small,
            child: InkWell(
              borderRadius: BorderRadius.circular(CinemaRadius.small),
              onTap: () => context.push('/item/${media.card.id}'),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Flexible(
                    child: Text(
                      media.card.title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: textTheme.titleSmall!.copyWith(
                        color: CinemaColors.text1,
                      ),
                    ),
                  ),
                  const Icon(Icons.chevron_right, color: CinemaColors.text2),
                ],
              ),
            ),
          ),
        ),
        Text(
          name == null || name.isEmpty ? '第 ${episode.number} 集' : name,
          style: textTheme.headlineMedium!.copyWith(
            color: CinemaColors.text0,
            fontWeight: FontWeight.w700,
          ),
        ),
        const SizedBox(height: 6),
        DetailMeta(
          parts: [
            formatEpisode(season.number, episode.number),
            if (airDate != null) formatDate(airDate),
            if (episode.runtime case final runtime?) formatMinutes(runtime),
          ],
          certification: media.contentRating,
          rating: episode.rating,
          trailing: _endsAt(episode),
        ),
        if (episode.overview case final overview? when overview.isNotEmpty) ...[
          const SizedBox(height: CinemaSpacing.heading),
          ExpandableText(
            overview,
            style: textTheme.bodyMedium!.copyWith(color: CinemaColors.text1),
          ),
        ],
        const SizedBox(height: CinemaSpacing.heading + 4),
        DetailActions(
          play: DetailPlayButton(
            label: episode.progress == null ? '播放' : '继续播放',
            progress: episode.progress,
            onPressed: onPlay,
          ),
          buttons: [
            // 没有能播的文件时播放按钮是禁用的，默认焦点交给已看按钮
            WatchedToggleButton(
              watched: episode.watched,
              busy: busy,
              autofocus: onPlay == null,
              onPressed: onToggleWatched,
            ),
            FavoriteButton(
              kind: FavoriteKind.episode,
              id: episode.id,
              favorite: episode.favorite,
              itemId: media.card.id,
              seasonId: season.id,
            ),
            PlaylistButton(itemId: media.card.id, episodeId: episode.id),
            if (admin) DetailMoreButton(onPressed: onMore),
          ],
        ),
        if (episode.versions.isNotEmpty) ...[
          const SizedBox(height: CinemaSpacing.section),
          MediaStreams(
            versions: episode.versions,
            versionId: versionId,
            subtitleId: subtitleId,
            onVersion: onVersion,
            onSubtitle: onSubtitle,
            onSubtitleAdded: onSubtitleAdded,
          ),
        ],
      ],
    );
  }
}

/// 现在开始看几点结束，参考 Emby；看到一半时按剩下的时长算。
String? _endsAt(Episode episode) {
  final progress = episode.progress;
  final runtime = episode.runtime;
  final left = progress != null
      ? progress.duration - progress.position
      : runtime == null
      ? null
      : Duration(minutes: runtime);
  if (left == null || left <= Duration.zero) return null;
  final end = DateTime.now().add(left);
  String two(int n) => n.toString().padLeft(2, '0');
  return '${two(end.hour)}:${two(end.minute)} 结束';
}
