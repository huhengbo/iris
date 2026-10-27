import 'package:flutter/material.dart';

import '../../app/motion.dart';
import '../../app/theme.dart';
import '../../core/api/paging.dart' show Artwork;
import '../../core/widgets/focus_tile.dart';
import '../library/art_image.dart';
import '../library/browse_api.dart';
import '../library/poster_card.dart' show WatchedBadge, openDetail;

/// 电视版的统一尺寸。电视隔着几米看：卡片大、字大、一屏只放少量内容。
abstract final class TvMetrics {
  static const posterWidth = 196.0;
  static const stillWidth = 352.0;
  static const gap = 24.0;

  /// 一排卡片上下留出聚焦放大和阴影的空间，不被横向列表裁掉
  static const railPadding = EdgeInsets.symmetric(horizontal: 6, vertical: 14);
}

/// 聚焦到一张卡片时的回调，用来让页面顶部的「聚焦展示区」跟着换内容。
typedef TvFocusCallback = void Function(MediaCard media);

Widget _focusWatcher({required Widget child, required VoidCallback? onFocus}) {
  if (onFocus == null) return child;
  return Focus(
    canRequestFocus: false,
    skipTraversal: true,
    onFocusChange: (focused) {
      if (focused) onFocus();
    },
    child: child,
  );
}

/// 2:3 海报 + 一行片名。聚焦时放大并描琥珀环。
class TvPoster extends StatelessWidget {
  const TvPoster({
    super.key,
    required this.media,
    this.onFocus,
    this.autofocus = false,
    this.width = TvMetrics.posterWidth,
    this.subtitle,
  });

  final MediaCard media;
  final TvFocusCallback? onFocus;
  final bool autofocus;
  final double width;

  /// 片名下的一行，如年份
  final String? subtitle;

  /// 片名行的高度，用来算一排的高度
  static double textHeight({bool subtitle = false}) => subtitle ? 64 : 40;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    return SizedBox(
      width: width,
      child: _focusWatcher(
        onFocus: onFocus == null ? null : () => onFocus!(media),
        child: FocusTile(
          autofocus: autofocus,
          focusScale: 1.07,
          semanticLabel: [
            media.title,
            ?subtitle,
            if (media.watched) '已看',
          ].join('，'),
          onTap: () => openDetail(context, media, 'tv:${media.id}'),
          builder: (context, highlight) => Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              AspectRatio(
                aspectRatio: 2 / 3,
                child: ArtFrame(
                  highlight: highlight,
                  radius: CinemaRadius.card,
                  dimmed: media.watched,
                  badge: media.watched ? const WatchedBadge() : null,
                  child: ArtImage(art: media.poster),
                ),
              ),
              const SizedBox(height: 10),
              Text(
                media.title,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: textTheme.titleSmall!.copyWith(
                  color: highlight.focused
                      ? CinemaColors.text0
                      : CinemaColors.text1,
                  fontWeight: highlight.focused ? FontWeight.w600 : null,
                ),
              ),
              if (subtitle != null)
                Text(
                  subtitle!,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: textTheme.bodySmall!.copyWith(
                    color: CinemaColors.text2,
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

/// 16:9 剧照卡（继续观看、精选）：标题 + 一行说明，可带进度条。
class TvStill extends StatelessWidget {
  const TvStill({
    super.key,
    required this.media,
    required this.art,
    required this.note,
    this.progress,
    this.onFocus,
    this.autofocus = false,
    this.width = TvMetrics.stillWidth,
  });

  final MediaCard media;
  final Artwork art;
  final String note;

  /// 0–1；为空时不画进度条
  final double? progress;
  final TvFocusCallback? onFocus;
  final bool autofocus;
  final double width;

  static const textHeight = 64.0;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final progress = this.progress;
    return SizedBox(
      width: width,
      child: _focusWatcher(
        onFocus: onFocus == null ? null : () => onFocus!(media),
        child: FocusTile(
          autofocus: autofocus,
          focusScale: 1.05,
          semanticLabel: '${media.title}，$note',
          onTap: () => openDetail(context, media, 'tv:${media.id}'),
          builder: (context, highlight) => Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              AspectRatio(
                aspectRatio: 16 / 9,
                child: ArtFrame(
                  highlight: highlight,
                  radius: CinemaRadius.card,
                  child: Stack(
                    fit: StackFit.expand,
                    children: [
                      ArtImage(art: art),
                      if (progress != null)
                        Positioned(
                          left: 12,
                          right: 12,
                          bottom: 12,
                          child: ProgressLine(value: progress),
                        ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 10),
              Text(
                media.title,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: textTheme.titleSmall!.copyWith(
                  color: highlight.focused
                      ? CinemaColors.text0
                      : CinemaColors.text1,
                  fontWeight: highlight.focused ? FontWeight.w600 : null,
                ),
              ),
              Text(
                note,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: textTheme.bodySmall!.copyWith(color: CinemaColors.text2),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// 一排卡片：标题 + 横向滚动。焦点进入这一排时，纵向列表把它滚到视野顶部，
/// 遥控器上下移动时一次只聚焦一排，不会停在两排之间。
class TvShelf extends StatelessWidget {
  const TvShelf({
    super.key,
    required this.title,
    required this.height,
    required this.children,
  });

  final String title;

  /// 卡片区域的高度（不含标题）
  final double height;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    return Focus(
      canRequestFocus: false,
      skipTraversal: true,
      onFocusChange: (focused) {
        if (!focused) return;
        Scrollable.ensureVisible(
          context,
          alignment: 0,
          duration: Motion.medium,
          curve: Motion.emphasized,
        );
      },
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(left: 6),
            child: Text(
              title,
              style: Theme.of(context).textTheme.titleLarge!.copyWith(
                color: CinemaColors.text0,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
          SizedBox(
            height: height + TvMetrics.railPadding.vertical,
            child: ListView.separated(
              scrollDirection: Axis.horizontal,
              padding: TvMetrics.railPadding,
              itemCount: children.length,
              separatorBuilder: (_, _) => const SizedBox(width: TvMetrics.gap),
              itemBuilder: (_, i) => children[i],
            ),
          ),
        ],
      ),
    );
  }
}

/// 电视上的按钮：聚焦时整颗变琥珀并略放大，隔着客厅也看得出焦点在哪。
/// [label] 为空时是纯图标的圆按钮。
class TvButton extends StatefulWidget {
  const TvButton({
    super.key,
    required this.icon,
    required this.onPressed,
    this.label,
    this.tooltip,
    this.focusNode,
    this.autofocus = false,
    this.primary = false,
  });

  final IconData icon;
  final String? label;
  final String? tooltip;
  final VoidCallback? onPressed;
  final FocusNode? focusNode;
  final bool autofocus;

  /// 主操作（播放）更大，未聚焦时也是琥珀色
  final bool primary;

  @override
  State<TvButton> createState() => _TvButtonState();
}

class _TvButtonState extends State<TvButton> {
  bool _focused = false;

  @override
  Widget build(BuildContext context) {
    final label = widget.label;
    final enabled = widget.onPressed != null;
    final filled = _focused || widget.primary;
    final foreground = filled ? CinemaColors.amberInk : CinemaColors.text0;
    final height = widget.primary ? 64.0 : 56.0;
    return FocusableActionDetector(
      focusNode: widget.focusNode,
      autofocus: widget.autofocus,
      enabled: enabled,
      onShowFocusHighlight: (v) => setState(() => _focused = v),
      actions: {
        ActivateIntent: CallbackAction<ActivateIntent>(
          onInvoke: (_) {
            widget.onPressed?.call();
            return null;
          },
        ),
      },
      child: Tooltip(
        message: widget.tooltip ?? label ?? '',
        child: GestureDetector(
          onTap: widget.onPressed,
          child: AnimatedScale(
            scale: _focused ? 1.06 : 1,
            duration: Motion.focus,
            curve: Motion.emphasized,
            child: Opacity(
              opacity: enabled ? 1 : .4,
              child: AnimatedContainer(
                duration: Motion.focus,
                height: height,
                width: label == null ? height : null,
                padding: EdgeInsets.symmetric(
                  horizontal: label == null ? 0 : (widget.primary ? 30 : 24),
                ),
                decoration: BoxDecoration(
                  color: filled
                      ? (_focused
                            ? CinemaColors.amber
                            : CinemaColors.amber.withValues(alpha: .86))
                      : Colors.white.withValues(alpha: .14),
                  borderRadius: BorderRadius.circular(height / 2),
                  border: Border.all(
                    color: _focused && widget.primary
                        ? CinemaColors.text0
                        : Colors.transparent,
                    width: 3,
                  ),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Icon(widget.icon, size: 30, color: foreground),
                    if (label != null) ...[
                      const SizedBox(width: 10),
                      Text(
                        label,
                        style: Theme.of(context).textTheme.titleMedium!
                            .copyWith(
                              color: foreground,
                              fontWeight: FontWeight.w600,
                            ),
                      ),
                    ],
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
