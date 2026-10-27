import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/section_title.dart';
import 'admin_widgets.dart';
import 'library_advanced_api.dart';

/// 资料库统计。第 ⑤ 批会整体移到「状态 › 仪表盘」。
class LibraryAdvancedPage extends ConsumerWidget {
  const LibraryAdvancedPage({super.key, required this.id, required this.name});

  final int id;
  final String name;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final text = Theme.of(context).textTheme;
    return Scaffold(
      appBar: AppBar(
        title: Text('$name · 资料库统计'),
        actions: [
          IconButton(
            tooltip: '刷新',
            onPressed: () {
              ref.invalidate(libraryAdvancedStatsProvider(id));
            },
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: AdminListWidth(
        builder: (padding) => ListView(
          padding: padding,
          children: [
            const SectionTitle('资料库统计', first: true),
            const SizedBox(height: CinemaSpacing.heading - 4),
            Padding(
              padding: const EdgeInsets.symmetric(
                horizontal: CinemaSpacing.gutter,
              ),
              child: AsyncView(
                value: ref.watch(libraryAdvancedStatsProvider(id)),
                onRetry: () => ref.invalidate(libraryAdvancedStatsProvider(id)),
                builder: (value) => Card(
                  margin: EdgeInsets.zero,
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Wrap(
                      spacing: 24,
                      runSpacing: 12,
                      children: [
                        _Stat('条目', value['items']),
                        _Stat('电影', value['movies']),
                        _Stat('剧集', value['series']),
                        _Stat('单集', value['episodes']),
                        _Stat('未匹配', value['unmatched']),
                        _Stat('失效', value['missing']),
                      ],
                    ),
                  ),
                ),
              ),
            ),
            const SizedBox(height: CinemaSpacing.section),
            Padding(
              padding: const EdgeInsets.symmetric(
                horizontal: CinemaSpacing.gutter,
              ),
              child: Text(
                '资料库行为、下载器和推荐行在「编辑资料库」里设置。',
                style: text.bodySmall?.copyWith(color: CinemaColors.text2),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat(this.label, this.value);

  final String label;
  final Object? value;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text('${value ?? 0}', style: Theme.of(context).textTheme.titleLarge),
      Text(label, style: Theme.of(context).textTheme.bodySmall),
    ],
  );
}
