import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/shell.dart';
import '../../app/theme.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/empty_state.dart';
import 'browse_api.dart';
import 'library_hub_shelf.dart';

/// 资料库「推荐」标签：本库的推荐行，空行由服务端去掉。
class LibraryRecommendTab extends ConsumerWidget {
  const LibraryRecommendTab({
    super.key,
    required this.libraryId,
    required this.onOpenHub,
  });

  final int libraryId;
  final ValueChanged<HubRow> onOpenHub;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final value = ref.watch(libraryHubsProvider(libraryId));
    final wide = MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint;
    final gutter = CinemaSpacing.gutterFor(wide: wide);
    final posterWidth = wide ? 140.0 : 104.0;
    return AsyncView(
      value: value,
      onRetry: () => ref.invalidate(libraryHubsProvider(libraryId)),
      builder: (rows) {
        if (rows.isEmpty) {
          return const EmptyState(
            icon: Icons.auto_awesome_outlined,
            title: '还没有推荐内容',
            message: '资料库里有条目后，继续观看、最近添加、评分最高和类型行会出现在这里',
          );
        }
        return RefreshIndicator(
          onRefresh: () => ref.refresh(libraryHubsProvider(libraryId).future),
          child: ListView.builder(
            physics: const AlwaysScrollableScrollPhysics(),
            padding: const EdgeInsets.only(bottom: 32),
            itemCount: rows.length,
            itemBuilder: (context, index) => HubShelfView(
              hub: rows[index],
              posterWidth: posterWidth,
              gutter: gutter,
              heroPrefix: 'hub:$libraryId:${rows[index].kind}:$index',
              autofocus: index == 0,
              // 继续观看没有对应的海报墙筛选，不给「全部」
              onAll: rows[index].kind == 'continue'
                  ? null
                  : () => onOpenHub(rows[index]),
            ),
          ),
        );
      },
    );
  }
}

/// 推荐行右上角「全部」进入的筛选：最近添加、最近上映、评分最高和类型行
/// 都切到「资料库」标签并带上对应的排序或类型。
LibraryQuery queryForHub(int libraryId, HubRow hub) => switch (hub.kind) {
  'released' => LibraryQuery(libraryId: libraryId, sort: SortKey.release),
  'rating' => LibraryQuery(libraryId: libraryId, sort: SortKey.rating),
  'genre' => LibraryQuery(libraryId: libraryId, genre: hub.arg),
  _ => LibraryQuery(libraryId: libraryId),
};
