import 'package:iris/app/motion.dart';
import 'package:iris/app/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('buttons show a visible border when focused', () {
    final theme = buildTheme();
    expect(
      theme.textTheme.bodyMedium!.fontFamilyFallback,
      contains('Noto Sans CJK SC'),
    );
    BorderSide? side(ButtonStyle? style, Set<WidgetState> states) =>
        style!.side!.resolve(states);

    expect(
      side(theme.filledButtonTheme.style, {WidgetState.focused}),
      isNotNull,
    );
    expect(side(theme.filledButtonTheme.style, {}), isNull);
    expect(
      side(theme.textButtonTheme.style, {WidgetState.focused})!.color,
      CinemaColors.amber,
    );
  });

  Future<double> pressedScale(
    WidgetTester tester, {
    required bool reduce,
  }) async {
    await tester.pumpWidget(
      MediaQuery(
        data: MediaQueryData(disableAnimations: reduce),
        child: Directionality(
          textDirection: TextDirection.ltr,
          child: Center(
            child: PressScale(
              child: const SizedBox.square(
                dimension: 80,
                child: ColoredBox(color: Color(0xFF000000)),
              ),
            ),
          ),
        ),
      ),
    );
    final gesture = await tester.startGesture(
      tester.getCenter(find.byType(PressScale)),
    );
    await tester.pumpAndSettle();
    final scale = tester
        .widget<AnimatedScale>(find.byType(AnimatedScale))
        .scale;
    await gesture.up();
    await tester.pumpAndSettle();
    expect(tester.widget<AnimatedScale>(find.byType(AnimatedScale)).scale, 1);
    return scale;
  }

  testWidgets('press feedback shrinks and springs back', (tester) async {
    expect(await pressedScale(tester, reduce: false), 0.97);
  });

  testWidgets('press feedback is off when reduce motion is on', (tester) async {
    expect(await pressedScale(tester, reduce: true), 1);
  });
}
