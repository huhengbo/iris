import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/shell.dart' show wideLayoutBreakpoint;
import '../../core/widgets/rail_scroller.dart';
import '../../app/theme.dart';
import '../../core/provider_cache.dart';
import '../../core/providers.dart';
import '../../core/widgets/skeleton.dart';
import '../library/browse_api.dart';
import '../library/poster_card.dart';

/// 更多类似一行最多几条，和后端的默认值一致。
const relatedLimit = 20;

/// 详情页底部的「更多类似」。服务端已按可见范围过滤，按类型重合度和评分排好序。
final relatedProvider = FutureProvider.autoDispose.family<List<MediaCard>, int>(
  (ref, itemId) async {
    keepProviderAlive(ref);
    final baseUrl = ref.watch(apiBaseUrlProvider);
    final response = await ref
        .watch(apiClientProvider)
        .get<List<dynamic>>(
          '/api/items/$itemId/related',
          queryParameters: {'limit': relatedLimit},
        );
    return [
      for (final item in response.data ?? const <dynamic>[])
        MediaCard.fromJson(item as Map<String, dynamic>, baseUrl),
    ];
  },
);

/// 详情页底部的「更多类似」海报片架：空列表整个不显示，加载失败也不影响详情页，
/// 只在这一块给出说明和重试。
class RelatedRow extends ConsumerWidget {
  const RelatedRow({super.key, required this.itemId, required this.padding});

  final int itemId;

  /// 正文的左右边距，片架和上面的内容对齐
  final EdgeInsets padding;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final related = ref.watch(relatedProvider(itemId));
    final items = related.value;
    if (items == null) {
      return related.hasError
          ? _failed(context, () => ref.invalidate(relatedProvider(itemId)))
          : _skeleton(context);
    }
    if (items.isEmpty) return const SizedBox.shrink();
    final textTheme = Theme.of(context).textTheme;
    final wide = MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint;
    final cardWidth = wide ? 168.0 : 132.0;
    final textHeight = MediaQuery.textScalerOf(context)
        .scale(PosterCard.textLines(subtitle: false).single);
    return Padding(
      padding: EdgeInsets.only(top: CinemaSpacing.section),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: EdgeInsets.fromLTRB(
              padding.left,
              0,
              padding.right,
              CinemaSpacing.heading,
            ),
            child: Semantics(
              header: true,
              child: Text(
                '更多类似',
                style: textTheme.titleMedium!.copyWith(
                  color: CinemaColors.text0,
                ),
              ),
            ),
          ),
          Padding(
            // 片架和正文同宽，不伸到宽屏右侧的空白里
            padding: EdgeInsets.only(left: padding.left, right: padding.right),
            child: SizedBox(
              // 聚焦放大和阴影都要留出位置，片架本身不裁切
              height: cardWidth * 1.5 + 8 + textHeight + 24,
              child: RailScroller(
                builder: (context, controller) => ListView.separated(
                  controller: controller,
                  scrollDirection: Axis.horizontal,
                  clipBehavior: Clip.none,
                  padding: const EdgeInsets.symmetric(vertical: 12),
                  itemCount: items.length,
                  separatorBuilder: (_, _) => SizedBox(width: wide ? 20 : 12),
                  itemBuilder: (context, i) {
                    final media = items[i];
                    return SizedBox(
                      width: cardWidth,
                      child: PosterCard(
                        media: media,
                        heroTag: 'related:$itemId:${media.id}',
                      ),
                    );
                  },
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  /// 加载失败：只在这一块说明，详情页其余内容照常。
  Widget _failed(BuildContext context, VoidCallback onRetry) => Padding(
    padding: EdgeInsets.fromLTRB(
      padding.left,
      CinemaSpacing.heading,
      padding.right,
      0,
    ),
    child: Row(
      children: [
        Expanded(
          child: Text(
            '更多类似没有加载出来',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ),
        TextButton(onPressed: onRetry, child: const Text('重试')),
      ],
    ),
  );

  /// 加载中：标题 + 一行的海报骨架，避免内容跳出来时页面跳动。
  Widget _skeleton(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final wide = MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint;
    final cardWidth = wide ? 168.0 : 132.0;
    return Padding(
      padding: EdgeInsets.only(top: CinemaSpacing.section),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: EdgeInsets.fromLTRB(
              padding.left,
              0,
              padding.right,
              CinemaSpacing.heading,
            ),
            child: Text(
              '更多类似',
              style: textTheme.titleMedium!.copyWith(color: CinemaColors.text0),
            ),
          ),
          Padding(
            padding: EdgeInsets.only(left: padding.left, right: padding.right),
            child: SizedBox(
              height: cardWidth * 1.5 + 8 + 20 + 24,
              child: ListView.separated(
                scrollDirection: Axis.horizontal,
                physics: const NeverScrollableScrollPhysics(),
                padding: const EdgeInsets.symmetric(vertical: 12),
                itemCount: wide ? 4 : 3,
                separatorBuilder: (_, _) => SizedBox(width: wide ? 20 : 12),
                itemBuilder: (_, _) => Semantics(
                  label: '正在加载',
                  child: SizedBox(
                    width: cardWidth,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        AspectRatio(
                          aspectRatio: 2 / 3,
                          child: Skeleton(
                            borderRadius: BorderRadius.circular(
                              CinemaRadius.poster,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
