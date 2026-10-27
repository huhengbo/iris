part of 'detail_page.dart';

/// 顶部剧照的高度。
double detailArtHeight(double width) => width >= wideLayoutBreakpoint
    ? 460
    : (width * 0.89).clamp(300, 460).toDouble();

/// 正文的左右边距：边距同 gutter，还要让开系统边距（TV 过扫描）；不限宽，片架铺满。
EdgeInsets detailBodyPadding(BuildContext context, double width) {
  final gutter = CinemaSpacing.gutterFor(wide: width >= wideLayoutBreakpoint);
  final safe = MediaQuery.paddingOf(context);
  return EdgeInsets.only(left: gutter + safe.left, right: gutter + safe.right);
}

/// 页头右侧信息栏的宽度和出现的最小屏宽；再窄，文字列和信息栏会互相挤。
const detailAsideWidth = 300.0;
const detailAsideMinWidth = 1280.0;

/// 详情和单集页的页头：剧照铺满顶部，正文从剧照 58% 处压上去；
/// 宽屏时有 [poster] 就放在左边，文字列限宽，屏幕够宽时再在右边放信息栏 [aside]。
class DetailHeader extends StatelessWidget {
  const DetailHeader({
    super.key,
    required this.width,
    required this.backdrop,
    required this.child,
    this.poster = Artwork.none,
    this.aside,
  });

  final double width;
  final Widget backdrop;
  final Artwork poster;
  final Widget child;
  final Widget? aside;

  @override
  Widget build(BuildContext context) {
    final artHeight = detailArtHeight(width);
    final wide = width >= wideLayoutBreakpoint;
    return Stack(
      children: [
        Positioned(
          left: 0,
          right: 0,
          top: 0,
          height: artHeight,
          child: backdrop,
        ),
        Padding(
          padding: detailBodyPadding(
            context,
            width,
          ).copyWith(top: artHeight * 0.58),
          child: !wide
              ? child
              : Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (!poster.isEmpty) ...[
                      SizedBox(width: 220, child: _Poster(art: poster)),
                      const SizedBox(width: CinemaSpacing.gutterWide),
                    ],
                    Flexible(
                      child: ConstrainedBox(
                        constraints: const BoxConstraints(
                          maxWidth: detailTextMaxWidth,
                        ),
                        child: child,
                      ),
                    ),
                    if (aside != null && width >= detailAsideMinWidth) ...[
                      const SizedBox(width: CinemaSpacing.gutterWide + 16),
                      SizedBox(width: detailAsideWidth, child: aside),
                    ],
                  ],
                ),
        ),
      ],
    );
  }
}

/// 宽屏页头左侧的海报，带银幕阴影。
class _Poster extends StatelessWidget {
  const _Poster({required this.art});

  final Artwork art;

  @override
  Widget build(BuildContext context) {
    final radius = BorderRadius.circular(CinemaRadius.poster);
    return AspectRatio(
      aspectRatio: 2 / 3,
      child: DecoratedBox(
        decoration: BoxDecoration(
          borderRadius: radius,
          boxShadow: CinemaShadows.lift,
        ),
        child: ClipRRect(
          borderRadius: radius,
          child: ArtImage(art: art),
        ),
      ),
    );
  }
}

/// 压在剧照上的返回按钮。直接打开的地址没有上一页时回首页。
class DetailBackButton extends StatelessWidget {
  const DetailBackButton({super.key});

  @override
  Widget build(BuildContext context) {
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(8),
        child: IconButton.filled(
          tooltip: '返回',
          style: IconButton.styleFrom(
            fixedSize: const Size.square(44),
            backgroundColor: CinemaColors.scrim,
            foregroundColor: CinemaColors.text0,
          ),
          onPressed: () => context.canPop() ? context.pop() : context.go('/'),
          icon: const Icon(Icons.arrow_back),
        ),
      ),
    );
  }
}

/// 「⋯」：44 圆形 tonal，和已看、稍后观看、编辑并排；所有用户都有。
class DetailMoreButton extends StatelessWidget {
  const DetailMoreButton({super.key, required this.onPressed});

  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) {
    return IconButton.filledTonal(
      tooltip: '更多操作',
      iconSize: 24,
      style: IconButton.styleFrom(fixedSize: const Size.square(44)),
      onPressed: onPressed,
      icon: const Icon(Icons.more_horiz),
    );
  }
}

/// 详情主按钮旁的已看切换：44 圆形 tonal，已看时琥珀实心勾。
class WatchedToggleButton extends StatelessWidget {
  const WatchedToggleButton({
    super.key,
    required this.watched,
    required this.busy,
    required this.onPressed,
    this.autofocus = false,
  });

  final bool watched;
  final bool busy;
  final VoidCallback onPressed;
  final bool autofocus;

  @override
  Widget build(BuildContext context) {
    return IconButton.filledTonal(
      autofocus: autofocus,
      tooltip: watched ? '标记未看' : '标记已看',
      isSelected: watched,
      iconSize: 24,
      style: IconButton.styleFrom(fixedSize: const Size.square(44)),
      // 忙碌时不禁用：禁用的按钮会丢掉遥控器焦点
      onPressed: busy ? () {} : onPressed,
      icon: busy
          ? const SizedBox.square(
              dimension: 20,
              child: CircularProgressIndicator(strokeWidth: 2),
            )
          : const Icon(Icons.check_circle_outline),
      selectedIcon: busy
          ? null
          : const Icon(Icons.check_circle, color: CinemaColors.amber),
    );
  }
}

/// 琥珀主按钮（默认焦点），看到一半时下方一行 3px 进度条 +「剩 N 分钟」。
/// [onPressed] 为空时禁用，文字不变。
class DetailPlayButton extends StatelessWidget {
  const DetailPlayButton({
    super.key,
    required this.label,
    required this.progress,
    required this.onPressed,
  });

  final String label;
  final PlayProgress? progress;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    final progress = this.progress;
    final button = PressScale(
      enabled: onPressed != null,
      child: FilledButton.icon(
        // 遥控器进入详情页后直接按确定就能播放
        autofocus: true,
        onPressed: onPressed,
        style: FilledButton.styleFrom(
          minimumSize: const Size.fromHeight(48),
          padding: const EdgeInsets.symmetric(horizontal: 16),
        ),
        icon: const Icon(Icons.play_arrow_rounded, size: 26),
        label: Text(label, maxLines: 1, overflow: TextOverflow.ellipsis),
      ),
    );
    if (progress == null) return button;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        button,
        const SizedBox(height: 10),
        Row(
          children: [
            Expanded(child: ProgressLine(value: progress.fraction)),
            const SizedBox(width: CinemaSpacing.heading),
            Text(
              '剩 ${progress.remainingMinutes} 分钟',
              style: Theme.of(context).textTheme.labelSmall,
            ),
          ],
        ),
      ],
    );
  }
}

/// 播放和操作一行：主按钮占满剩余宽度，后面是 44 的图标按钮。
/// 主按钮下方可能有进度条，图标按钮对齐主按钮而不是整列。
/// 窄屏上按钮多到主按钮放不下时（手机上有 4 个图标按钮），操作按钮另起一行，
/// 播放按钮占满整行，免得图标按钮挤坏主按钮里的图标和文字。
class DetailActions extends StatelessWidget {
  const DetailActions({super.key, required this.play, required this.buttons});

  final Widget play;
  final List<Widget> buttons;

  static const _gap = 10.0;
  static const _minPlayWidth = 132.0;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final required = buttons.length * (44 + _gap) + _minPlayWidth;
        if (constraints.maxWidth < required) {
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              play,
              if (buttons.isNotEmpty) ...[
                const SizedBox(height: _gap),
                Row(
                  mainAxisAlignment: MainAxisAlignment.end,
                  children: [
                    for (final (i, button) in buttons.indexed) ...[
                      if (i > 0) const SizedBox(width: _gap),
                      button,
                    ],
                  ],
                ),
              ],
            ],
          );
        }
        return Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(child: play),
            for (final button in buttons) ...[
              const SizedBox(width: _gap),
              // 主按钮高 48，44 的图标按钮上下居中
              Padding(padding: const EdgeInsets.only(top: 2), child: button),
            ],
          ],
        );
      },
    );
  }
}

/// 片名下的元数据：「·」分隔的一行文字，后面跟内容分级和评分角标。
/// 详情给了 [ratings] 就每个来源一个角标，否则回退到单个 TMDB 评分。
class DetailMeta extends StatelessWidget {
  const DetailMeta({
    super.key,
    required this.parts,
    this.certification,
    this.rating,
    this.ratings = const [],
    this.trailing,
  });

  final List<String> parts;
  final String? certification;
  final double? rating;
  final List<SourceRating> ratings;

  /// 角标后面的补充，如单集的「21:30 结束」
  final String? trailing;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final rating = this.rating;
    return Wrap(
      crossAxisAlignment: WrapCrossAlignment.center,
      spacing: 10,
      runSpacing: 4,
      children: [
        if (parts.isNotEmpty)
          Text(
            parts.join(' · '),
            style: textTheme.bodySmall!.copyWith(color: CinemaColors.text1),
          ),
        if (certification case final certification?)
          ContentRatingBadge(certification),
        if (ratings.isNotEmpty)
          for (final source in ratings)
            SourceRatingBadge(label: source.label, rating: source.value)
        else if (rating != null && rating > 0)
          TmdbRatingBadge(rating),
        if (trailing case final trailing?)
          Text(trailing, style: textTheme.labelSmall),
      ],
    );
  }
}

/// 剧照铺满顶部，往下渐隐进底色。[art] 为空时是加载中的骨架，
/// [failed] 时（加载失败、又没有可显示的图）是 surface1 底。
class DetailBackdrop extends StatelessWidget {
  const DetailBackdrop({
    super.key,
    required this.art,
    this.heroTag,
    this.failed = false,
  });

  final Artwork? art;

  /// 从海报卡打开时的共享元素
  final String? heroTag;
  final bool failed;

  @override
  Widget build(BuildContext context) {
    final art = this.art;
    final heroTag = this.heroTag;
    final image = art == null ? null : ArtImage(art: art);
    return Stack(
      fit: StackFit.expand,
      children: [
        // 剧照比遮罩短 2px：两者底边在小数像素处一起抗锯齿时会漏出一条亮线，
        // 遮罩底部本来就是不透明的底色，缩进去看不出来
        Positioned.fill(
          bottom: 2,
          child: image == null
              ? failed
                    ? const ColoredBox(color: CinemaColors.surface1)
                    : const Skeleton()
              : heroTag == null
              ? image
              : ArtHero(tag: heroTag, child: image),
        ),
        // 正文从 58% 处压在剧照上，真实剧照常常偏亮，标题处要保证足够暗
        DecoratedBox(
          decoration: BoxDecoration(
            gradient: LinearGradient(
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
              colors: [
                CinemaColors.bg.withValues(alpha: .6),
                CinemaColors.bg.withValues(alpha: 0),
                CinemaColors.bg.withValues(alpha: 0),
                CinemaColors.bg.withValues(alpha: .8),
                CinemaColors.bg,
              ],
              stops: const [0, 0.22, 0.34, 0.58, 0.92],
            ),
          ),
        ),
      ],
    );
  }
}

/// 详情正文加载中的骨架：元数据、简介两行、主按钮。
class DetailBodySkeleton extends StatelessWidget {
  const DetailBodySkeleton({super.key});

  @override
  Widget build(BuildContext context) {
    Widget bar(double? width, double height) => Padding(
      padding: const EdgeInsets.only(top: CinemaSpacing.heading),
      child: SizedBox(
        width: width,
        height: height,
        child: Skeleton(borderRadius: BorderRadius.circular(CinemaRadius.tiny)),
      ),
    );
    return Semantics(
      label: '正在加载',
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          bar(220, 14),
          bar(double.infinity, 14),
          bar(double.infinity, 14),
          Padding(
            padding: const EdgeInsets.only(top: CinemaSpacing.heading),
            child: SizedBox(
              height: 48,
              child: Skeleton(
                borderRadius: BorderRadius.circular(CinemaRadius.control),
              ),
            ),
          ),
        ],
      ),
    );
  }
}
