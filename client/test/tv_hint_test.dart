import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/app/tv.dart';

void main() {
  testWidgets('TV remote hint appears without taking the page focus', (
    tester,
  ) async {
    final pageFocus = FocusNode();
    addTearDown(pageFocus.dispose);
    await tester.pumpWidget(
      MaterialApp(
        builder: (context, child) => MediaQuery(
          data: televisionMediaQuery(MediaQuery.of(context)),
          child: TvRemoteHint(
            enabled: true,
            child: Focus(
              autofocus: true,
              focusNode: pageFocus,
              child: const SizedBox.expand(),
            ),
          ),
        ),
        home: const SizedBox(),
      ),
    );
    await tester.pump();

    expect(find.textContaining('方向键移动'), findsOneWidget);
    expect(pageFocus.hasPrimaryFocus, isTrue);

    await tester.tap(find.textContaining('方向键移动'));
    await tester.pump();
    expect(find.textContaining('方向键移动'), findsNothing);
    expect(pageFocus.hasPrimaryFocus, isTrue);
  });
}
