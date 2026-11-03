import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/features/play/player_page.dart';

void main() {
  test('横滑一屏的时长是片长的 1/10，夹在 1–5 分钟，不超过片长', () {
    // 短片保底 1 分钟，但不能超过片长本身
    expect(
      dragSeekSpan(const Duration(minutes: 5)),
      const Duration(minutes: 1),
    );
    expect(
      dragSeekSpan(const Duration(seconds: 40)),
      const Duration(seconds: 40),
    );
    // 中等长度按 1/10
    expect(
      dragSeekSpan(const Duration(minutes: 24)),
      const Duration(minutes: 2, seconds: 24),
    );
    // 长片封顶 5 分钟
    expect(dragSeekSpan(const Duration(hours: 2)), const Duration(minutes: 5));
    // 还不知道片长时按保底
    expect(dragSeekSpan(Duration.zero), const Duration(minutes: 1));
  });

  test('横滑偏移按屏宽比例换算成整秒', () {
    const movie = Duration(hours: 2);
    expect(dragSeekOffset(400, 800, movie), const Duration(seconds: 150));
    expect(dragSeekOffset(-800, 800, movie), const Duration(minutes: -5));
    expect(dragSeekOffset(1, 800, movie), Duration.zero);
    expect(dragSeekOffset(100, 0, movie), Duration.zero);
  });

  test('偏移量带正负号，超过一小时写到时', () {
    expect(formatSeekOffset(const Duration(seconds: 83)), '+1:23');
    expect(formatSeekOffset(const Duration(seconds: -62)), '−1:02');
    expect(
      formatSeekOffset(const Duration(hours: -1, minutes: -2, seconds: -3)),
      '−1:02:03',
    );
    expect(formatSeekOffset(Duration.zero), '+0:00');
  });

  test('左半屏调亮度，右半屏调音量', () {
    expect(levelSideAt(10, 800), PlayerLevel.brightness);
    expect(levelSideAt(399, 800), PlayerLevel.brightness);
    expect(levelSideAt(400, 800), PlayerLevel.volume);
    expect(levelSideAt(790, 800), PlayerLevel.volume);
  });

  test('竖滑 60% 屏高调满全程，上滑变大，夹在 0–1', () {
    const height = 400.0;
    // 上滑 240（屏高的 60%）从 0 到满
    expect(levelAfterDrag(0, -240, height), closeTo(1, 1e-9));
    expect(levelAfterDrag(.5, -120, height), closeTo(1, 1e-9));
    expect(levelAfterDrag(.5, 48, height), closeTo(.3, 1e-9));
    expect(levelAfterDrag(.2, 1000, height), 0);
    expect(levelAfterDrag(.9, -1000, height), 1);
    expect(levelAfterDrag(.4, 50, 0), .4);
  });

  test('系统手势区和上下边缘起手的滑动不响应', () {
    const size = Size(800, 400);
    const none = EdgeInsets.zero;
    const gestures = EdgeInsets.fromLTRB(30, 0, 30, 40);
    expect(inGestureDeadZone(const Offset(400, 200), size, none), isFalse);
    // 上下边缘至少留 24
    expect(inGestureDeadZone(const Offset(400, 10), size, none), isTrue);
    expect(inGestureDeadZone(const Offset(400, 390), size, none), isTrue);
    // 系统手势区更宽时以系统为准
    expect(inGestureDeadZone(const Offset(400, 370), size, gestures), isTrue);
    expect(inGestureDeadZone(const Offset(20, 200), size, gestures), isTrue);
    expect(inGestureDeadZone(const Offset(780, 200), size, gestures), isTrue);
    expect(inGestureDeadZone(const Offset(40, 200), size, gestures), isFalse);
  });
}
