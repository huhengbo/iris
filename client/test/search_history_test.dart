import 'package:iris/features/search/search_page.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('keeps recent searches unique and bounded', () {
    final container = ProviderContainer();
    addTearDown(container.dispose);
    final history = container.read(searchHistoryProvider.notifier);

    for (var i = 0; i < 10; i++) {
      history.add('片名 $i');
    }
    history.add('片名 5');

    expect(container.read(searchHistoryProvider), [
      '片名 5',
      '片名 9',
      '片名 8',
      '片名 7',
      '片名 6',
      '片名 4',
      '片名 3',
      '片名 2',
    ]);
  });
}
