part of 'player_page.dart';

/// 控制层背后的暗色渐层，保证白字和图标在亮画面上也看得清。
/// 渐层铺满屏幕边缘（不被刘海 / 过扫描安全区截成矩形），内容让进安全区。
class _Scrim extends StatelessWidget {
  const _Scrim({required this.top, required this.tv, required this.child});

  final bool top;

  /// 电视上看的是几米外的画面，渐层更深、再到中部才淡出
  final bool tv;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final safe = MediaQuery.paddingOf(context);
    return DecoratedBox(
      decoration: BoxDecoration(
        gradient: LinearGradient(
          begin: top ? Alignment.topCenter : Alignment.bottomCenter,
          end: top ? Alignment.bottomCenter : Alignment.topCenter,
          colors: tv
              ? [
                  Colors.black.withValues(alpha: .82),
                  Colors.black.withValues(alpha: .38),
                  Colors.transparent,
                ]
              : [Colors.black.withValues(alpha: .7), Colors.transparent],
          stops: tv ? const [0, .55, 1] : null,
        ),
      ),
      child: Padding(
        padding: EdgeInsets.fromLTRB(
          8 + safe.left,
          top ? 8 + safe.top : 20,
          8 + safe.right,
          top ? 20 : 8 + safe.bottom,
        ),
        child: child,
      ),
    );
  }
}

/// 缓冲时屏幕正中的圈和百分比
class _BufferingIndicator extends StatelessWidget {
  const _BufferingIndicator({required this.percent});

  /// 0–100
  final double percent;

  @override
  Widget build(BuildContext context) {
    return IgnorePointer(
      child: Center(
        child: SizedBox.square(
          dimension: 56,
          child: Stack(
            alignment: Alignment.center,
            children: [
              const SizedBox.expand(
                child: CircularProgressIndicator(
                  strokeWidth: 3,
                  color: CinemaColors.amber,
                ),
              ),
              Text(
                '${percent.clamp(0, 100).round()}%',
                style: Theme.of(context).textTheme.labelSmall!.copyWith(
                  color: CinemaColors.text0,
                  fontFeatures: const [FontFeature.tabularFigures()],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _TimeText extends StatelessWidget {
  const _TimeText(this.text, {this.tv = false});

  final String text;
  final bool tv;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 8),
      child: Text(
        text,
        style: (tv ? textTheme.titleMedium : textTheme.bodySmall)!.copyWith(
          color: CinemaColors.text1,
          fontFeatures: const [FontFeature.tabularFigures()],
        ),
      ),
    );
  }
}

class _SectionLabel extends StatelessWidget {
  const _SectionLabel(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    // 左边和 PickerOption 的文字对齐
    return Padding(
      padding: const EdgeInsets.fromLTRB(8, 10, 8, 4),
      child: Text(
        text,
        style: Theme.of(context).textTheme.labelLarge!
            .copyWith(color: CinemaColors.text2),
      ),
    );
  }
}

/// media_kit 在轨道列表最前面放了「自动」「关闭」两个虚拟轨道
bool _isRealTrack(Object track) => switch (track) {
  AudioTrack(:final id) ||
  SubtitleTrack(:final id) => id != 'auto' && id != 'no',
  _ => false,
};

String _trackLabel(String? title, String? language, String kind, int index) {
  final parts = [?title, ?language];
  return parts.isEmpty ? '$kind ${index + 1}' : parts.join(' · ');
}

String _rateLabel(double rate) =>
    rate == rate.roundToDouble() ? rate.toStringAsFixed(1) : '$rate';
