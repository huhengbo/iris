import 'dart:convert';
import 'dart:typed_data';

import 'package:iris/core/api/api_client.dart';
import 'package:iris/core/api/token_store.dart';
import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';

/// 模拟后端：只接受当前有效的 access token，refresh token 可以换一次新令牌。
class FakeBackend implements HttpClientAdapter {
  FakeBackend({required this.validAccess, required this.validRefresh});

  String validAccess;
  String? validRefresh;
  int refreshCalls = 0;
  bool refreshUnreachable = false;
  Map<String, dynamic>? lastRefreshBody;
  final List<String?> seenAuthorization = [];

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    if (options.path == refreshPath) {
      if (refreshUnreachable) {
        throw DioException.connectionError(
          requestOptions: options,
          reason: 'unreachable',
        );
      }
      refreshCalls++;
      final body = jsonDecode(
        utf8.decode(await requestStream!.expand((c) => c).toList()),
      ) as Map<String, dynamic>;
      lastRefreshBody = body;
      if (validRefresh == null || body['refresh_token'] != validRefresh) {
        return _json(401, {
          'error': {'code': 'invalid_token', 'message': '登录已失效'},
        });
      }
      validAccess = 'access-$refreshCalls';
      validRefresh = 'refresh-$refreshCalls';
      return _json(200, {
        'access_token': validAccess,
        'refresh_token': validRefresh,
      });
    }
    final auth = options.headers['Authorization'] as String?;
    seenAuthorization.add(auth);
    if (auth != 'Bearer $validAccess') {
      return _json(401, {
        'error': {'code': 'invalid_token', 'message': '登录已失效'},
      });
    }
    return _json(200, {'ok': true});
  }

  ResponseBody _json(int status, Object body) => ResponseBody.fromString(
    jsonEncode(body),
    status,
    headers: {
      Headers.contentTypeHeader: [Headers.jsonContentType],
    },
  );

  @override
  void close({bool force = false}) {}
}

void main() {
  late FakeBackend backend;
  late MemoryTokenStore store;
  late int expiredSignals;
  late Dio dio;

  setUp(() {
    backend = FakeBackend(validAccess: 'access-0', validRefresh: 'refresh-0');
    store = MemoryTokenStore(
      const TokenPair(accessToken: 'stale', refreshToken: 'refresh-0'),
    );
    expiredSignals = 0;
    dio = createApiClient(
      baseUrl: 'http://backend.test',
      tokenStore: store,
      onSessionExpired: () => expiredSignals++,
      adapter: backend,
    );
  });

  test(
    'expired access token is refreshed once and the request retried',
    () async {
      final response = await dio.get<Map<String, dynamic>>('/api/me');

      expect(response.data, {'ok': true});
      expect(backend.refreshCalls, 1);
      expect((await store.read())!.accessToken, 'access-1');
      expect(expiredSignals, 0);
    },
  );

  test('refresh reports this device so older sessions get named', () async {
    dio = createApiClient(
      baseUrl: 'http://backend.test',
      tokenStore: store,
      onSessionExpired: () => expiredSignals++,
      deviceInfo: () async => {'device_id': 'device-1', 'platform': 'Web'},
      adapter: backend,
    );

    await dio.get<Map<String, dynamic>>('/api/me');

    expect(backend.lastRefreshBody, {
      'refresh_token': 'refresh-0',
      'device_id': 'device-1',
      'platform': 'Web',
    });
  });

  test('concurrent 401s share a single refresh', () async {
    final results = await Future.wait([
      for (var i = 0; i < 5; i++) dio.get<Map<String, dynamic>>('/api/me'),
    ]);

    expect(results.map((r) => r.statusCode), everyElement(200));
    expect(backend.refreshCalls, 1);
  });

  test('rejected refresh token clears session and signals expiry', () async {
    backend.validRefresh = null;

    await expectLater(
      dio.get<Map<String, dynamic>>('/api/me'),
      throwsA(isA<DioException>()),
    );
    expect(await store.read(), isNull);
    expect(expiredSignals, 1);
  });

  test('network failure during refresh keeps the tokens', () async {
    backend.refreshUnreachable = true;

    await expectLater(
      dio.get<Map<String, dynamic>>('/api/me'),
      throwsA(isA<DioException>()),
    );
    expect((await store.read())!.refreshToken, 'refresh-0');
    expect(expiredSignals, 0);
  });

  test(
    'requests marked skipAuth carry no token and are never refreshed',
    () async {
      await expectLater(
        dio.get<Map<String, dynamic>>(
          '/api/me',
          options: Options(extra: {skipAuthExtra: true}),
        ),
        throwsA(isA<DioException>()),
      );
      expect(backend.seenAuthorization, [null]);
      expect(backend.refreshCalls, 0);
    },
  );
}
