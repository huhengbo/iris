import 'dart:ui' show lerpDouble;

import 'package:cached_network_image/cached_network_image.dart';
import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter/material.dart';
import 'package:flutter_cache_manager/flutter_cache_manager.dart';

import '../../app/motion.dart';
import '../../app/theme.dart';
import '../../core/api/paging.dart';
import '../../core/widgets/focus_tile.dart';
import '../../core/widgets/skeleton.dart';

/// 图片的磁盘缓存。图片地址带内容哈希、服务端给一年的缓存期，重启 App 也不用重新下载；
/// 默认只存 200 张，不够一个片库的海报墙。网页端由浏览器缓存负责。
final _artCache = CacheManager(
  Config(
    'iris_art',
    stalePeriod: const Duration(days: 60),
    maxNrOfCacheObjects: 3000,
  ),
);

/// 网页端用 Flutter 自带的 [NetworkImage]：cached_network_image 的图片流在没人看后
/// 再被订阅时会把单帧图重新解码一遍，网页引擎里新旧两张图共用同一个 `<img>`，
/// 旧图释放时把它清空，新图就成了黑块（横滑出去再滑回来的海报全黑）。
ImageProvider<Object> _artProvider(String url) => kIsWeb
    ? NetworkImage(url)
    : CachedNetworkImageProvider(url, cacheManager: _artCache);

/// 海报、剧照和集缩略图。铺满父级尺寸，按显示宽度挑一档图片：
/// 加载中显示骨架，失败或没有图时显示占位。
class ArtImage extends StatelessWidget {
  const ArtImage({
    super.key,
    required this.art,
    this.icon = Icons.movie_outlined,
  });

  final Artwork art;

  /// 没有图时占位的图标
  final IconData icon;

  @override
  Widget build(BuildContext context) {
    if (art.isEmpty) return _ArtPlaceholder(icon: icon);
    return LayoutBuilder(
      builder: (context, constraints) {
        final width = constraints.maxWidth.isFinite
            ? constraints.maxWidth
            : MediaQuery.sizeOf(context).width;
        final pixelWidth = (width * MediaQuery.devicePixelRatioOf(context))
            .round();
        final url = art.pick(pixelWidth.toDouble())!;
        return Image(
          image: ResizeImage.resizeIfNeeded(
            pixelWidth,
            null,
            _artProvider(url),
          ),
          fit: BoxFit.cover,
          width: double.infinity,
          height: double.infinity,
          gaplessPlayback: true,
          frameBuilder: (context, child, frame, synchronous) {
            if (synchronous) return child;
            return _ArtFrameLayer(loaded: frame != null, child: child);
          },
          loadingBuilder: (context, child, progress) {
            if (progress == null) return child;
            if (child is _ArtFrameLayer && child.loaded) return child;
            return const Skeleton();
          },
          errorBuilder: (_, _, _) => _ArtPlaceholder(icon: icon),
        );
      },
    );
  }
}

class _ArtFrameLayer extends StatelessWidget {
  const _ArtFrameLayer({required this.loaded, required this.child});

  final bool loaded;
  final Widget child;

  @override
  Widget build(BuildContext context) => AnimatedOpacity(
    opacity: loaded ? 1 : 0,
    duration: Motion.short,
    curve: Motion.emphasized,
    child: child,
  );
}

class _ArtPlaceholder extends StatelessWidget {
  const _ArtPlaceholder({required this.icon});

  final IconData icon;

  @override
  Widget build(BuildContext context) {
    return ColoredBox(
      color: CinemaColors.surface3,
      child: Center(child: Icon(icon, color: CinemaColors.track)),
    );
  }
}

/// 海报/剧照的外框：圆角、银幕阴影和聚焦环。
/// 聚焦：琥珀环 + 加深阴影（放大由 [FocusTile] 负责）；悬停：上浮 4。
class ArtFrame extends StatelessWidget {
  const ArtFrame({
    super.key,
    required this.highlight,
    required this.radius,
    required this.child,
    this.dimmed = false,
    this.badge,
  });

  final TileHighlight highlight;
  final double radius;
  final Widget child;

  /// 已看完的海报压暗。
  final bool dimmed;

  /// 压在右上角、不随画面压暗的标记，如已看的勾
  final Widget? badge;

  @override
  Widget build(BuildContext context) {
    final reduced = Motion.reduced(context);
    final duration = reduced ? Duration.zero : Motion.focus;
    final borderRadius = BorderRadius.circular(radius);
    return AnimatedContainer(
      duration: duration,
      curve: Motion.emphasized,
      transform: Matrix4.translationValues(
        0,
        highlight.hovered && !highlight.focused && !reduced ? -4 : 0,
        0,
      ),
      decoration: BoxDecoration(
        borderRadius: borderRadius,
        boxShadow: highlight.any ? CinemaShadows.focused : CinemaShadows.lift,
      ),
      foregroundDecoration: BoxDecoration(
        borderRadius: borderRadius,
        border: Border.all(
          color: highlight.focused ? CinemaColors.amber : Colors.transparent,
          width: 2,
          strokeAlign: BorderSide.strokeAlignOutside,
        ),
      ),
      child: ClipRRect(
        borderRadius: borderRadius,
        child: Stack(
          fit: StackFit.expand,
          children: [
            child,
            if (dimmed)
              ColoredBox(color: CinemaColors.bg.withValues(alpha: .5)),
            if (badge case final badge?)
              Positioned(top: 6, right: 6, child: badge),
          ],
        ),
      ),
    );
  }
}

/// 海报/剧照的共享元素：飞行途中在两端图片之间交叉淡化（海报 → 详情页剧照），
/// 并按两端圆角插值裁切，避免起止瞬间出现直角/圆角跳变。减少动态效果时不飞。
class ArtHero extends StatelessWidget {
  const ArtHero({
    super.key,
    required this.tag,
    required this.child,
    this.radius = 0,
  });

  final Object tag;
  final Widget child;
  final double radius;

  @override
  Widget build(BuildContext context) {
    return HeroMode(
      enabled: !Motion.reduced(context),
      child: Hero(tag: tag, flightShuttleBuilder: _crossfade, child: child),
    );
  }

  static Widget _crossfade(
    BuildContext flightContext,
    Animation<double> animation,
    HeroFlightDirection direction,
    BuildContext fromContext,
    BuildContext toContext,
  ) {
    final fromHero = fromContext.widget as Hero;
    final toHero = toContext.widget as Hero;
    final fromRadius =
        fromContext.findAncestorWidgetOfExactType<ArtHero>()?.radius ?? 0;
    final toRadius =
        toContext.findAncestorWidgetOfExactType<ArtHero>()?.radius ?? 0;
    // 打开时 animation 0→1，返回时 1→0；详情页那一侧始终随 animation 显隐
    final (
      below,
      above,
      startRadius,
      endRadius,
    ) = direction == HeroFlightDirection.push
        ? (fromHero.child, toHero.child, fromRadius, toRadius)
        : (toHero.child, fromHero.child, toRadius, fromRadius);
    final stack = Stack(
      fit: StackFit.expand,
      children: [
        below,
        FadeTransition(opacity: animation, child: above),
      ],
    );
    if (startRadius == 0 && endRadius == 0) return stack;
    return AnimatedBuilder(
      animation: animation,
      child: stack,
      builder: (context, child) {
        final r = lerpDouble(startRadius, endRadius, animation.value) ?? 0;
        if (r <= 0) return child!;
        return ClipRRect(borderRadius: BorderRadius.circular(r), child: child);
      },
    );
  }
}

/// 3px 进度条：暖白 24% 底轨，琥珀填充。
class ProgressLine extends StatelessWidget {
  const ProgressLine({super.key, required this.value});

  final double value;

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 3,
      clipBehavior: Clip.antiAlias,
      decoration: BoxDecoration(
        color: CinemaColors.track,
        borderRadius: BorderRadius.circular(CinemaRadius.pill),
      ),
      alignment: Alignment.centerLeft,
      child: FractionallySizedBox(
        widthFactor: value.clamp(0, 1),
        heightFactor: 1,
        child: const ColoredBox(color: CinemaColors.amber),
      ),
    );
  }
}
