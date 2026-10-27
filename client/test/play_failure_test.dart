import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/core/api/api_error.dart';
import 'package:iris/core/network_status.dart';
import 'package:iris/features/library/browse_api.dart';
import 'package:iris/features/play/play_launcher.dart';

void main() {
  final versions = [
    const MediaVersion(id: 1, label: '4K · 60 GB', subtitles: []),
    const MediaVersion(id: 2, label: '1080p · 8 GB', subtitles: []),
  ];

  test('network status warns when Wi-Fi changes to mobile data', () {
    expect(networkKindFrom([ConnectivityResult.wifi]), NetworkKind.wifi);
    expect(networkKindFrom([ConnectivityResult.mobile]), NetworkKind.mobile);
    expect(
      networkKindFrom([ConnectivityResult.wifi, ConnectivityResult.mobile]),
      NetworkKind.mobile,
    );
    expect(isCellularSwitch(NetworkKind.wifi, NetworkKind.mobile), isTrue);
    expect(isCellularSwitch(NetworkKind.offline, NetworkKind.mobile), isFalse);
  });

  test('temporary network failures offer retry', () {
    final info = playFailureInfo(
      const ApiError(code: 'network', message: '网络连接失败，请检查网络后重试'),
      canChooseVersion: false,
    );

    expect(info.recovery, PlayFailureRecovery.retry);
    expect(info.retryLabel, '重试');
  });

  test('error codes share stable client categories', () {
    expect(
      const ApiError(code: 'forbidden', message: '需要管理员权限').kind,
      ApiErrorKind.forbidden,
    );
    expect(
      const ApiError(code: 'resource_unavailable', message: '来源暂时不可用').kind,
      ApiErrorKind.resourceUnavailable,
    );
    expect(
      const ApiError(
        code: 'http_503',
        message: '服务暂时不可用',
        statusCode: 503,
      ).retryable,
      isTrue,
    );
  });

  test('resource error codes from the server map to their own categories', () {
    ApiErrorKind kind(String code, int status) =>
        ApiError(code: code, message: '', statusCode: status).kind;

    // 限流后端返回 503，不能被当成普通的服务端错误
    expect(kind('resource_rate_limited', 503), ApiErrorKind.rateLimited);
    expect(kind('resource_file_gone', 404), ApiErrorKind.missingFile);
    expect(kind('resource_unavailable', 503), ApiErrorKind.resourceUnavailable);
    expect(
      const ApiError(
        code: 'resource_rate_limited',
        message: '',
        statusCode: 503,
      ).retryable,
      isTrue,
    );
  });

  test('missing version offers a direct version switch', () {
    final info = playFailureInfo(
      const ApiError(code: 'not_found', message: '版本不存在或已失效'),
      canChooseVersion: versions.length > 1,
    );

    expect(info.recovery, PlayFailureRecovery.chooseVersion);
    expect(info.title, '这个版本无法播放');
  });

  test('rate limits include the wait time and remain retryable', () {
    final info = playFailureInfo(
      const ApiError(
        code: 'rate_limited',
        message: '请求太频繁，请稍后再试',
        statusCode: 429,
        retryAfter: 3,
      ),
      canChooseVersion: false,
    );

    expect(info.recovery, PlayFailureRecovery.retry);
    expect(info.retryLabel, '稍后重试');
    expect(info.message, contains('3 秒'));
  });

  test('missing public address can fall back to the built-in player', () {
    final info = playFailureInfo(
      const ApiError(
        code: 'public_url_not_configured',
        message: '管理员还没有配置对外访问地址，暂时不能播放',
      ),
      canChooseVersion: false,
      canUseBuiltin: true,
    );

    expect(info.recovery, PlayFailureRecovery.useBuiltin);
    expect(info.retryLabel, '改用内置播放器');
  });

  _more();
}

void _more() {
  test('expired token offers re-issue and hides the token in the reason', () {
    const error = ApiError(
      code: 'play_token_expired',
      message: 'http://host/play/SECRET-TOKEN 已过期',
      statusCode: 410,
    );
    final info = playFailureInfo(error, canChooseVersion: false);

    expect(info.title, '播放地址已过期');
    expect(info.recovery, PlayFailureRecovery.retry);
    expect(info.retryLabel, '重新获取');
    expect(info.reason, contains('play_token_expired'));
    expect(info.reason, isNot(contains('SECRET-TOKEN')));
    expect(info.reason, isNot(contains('http')));
  });

  test('resource risk control gets its own advice and reason', () {
    final info = playFailureInfo(
      const ApiError(
        code: 'resource_rate_limited',
        message: '来源触发风控',
        statusCode: 503,
      ),
      canChooseVersion: false,
    );

    expect(info.title, '资源来源暂时限制了访问');
    expect(info.message, contains('限制'));
    expect(info.recovery, PlayFailureRecovery.retry);
    expect(info.reason, contains('resource_rate_limited'));
    expect(info.reason, contains('HTTP 503'));
  });

  test('unsupported player falls back to built-in or closes', () {
    const error = ApiError(code: 'player_unsupported', message: 'x');
    final withBuiltin = playFailureInfo(
      error,
      canChooseVersion: false,
      canUseBuiltin: true,
    );
    final without = playFailureInfo(error, canChooseVersion: false);

    expect(withBuiltin.recovery, PlayFailureRecovery.useBuiltin);
    expect(without.recovery, PlayFailureRecovery.close);
    expect(without.reason, contains('player_unsupported'));
  });
}
