import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/shell.dart';
import '../../app/theme.dart';
import '../../app/tv.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/focus_ring.dart';
import 'art_image.dart';
import 'browse_api.dart';
import '../../core/widgets/empty_state.dart';

/// 资料库一级入口。首页仍保留各库的最近添加，但用户不必先回首页找入口。
class LibrariesPage extends ConsumerWidget {
  const LibrariesPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final value = ref.watch(librariesProvider);
    final wide = MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint;
    final gutter = CinemaSpacing.gutterFor(wide: wide);
    return Scaffold(
      appBar: AppBar(title: const Text('资料库')),
      body: AsyncView(
        value: value,
        onRetry: () => ref.invalidate(librariesProvider),
        builder: (libraries) {
          if (libraries.isEmpty) {
            return const EmptyState(
              icon: Icons.video_library_outlined,
              title: '还没有可见的资料库',
              message: '管理员开放资料库后，影片会出现在这里',
            );
          }
          return ListView.separated(
            // 左右边距给行内（同管理列表），聚焦环铺满整行
            padding: const EdgeInsets.only(top: 8, bottom: 24),
            itemCount: libraries.length,
            separatorBuilder: (_, _) => const Divider(height: 1),
            itemBuilder: (context, index) {
              final library = libraries[index];
              return FocusRing(
                child: ListTile(
                  // 遥控器进来落在第一个资料库上；手机不抢焦点
                  autofocus: index == 0 && isTelevision(context),
                  contentPadding: EdgeInsets.symmetric(horizontal: gutter),
                  leading: library.cover.isEmpty
                      ? Icon(
                          library.kind == MediaKind.series
                              ? Icons.tv_outlined
                              : Icons.movie_outlined,
                        )
                      : ClipRRect(
                          borderRadius: BorderRadius.circular(6),
                          child: SizedBox(
                            width: 96,
                            height: 54,
                            child: ArtImage(art: library.cover),
                          ),
                        ),
                  title: Text(library.name),
                  subtitle: Text('${library.itemCount} 部'),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => context.go('/libraries/${library.id}'),
                ),
              );
            },
          );
        },
      ),
    );
  }
}
