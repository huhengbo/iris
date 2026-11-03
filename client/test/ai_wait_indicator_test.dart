import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/app/motion.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/core/widgets/ai_wait_indicator.dart';

Widget _host({bool reduced = false, String label = '正在思考…'}) => MaterialApp(
  theme: buildTheme(),
  home: MediaQuery(
    data: MediaQueryData(disableAnimations: reduced),
    child: Scaffold(
      body: Center(child: AiWaitIndicator(label: label)),
    ),
  ),
);

void main() {
  test('one breath is 1.6s, the same period as the skeleton shimmer', () {
    expect(Motion.breath, const Duration(milliseconds: 1600));
  });

  testWidgets('shows the label and keeps animating', (tester) async {
    await tester.pumpWidget(_host());

    expect(find.text('正在思考…'), findsOneWidget);
    // 循环动画一直在排帧
    await tester.pump(const Duration(milliseconds: 400));
    expect(tester.hasRunningAnimations, isTrue);
    await tester.pump(const Duration(milliseconds: 1600));
    expect(tester.hasRunningAnimations, isTrue);

    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('reduced motion stops the iris and counts seconds', (
    tester,
  ) async {
    await tester.pumpWidget(_host(reduced: true, label: '正在搜索资料库…'));

    expect(find.text('正在搜索资料库…'), findsOneWidget);
    await tester.pump(const Duration(seconds: 3));

    expect(find.text('正在搜索资料库 · 3s'), findsOneWidget);
    // 读屏只播报状态文字，不播报每秒变化的秒数
    expect(tester.getSemantics(find.byType(AiWaitIndicator)).label, '正在搜索资料库…');
    // 秒表是计时器，不是动画：没有动画在排帧
    expect(tester.hasRunningAnimations, isFalse);

    await tester.pumpWidget(const SizedBox());
  });
}
