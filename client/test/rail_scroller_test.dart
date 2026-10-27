import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/core/widgets/rail_scroller.dart';

Future<ScrollController> _pump(WidgetTester tester, {int count = 20}) async {
  final controller = ScrollController();
  addTearDown(controller.dispose);
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: Align(
          alignment: Alignment.topLeft,
          child: SizedBox(
            width: 400,
            height: 100,
            child: RailScroller(
              controller: controller,
              builder: (context, controller) => ListView.builder(
                controller: controller,
                scrollDirection: Axis.horizontal,
                itemCount: count,
                itemBuilder: (_, i) => SizedBox(width: 100, child: Text('$i')),
              ),
            ),
          ),
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
  return controller;
}

Future<TestGesture> _hover(WidgetTester tester) async {
  final mouse = await tester.createGesture(kind: PointerDeviceKind.mouse);
  await mouse.addPointer(location: const Offset(900, 500));
  addTearDown(mouse.removePointer);
  await mouse.moveTo(const Offset(200, 50));
  await tester.pumpAndSettle();
  return mouse;
}

void main() {
  testWidgets('arrows only show while the mouse is over the rail', (
    tester,
  ) async {
    await _pump(tester);
    expect(find.byTooltip('向后'), findsNothing);

    final mouse = await _hover(tester);
    expect(find.byTooltip('向后'), findsOneWidget);
    // 在最左边，没有往前的箭头
    expect(find.byTooltip('向前'), findsNothing);

    await mouse.moveTo(const Offset(900, 500));
    await tester.pumpAndSettle();
    expect(find.byTooltip('向后'), findsNothing);
  });

  testWidgets('the arrow pages the rail and the other arrow appears', (
    tester,
  ) async {
    final controller = await _pump(tester);
    await _hover(tester);

    await tester.tap(find.byTooltip('向后'));
    await tester.pumpAndSettle();
    expect(controller.offset, closeTo(320, 1));
    expect(find.byTooltip('向前'), findsOneWidget);

    await tester.tap(find.byTooltip('向前'));
    await tester.pumpAndSettle();
    expect(controller.offset, 0);
  });

  testWidgets('a rail that fits has no arrows', (tester) async {
    await _pump(tester, count: 3);
    await _hover(tester);

    expect(find.byTooltip('向后'), findsNothing);
    expect(find.byTooltip('向前'), findsNothing);
  });

  testWidgets('the end of the rail has no forward arrow', (tester) async {
    final controller = await _pump(tester);
    await _hover(tester);
    controller.jumpTo(controller.position.maxScrollExtent);
    await tester.pumpAndSettle();

    expect(find.byTooltip('向后'), findsNothing);
    expect(find.byTooltip('向前'), findsOneWidget);
  });
}
