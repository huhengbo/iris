import 'package:flutter/material.dart';

import '../../app/shell.dart' show wideLayoutBreakpoint;
import '../../app/theme.dart';
import '../../app/tv.dart';
import '../../core/widgets/rail_scroller.dart';
import 'browse_api.dart';
import 'continue_watching_card.dart';
import 'poster_card.dart';

/// 资料库「推荐」标签的一行：标题 + 可横滑的海报片架。
/// 继续观看行渲染与首页一致的 16:9 横版剧照卡（带进度条与剩余分钟），其余行渲染海报卡。
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
    final isContinue = hub.recent.isNotEmpty || hub.kind == 'continue';
    final plays = isContinue ? _plays() : const <RecentPlay>[];
    if (isContinue && plays.isEmpty) return const SizedBox.shrink();

    final wide = MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint;
    final stillWidth = wide ? 280.0 : 220.0;
    final textLines = isContinue
        ? ContinueWatchingCard.textLines
        : PosterCard.textLines(subtitle: false);
    final text = textLines.map(scaler.scale).fold(0.0, (sum, h) => sum + h);
    // 上方留出聚焦放大的空间，下方留出阴影
    final artHeight = isContinue ? stillWidth * 9 / 16 : posterWidth * 3 / 2;
    final height = artHeight + 8 + text.ceilToDouble() + 18;
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
                itemCount: isContinue ? plays.length : hub.items.length,
                separatorBuilder: (_, _) => const SizedBox(width: 12),
                itemBuilder: (context, index) {
                  final firstFocus =
                      autofocus && index == 0 && isTelevision(context);
                  if (isContinue) {
                    final play = plays[index];
                    return SizedBox(
                      width: stillWidth,
                      child: ContinueWatchingCard(
                        autofocus: firstFocus,
                        recent: play,
                        heroTag: '$heroPrefix:${play.item.id}',
                      ),
                    );
                  }
                  final item = hub.items[index];
                  return SizedBox(
                    width: posterWidth,
                    child: PosterCard(
                      autofocus: firstFocus,
                      media: item,
                      heroTag: '$heroPrefix:${item.id}',
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

  /// 继续观看行按 [HubRow.recent] 的顺序；没有 recent 时兜底从 items 构造。
  List<RecentPlay> _plays() {
    if (hub.recent.isEmpty) {
      return [for (final item in hub.items) RecentPlay(item: item)];
    }
    if (hub.items.isEmpty) return hub.recent;
    return [
      for (final play in hub.recent)
        if (hub.items.any((item) => item.id == play.item.id)) play,
    ];
  }
}
