import 'package:flutter_test/flutter_test.dart';
import 'package:iris/core/api/user_agent.dart';

void main() {
  test('browsers are told apart even though their user agents overlap', () {
    const mac =
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
        '(KHTML, like Gecko)';
    expect(
      describeUserAgent('$mac Chrome/130.0 Safari/537.36').name,
      'Chrome · macOS',
    );
    expect(
      describeUserAgent('$mac Chrome/130.0 Safari/537.36 Edg/130.0').name,
      'Edge · macOS',
    );
    expect(
      describeUserAgent('$mac Version/18.0 Safari/605.1.15').name,
      'Safari · macOS',
    );
    expect(
      describeUserAgent(
        'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 '
        '(KHTML, like Gecko) CriOS/130.0 Mobile/15E148 Safari/604.1',
      ).name,
      'Chrome · iOS',
    );
    expect(
      describeUserAgent(
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0',
      ).name,
      'Firefox · Windows',
    );
    expect(describeUserAgent('curl/8.0').name, '浏览器');
    expect(describeUserAgent('curl/8.0').platform, 'Web');
  });
}
