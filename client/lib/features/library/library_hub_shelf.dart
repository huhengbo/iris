import 'package:flutter/material.dart';

import '../../core/widgets/rail_scroller.dart';
import '../../app/theme.dart';
import '../../app/tv.dart';
import '../../core/format.dart';
import 'browse_api.dart';
import 'poster_card.dart';

/// 资料库「推荐」标签的一行：标题 + 可横滑的海报片架。
/// 继续观看行的片名下面写播到哪一集和剩余时间，其余行不写副标题。
class HubShelfView extends StatelessWidget {
  const HubShelfView({
    super.key,
    required this.hub,
    required this.posterWidth,
    required this.gutter,
    required this.heroPrefix,
    this.onAll,
    this.autofocus = false,
  });

  final HubRow hub;
  final double posterWidth;
  final double gutter;
  final String heroPrefix;

  /// 右上角「全部」；为空时不显示
  final VoidCallback? onAll;

  /// 遥控器进来落在第一行的第一张海报上
  final bool autofocus;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final scaler = MediaQuery.textScalerOf(context);
    final media = _covers();
    final text =
        scaler.scale(20) +
        (media.any((m) => _note(m.$2) != null) ? scaler.scale(18) : 0);
    // 上方留出聚焦放大的空间，下方留出阴影
    final height = posterWidth * 3 / 2 + 8 + text.ceilToDouble() + 18;
    return Padding(
      padding: EdgeInsets.fromLTRB(gutter, CinemaSpacing.section, gutter, 0),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            height: 40,
            child: Row(
              children: [
                Expanded(
                  child: Semantics(
                    header: true,
                    child: Text(
                      hub.title,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: textTheme.titleMedium!.copyWith(
                        color: CinemaColors.text0,
                      ),
                    ),
                  ),
                ),
                if (onAll != null)
                  TextButton(
                    onPressed: onAll,
                    style: TextButton.styleFrom(
                      padding: const EdgeInsets.symmetric(horizontal: 8),
                      textStyle: textTheme.labelMedium,
                    ),
                    child: const Row(
                      children: [
                        Text('全部'),
                        Icon(Icons.chevron_right, size: 18),
                      ],
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 4),
          SizedBox(
            height: height,
            child: RailScroller(
              builder: (context, controller) => ListView.separated(
                controller: controller,
                scrollDirection: Axis.horizontal,
                clipBehavior: Clip.none,
                itemCount: media.length,
                separatorBuilder: (_, _) => const SizedBox(width: 12),
                itemBuilder: (context, index) {
                  final (item, play) = media[index];
                  return SizedBox(
                    width: posterWidth,
                    child: PosterCard(
                      autofocus:
                          autofocus && index == 0 && isTelevision(context),
                      media: item,
                      heroTag: '$heroPrefix:${item.id}',
                      subtitle: _note(play),
                    ),
                  );
                },
              ),
            ),
          ),
        ],
      ),
    );
  }

  /// 继续观看行按 [HubRow.recent] 的顺序；其余行直接用 items。
  List<(MediaCard, RecentPlay?)> _covers() {
    if (hub.recent.isEmpty) {
      return [for (final item in hub.items) (item, null)];
    }
    return [
      for (final play in hub.recent)
        if (hub.items.any((item) => item.id == play.item.id)) (play.item, play),
    ];
  }

  String? _note(RecentPlay? play) {
    if (play == null) return null;
    final episode = play.episode;
    final parts = [
      if (episode != null) formatEpisode(episode.seasonNumber, episode.number),
      if (play.progress case final progress?)
        '剩 ${progress.remainingMinutes} 分钟',
    ];
    return parts.isEmpty ? null : parts.join(' · ');
  }
}
