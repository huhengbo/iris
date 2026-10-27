import 'package:flutter/material.dart';

import '../../app/theme.dart';

/// 元数据行里的小角标：ghost 实底、tiny 圆角、labelSmall w700 等宽数字。
/// 内容分级和 TMDB 评分共用这一个样式，只靠文字区分。
class _Badge extends StatelessWidget {
  const _Badge({required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    return DecoratedBox(
      decoration: BoxDecoration(
        color: CinemaColors.ghost,
        borderRadius: BorderRadius.circular(CinemaRadius.tiny),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
        child: DefaultTextStyle.merge(
          style: Theme.of(context).textTheme.labelSmall!.copyWith(
            color: CinemaColors.text0,
            fontWeight: FontWeight.w700,
            fontFeatures: const [FontFeature.tabularFigures()],
          ),
          child: child,
        ),
      ),
    );
  }
}

/// 内容分级，如 TV-MA。
class ContentRatingBadge extends StatelessWidget {
  const ContentRatingBadge(this.certification, {super.key});

  final String certification;

  @override
  Widget build(BuildContext context) {
    return _Badge(child: Text(certification));
  }
}

/// 一个评分来源的角标，如「豆瓣 8.6」：来源用 text2，分数用 text0。
class SourceRatingBadge extends StatelessWidget {
  const SourceRatingBadge({
    super.key,
    required this.label,
    required this.rating,
  });

  final String label;
  final double rating;

  @override
  Widget build(BuildContext context) {
    return _Badge(
      child: Text.rich(
        TextSpan(
          children: [
            TextSpan(
              text: '$label ',
              style: const TextStyle(color: CinemaColors.text2),
            ),
            TextSpan(text: rating.toStringAsFixed(1)),
          ],
        ),
      ),
    );
  }
}

/// 「TMDB 8.4」。只有详情没有 ratings 字段时回退使用。
class TmdbRatingBadge extends StatelessWidget {
  const TmdbRatingBadge(this.rating, {super.key});

  final double rating;

  @override
  Widget build(BuildContext context) =>
      SourceRatingBadge(label: 'TMDB', rating: rating);
}
