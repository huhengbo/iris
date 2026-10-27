import 'package:iris/app/launch_splash.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  var taps = 0;
  var mounts = 0;

  setUp(() {
    taps = 0;
    mounts = 0;
  });

  Widget app({required bool ready, bool reduced = false}) => MaterialApp(
    builder: (context, child) => MediaQuery(
      data: MediaQuery.of(context).copyWith(disableAnimations: reduced),
      child: LaunchSplash(ready: ready, child: child!),
    ),
    home: Scaffold(
      body: Center(
        child: _Counted(
          onMount: () => mounts++,
          child: TextButton(onPressed: () => taps++, child: const Text('首页')),
        ),
      ),
    ),
  );

  Finder backdrop() => find.bySemanticsLabel('正在启动');

  double spinnerOpacity(WidgetTester tester) => tester
      .widget<AnimatedOpacity>(
        find.ancestor(
          of: find.byType(CircularProgressIndicator),
          matching: find.byType(AnimatedOpacity),
        ),
      )
      .opacity;

  testWidgets('covers the app and blocks taps until ready', (tester) async {
    await tester.pumpWidget(app(ready: false));
    expect(backdrop(), findsOneWidget);
    await tester.tap(find.text('首页'), warnIfMissed: false);
    expect(taps, 0);
    // 正常启动看不到转圈
    expect(spinnerOpacity(tester), 0);
  });

  testWidgets('opens once ready and keeps the app state', (tester) async {
    await tester.pumpWidget(app(ready: false));
    await tester.pumpWidget(app(ready: true));
    await tester.pump();
    await tester.pump(LaunchSplash.duration ~/ 2);
    expect(backdrop(), findsOneWidget);
    await tester.pump(LaunchSplash.duration);
    expect(backdrop(), findsNothing);
    await tester.tap(find.text('首页'));
    expect(taps, 1);
    expect(mounts, 1);
  });

  testWidgets('shows a spinner when restoring the session is slow', (
    tester,
  ) async {
    await tester.pumpWidget(app(ready: false));
    await tester.pump(LaunchSplash.slowAfter);
    expect(spinnerOpacity(tester), 1);
    await tester.pumpWidget(app(ready: true));
    expect(spinnerOpacity(tester), 0);
    await tester.pump();
    await tester.pump(LaunchSplash.duration * 2);
    expect(backdrop(), findsNothing);
  });

  testWidgets('reduced motion fades in quickly instead', (tester) async {
    await tester.pumpWidget(app(ready: true, reduced: true));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));
    final fade = tester.widget<FadeTransition>(
      find
          .ancestor(
            of: find.byType(Scaffold),
            matching: find.byType(FadeTransition),
          )
          .last,
    );
    expect(fade.opacity.value, closeTo(0.5, 0.01));
    // 动画到头后的下一帧才算结束
    await tester.pump(const Duration(milliseconds: 101));
    expect(backdrop(), findsNothing);
    await tester.tap(find.text('首页'));
    expect(taps, 1);
  });
}

class _Counted extends StatefulWidget {
  const _Counted({required this.onMount, required this.child});

  final VoidCallback onMount;
  final Widget child;

  @override
  State<_Counted> createState() => _CountedState();
}

class _CountedState extends State<_Counted> {
  @override
  void initState() {
    super.initState();
    widget.onMount();
  }

  @override
  Widget build(BuildContext context) => widget.child;
}
