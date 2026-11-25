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

/// 缓冲时屏幕正中的圈和百分比，带暗色圆底保障亮画面下的对比度。
class _BufferingIndicator extends StatelessWidget {
  const _BufferingIndicator({required this.visible, required this.percent});

  final bool visible;

  /// 0–100
  final double percent;

  @override
  Widget build(BuildContext context) {
    return IgnorePointer(
      child: Center(
        child: AnimatedSwitcher(
          duration: Motion.short,
          switchInCurve: Motion.emphasized,
          switchOutCurve: Motion.emphasized,
          child: visible
              ? DecoratedBox(
                  key: const ValueKey(true),
                  decoration: const BoxDecoration(
                    color: CinemaColors.scrim,
                    shape: BoxShape.circle,
                  ),
                  child: Padding(
                    padding: const EdgeInsets.all(10),
                    child: SizedBox.square(
                      dimension: 52,
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
                            style: Theme.of(context).textTheme.labelSmall!
                                .copyWith(
                                  color: CinemaColors.text0,
                                  fontFeatures: const [
                                    FontFeature.tabularFigures(),
                                  ],
                                ),
                          ),
                        ],
                      ),
                    ),
                  ),
                )
              : const SizedBox.shrink(key: ValueKey(false)),
        ),
      ),
    );
  }
}

/// 屏幕中上方的临时提示：淡出时保留最后内容直至透明度归零，避免瞬间闪烁消失。
class _CenterHud extends StatefulWidget {
  const _CenterHud({required this.content});

  final Widget? content;

  @override
  State<_CenterHud> createState() => _CenterHudState();
}

class _CenterHudState extends State<_CenterHud> {
  Widget? _cached;

  @override
  void initState() {
    super.initState();
    _cached = widget.content;
  }

  @override
  void didUpdateWidget(_CenterHud oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.content != null) {
      _cached = widget.content;
    }
  }

  @override
  Widget build(BuildContext context) {
    final showing = widget.content != null;
    final child = widget.content ?? _cached;
    return IgnorePointer(
      child: Align(
        alignment: const Alignment(0, -0.35),
        child: AnimatedOpacity(
          opacity: showing ? 1 : 0,
          duration: Motion.short,
          curve: Motion.emphasized,
          onEnd: () {
            if (widget.content == null && _cached != null) {
              setState(() => _cached = null);
            }
          },
          child: child == null
              ? const SizedBox.shrink()
              : DecoratedBox(
                  decoration: BoxDecoration(
                    color: CinemaColors.scrim,
                    borderRadius: BorderRadius.circular(CinemaRadius.pill),
                  ),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 16,
                      vertical: 8,
                    ),
                    child: child,
                  ),
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

/// 媒体章节：从 mpv `chapter-list` 解析而来。
class PlayerChapter {
  const PlayerChapter({required this.title, required this.start});

  final String title;
  final Duration start;
}

/// 解析 mpv `chapter-list`（JSON 数组，含 `title` / `time`）。
/// 非原生环境、空串或格式错误时返回空列表，不抛异常。
List<PlayerChapter> parseChapterList(String? raw) {
  if (raw == null || raw.trim().isEmpty) return const [];
  try {
    final decoded = jsonDecode(raw);
    if (decoded is! List<dynamic>) return const [];
    final list = <PlayerChapter>[];
    for (var i = 0; i < decoded.length; i++) {
      final item = decoded[i];
      if (item is! Map<String, dynamic>) continue;
      final time = item['time'];
      if (time is! num || time.isNaN || time.isInfinite || time < 0) continue;
      final title = (item['title'] as String?)?.trim();
      list.add(
        PlayerChapter(
          title: (title == null || title.isEmpty) ? '第 ${i + 1} 章' : title,
          start: Duration(milliseconds: (time * 1000).round()),
        ),
      );
    }
    list.sort((a, b) => a.start.compareTo(b.start));
    return list;
  } on Object {
    return const [];
  }
}

/// 当前播放位置之后的下一章节；不存在时返回 null。
PlayerChapter? nextChapterAfter(
  List<PlayerChapter> chapters,
  Duration position,
) {
  for (final chapter in chapters) {
    if (chapter.start > position + const Duration(seconds: 1)) {
      return chapter;
    }
  }
  return null;
}

const _introWindow = Duration(minutes: 5);
const _defaultSkipIntro = Duration(seconds: 85);

/// 是否在进度条上方展示「跳过片头」胶囊按钮：
/// 控制条唤出且未锁屏、片长足够，处于剧集前 5 分钟或存在 5 分钟内的片头章节。
bool shouldShowSkipIntro({
  required bool visible,
  required bool locked,
  required bool episodic,
  required Duration position,
  required Duration duration,
  required List<PlayerChapter> chapters,
}) {
  if (!visible || locked || duration <= const Duration(seconds: 90)) {
    return false;
  }
  final hasIntroChapter = chapters.any(
    (c) =>
        c.start > Duration.zero &&
        c.start <= _introWindow &&
        position < c.start - const Duration(seconds: 2),
  );
  if (hasIntroChapter) return true;
  return episodic && position < _introWindow;
}

/// 计算「跳过片头」目标位置：存在 0–5 分钟内的下一章节时跳到该章节，
/// 否则默认前进 85 秒（不超过总时长）。
Duration skipIntroTarget({
  required Duration position,
  required Duration duration,
  required List<PlayerChapter> chapters,
}) {
  for (final chapter in chapters) {
    if (chapter.start > position + const Duration(seconds: 2) &&
        chapter.start <= _introWindow) {
      return duration > Duration.zero && chapter.start > duration
          ? duration
          : chapter.start;
    }
  }
  final fallback = position + _defaultSkipIntro;
  if (duration > Duration.zero && fallback > duration) return duration;
  return fallback;
}

/// 根据屏高、字号缩放和底部边距计算字幕配置。
SubtitleViewConfiguration buildSubtitleViewConfiguration(
  double height, {
  double scale = 1.0,
  double bottomPadding = 28.0,
}) {
  const base = 32.0;
  const outline = Colors.black87;
  final scaled = ((height * .055).clamp(18.0, 48.0) * scale).clamp(14.0, 64.0);
  return SubtitleViewConfiguration(
    style: const TextStyle(
      height: 1.35,
      fontSize: base,
      color: Colors.white,
      fontWeight: FontWeight.w500,
      shadows: [
        Shadow(color: outline, blurRadius: 3),
        Shadow(color: outline, offset: Offset(1, 1), blurRadius: 1),
        Shadow(color: outline, offset: Offset(-1, -1), blurRadius: 1),
      ],
    ),
    textScaler: TextScaler.linear(scaled / base),
    padding: EdgeInsets.fromLTRB(48, 0, 48, bottomPadding),
  );
}

/// 顶栏右侧的系统时间与预计结束时间。
String formatTopBarTime(
  DateTime now, {
  required Duration position,
  required Duration duration,
  double rate = 1.0,
}) {
  String hhmm(DateTime t) {
    String two(int v) => v.toString().padLeft(2, '0');
    return '${two(t.hour)}:${two(t.minute)}';
  }

  final nowText = hhmm(now);
  if (duration <= Duration.zero) return nowText;
  final clampedPos = position < Duration.zero
      ? Duration.zero
      : (position > duration ? duration : position);
  final remaining = duration - clampedPos;
  final effectiveRate = rate > 0 ? rate : 1.0;
  final adjusted = Duration(
    milliseconds: (remaining.inMilliseconds / effectiveRate).round(),
  );
  final endText = hhmm(now.add(adjusted));
  return '$nowText · 预计 $endText 结束';
}

/// 剧集完播后画面右下角的自动连播倒计时浮层。
class AutoNextOverlay extends StatelessWidget {
  const AutoNextOverlay({
    super.key,
    required this.seconds,
    required this.nextTitle,
    required this.onPlayNow,
    required this.onCancel,
    this.tv = false,
  });

  final int seconds;
  final String nextTitle;
  final VoidCallback onPlayNow;
  final VoidCallback onCancel;
  final bool tv;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    return Material(
      color: Colors.transparent,
      child: Container(
        constraints: const BoxConstraints(maxWidth: 380),
        padding: const EdgeInsets.fromLTRB(16, 12, 12, 12),
        decoration: BoxDecoration(
          color: CinemaColors.surface2.withValues(alpha: .94),
          borderRadius: BorderRadius.circular(CinemaRadius.card),
          border: Border.all(color: CinemaColors.line),
          boxShadow: CinemaShadows.lift,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '$seconds 秒后播放 $nextTitle',
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: (tv ? textTheme.titleMedium : textTheme.titleSmall)!
                  .copyWith(
                    color: CinemaColors.text0,
                    fontFeatures: const [FontFeature.tabularFigures()],
                  ),
            ),
            const SizedBox(height: 10),
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                TextButton(onPressed: onCancel, child: const Text('取消')),
                const SizedBox(width: 8),
                FilledButton(
                  autofocus: tv,
                  onPressed: onPlayNow,
                  child: const Text('立即播放'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

/// 播放信息浮层（Stats for Nerds）数据结构。
class PlayerStatsInfo {
  const PlayerStatsInfo({
    required this.resolution,
    required this.videoCodec,
    required this.audioCodec,
    required this.hwdec,
    required this.rate,
    required this.bufferAhead,
  });

  final String resolution;
  final String videoCodec;
  final String audioCodec;
  final String hwdec;
  final double rate;
  final Duration bufferAhead;
}

/// 画面左上角的紧凑半透明播放信息浮层，不拦截任何手势与点击。
class PlayerStatsOverlay extends StatelessWidget {
  const PlayerStatsOverlay({super.key, required this.stats});

  final PlayerStatsInfo stats;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final labelStyle = textTheme.labelSmall!.copyWith(
      color: CinemaColors.text2,
    );
    final valueStyle = textTheme.labelSmall!.copyWith(
      color: CinemaColors.text0,
      fontFeatures: const [FontFeature.tabularFigures()],
    );
    Widget row(String label, String value) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 1.5),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          SizedBox(width: 56, child: Text(label, style: labelStyle)),
          Flexible(
            child: Text(
              value,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: valueStyle,
            ),
          ),
        ],
      ),
    );

    return IgnorePointer(
      child: Container(
        constraints: const BoxConstraints(maxWidth: 280),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        decoration: BoxDecoration(
          color: CinemaColors.scrim,
          borderRadius: BorderRadius.circular(CinemaRadius.card),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            row('分辨率', stats.resolution),
            row('视频', stats.videoCodec),
            row('音频', stats.audioCodec),
            row('硬解', stats.hwdec),
            row('倍速', '${_rateLabel(stats.rate)}×'),
            row('缓冲', '${stats.bufferAhead.inSeconds} 秒'),
          ],
        ),
      ),
    );
  }
}
