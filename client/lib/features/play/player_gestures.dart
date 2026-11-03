part of 'player_page.dart';

/// 横滑划满一屏对应片长的 1/10，夹在 1–5 分钟之间：短片一划不至于过头，
/// 长片也不用来回划（DESIGN.md「内置播放器」）
const _dragSeekShortest = Duration(minutes: 1);
const _dragSeekLongest = Duration(minutes: 5);

/// 竖滑划过屏幕高度的这一比例，亮度或音量从 0 调到满
const _levelDragSpan = .6;

/// 离上下边缘这么近起手的滑动不算，留给下拉状态栏和上滑回桌面
const _edgeDeadZone = 24.0;

/// 横滑划满一屏宽度对应的时长，不超过片长
Duration dragSeekSpan(Duration duration) {
  var span = duration ~/ 10;
  if (span < _dragSeekShortest) span = _dragSeekShortest;
  if (span > _dragSeekLongest) span = _dragSeekLongest;
  return duration > Duration.zero && span > duration ? duration : span;
}

/// 横滑 [dx] 像素对应的进度偏移，整秒
Duration dragSeekOffset(double dx, double width, Duration duration) {
  if (width <= 0) return Duration.zero;
  final ms = dx / width * dragSeekSpan(duration).inMilliseconds;
  return Duration(seconds: (ms / 1000).round());
}

/// 偏移量带正负号：「+1:23」「−1:02:03」
String formatSeekOffset(Duration offset) =>
    '${offset.isNegative ? '−' : '+'}${formatClock(offset.abs())}';

/// 竖滑调的是哪一项：左半屏亮度，右半屏音量
enum PlayerLevel { brightness, volume }

PlayerLevel levelSideAt(double x, double width) =>
    x < width / 2 ? PlayerLevel.brightness : PlayerLevel.volume;

/// 从 [from] 开始竖滑 [dy] 像素（向下为正）后的值，上滑变大，夹在 0–1
double levelAfterDrag(double from, double dy, double height) {
  if (height <= 0) return from;
  return (from - dy / (height * _levelDragSpan)).clamp(0.0, 1.0);
}

/// 滑动起点落在系统手势区（侧边返回、底部横条）或离上下边缘太近时不响应，
/// 免得和系统手势抢
bool inGestureDeadZone(Offset at, Size size, EdgeInsets systemGestures) {
  final top = systemGestures.top.clamp(_edgeDeadZone, double.infinity);
  final bottom = systemGestures.bottom.clamp(_edgeDeadZone, double.infinity);
  return at.dy < top ||
      at.dy > size.height - bottom ||
      at.dx < systemGestures.left ||
      at.dx > size.width - systemGestures.right;
}

/// 屏幕亮度和媒体音量。安卓走原生通道（MainActivity）：亮度只改本窗口，
/// 退出播放器就恢复跟随系统；音量是系统媒体音量。其他平台没有亮度，
/// 音量调播放器自己的（[PlayerLevels.native] 为 false 时由调用方处理）。
abstract final class PlayerLevels {
  static const _channel = MethodChannel('iris/device');

  static bool get native =>
      !kIsWeb && defaultTargetPlatform == TargetPlatform.android;

  static Future<double?> read(PlayerLevel level) =>
      _invoke(level == PlayerLevel.brightness ? 'getBrightness' : 'getVolume');

  static Future<void> write(PlayerLevel level, double value) => _invoke(
    level == PlayerLevel.brightness ? 'setBrightness' : 'setVolume',
    value,
  );

  static Future<void> resetBrightness() => _invoke('resetBrightness');

  static Future<double?> _invoke(String method, [double? value]) async {
    if (!native) return null;
    try {
      final result = await _channel.invokeMethod<num>(method, value);
      return result?.toDouble();
    } on Object {
      // 原生端不支持或调不动时手势就不起作用，不打断播放
      return null;
    }
  }
}

/// 竖滑时屏幕中间的指示：图标 + 细进度条 + 百分比
class _LevelIndicator extends StatelessWidget {
  const _LevelIndicator({required this.level, required this.value});

  final PlayerLevel level;
  final double value;

  @override
  Widget build(BuildContext context) {
    final icon = switch (level) {
      PlayerLevel.brightness => Icons.brightness_6,
      PlayerLevel.volume when value <= 0 => Icons.volume_off,
      PlayerLevel.volume => Icons.volume_up,
    };
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(icon, size: 20, color: CinemaColors.text0),
        const SizedBox(width: 10),
        SizedBox(
          width: 120,
          child: ClipRRect(
            borderRadius: BorderRadius.circular(CinemaRadius.pill),
            child: LinearProgressIndicator(
              value: value,
              minHeight: 4,
              color: CinemaColors.amber,
              backgroundColor: CinemaColors.track,
            ),
          ),
        ),
        const SizedBox(width: 10),
        SizedBox(
          width: 40,
          child: Text(
            '${(value * 100).round()}%',
            textAlign: TextAlign.end,
            style: Theme.of(context).textTheme.titleSmall!.copyWith(
              color: CinemaColors.text0,
              fontFeatures: const [FontFeature.tabularFigures()],
            ),
          ),
        ),
      ],
    );
  }
}
