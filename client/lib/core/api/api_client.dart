import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart' show kIsWeb;

import 'token_store.dart';

const refreshPath = '/api/auth/refresh';

/// 标记不需要携带 access token 的请求（登录、刷新）。
const skipAuthExtra = 'skipAuth';
const _retriedExtra = 'authRetried';

/// 创建业务用的 Dio：自动带 Bearer，遇到 401 用 refresh token 换新令牌后重试一次。
/// 刷新失败（refresh token 也失效）时清空令牌并调用 [onSessionExpired]。
Dio createApiClient({
  required String baseUrl,
  required TokenStore tokenStore,
  required void Function() onSessionExpired,
  Future<Map<String, String>> Function()? deviceInfo,
  HttpClientAdapter? adapter,
}) {
  final options = BaseOptions(
    baseUrl: baseUrl,
    // Web 端 Dio 把 connectTimeout 当作「等到响应头」的期限，后端要等插件返回的请求
    // （扫码长轮询约 30 秒）会被提前中断；浏览器自己处理连接超时，只保留总的接收超时
    connectTimeout: kIsWeb ? null : const Duration(seconds: 10),
    receiveTimeout: const Duration(seconds: 30),
    contentType: Headers.jsonContentType,
  );
  Dio plain() {
    final dio = Dio(options);
    if (adapter != null) dio.httpClientAdapter = adapter;
    return dio;
  }

  final dio = plain();
  dio.interceptors.add(
    AuthInterceptor(
      tokenStore: tokenStore,
      // 刷新和重试走不带拦截器的实例：在排队拦截器里回调同一实例会互相等待而死锁
      plainClient: plain(),
      onSessionExpired: onSessionExpired,
      deviceInfo: deviceInfo,
    ),
  );
  dio.interceptors.add(_BrowseCacheInterceptor());
  return dio;
}

/// 对浏览接口复用 ETag，避免每次返回页面都重新下载相同 JSON。
/// 缓存只存在当前 Dio 实例内，并把 Authorization 纳入 key，避免账号切换串数据。
class _BrowseCacheInterceptor extends Interceptor {
  final _entries = <String, _CachedResponse>{};

  @override
  void onRequest(RequestOptions options, RequestInterceptorHandler handler) {
    if (_isCacheable(options)) {
      final entry = _entries[_key(options)];
      if (entry != null) options.headers['If-None-Match'] = entry.etag;
    }
    handler.next(options);
  }

  @override
  void onResponse(
    Response<dynamic> response,
    ResponseInterceptorHandler handler,
  ) {
    final options = response.requestOptions;
    final etag = response.headers.value('etag');
    if (_isCacheable(options) && etag != null && response.statusCode == 200) {
      _entries[_key(options)] = _CachedResponse(
        etag,
        response.data,
        response.headers,
      );
    }
    handler.next(response);
  }

  @override
  void onError(DioException error, ErrorInterceptorHandler handler) {
    final options = error.requestOptions;
    final entry = _isCacheable(options) ? _entries[_key(options)] : null;
    if (error.response?.statusCode == 304 && entry != null) {
      handler.resolve(
        Response<dynamic>(
          requestOptions: options,
          data: entry.data,
          headers: entry.headers,
          statusCode: 200,
        ),
      );
      return;
    }
    handler.next(error);
  }

  bool _isCacheable(RequestOptions options) {
    if (options.method.toUpperCase() != 'GET') return false;
    final path = options.uri.path;
    return path == '/api/home' ||
        path == '/api/libraries' ||
        path.startsWith('/api/libraries/') ||
        path.startsWith('/api/items/') ||
        path.startsWith('/api/seasons/') ||
        path.startsWith('/api/people/') ||
        path == '/api/search';
  }

  String _key(RequestOptions options) =>
      '${options.uri}|${options.headers['Authorization'] ?? ''}';
}

class _CachedResponse {
  const _CachedResponse(this.etag, this.data, this.headers);

  final String etag;
  final dynamic data;
  final Headers headers;
}

class AuthInterceptor extends QueuedInterceptor {
  AuthInterceptor({
    required this.tokenStore,
    required this.plainClient,
    required this.onSessionExpired,
    this.deviceInfo,
  });

  final TokenStore tokenStore;
  final Dio plainClient;
  final void Function() onSessionExpired;

  /// 续期时一并上报的本机信息，服务端据此补全升级前登录的会话
  final Future<Map<String, String>> Function()? deviceInfo;

  @override
  Future<void> onRequest(
    RequestOptions options,
    RequestInterceptorHandler handler,
  ) async {
    if (options.extra[skipAuthExtra] != true) {
      final tokens = await tokenStore.read();
      if (tokens != null) {
        options.headers['Authorization'] = 'Bearer ${tokens.accessToken}';
      }
    }
    handler.next(options);
  }

  @override
  Future<void> onError(
    DioException err,
    ErrorInterceptorHandler handler,
  ) async {
    final options = err.requestOptions;
    if (err.response?.statusCode != 401 ||
        options.extra[skipAuthExtra] == true ||
        options.extra[_retriedExtra] == true) {
      handler.next(err);
      return;
    }
    final tokens = await tokenStore.read();
    if (tokens == null) {
      onSessionExpired();
      handler.next(err);
      return;
    }
    var current = tokens;
    // 排队期间其他请求可能已经刷新过，此时直接用新令牌重试，避免重复刷新
    if (options.headers['Authorization'] == 'Bearer ${tokens.accessToken}') {
      final refreshed = await _refresh(tokens);
      if (refreshed == null) {
        handler.next(err);
        return;
      }
      current = refreshed;
    }
    options.headers['Authorization'] = 'Bearer ${current.accessToken}';
    options.extra[_retriedExtra] = true;
    try {
      handler.resolve(await plainClient.fetch<dynamic>(options));
    } on DioException catch (retryError) {
      handler.next(retryError);
    }
  }

  Future<TokenPair?> _refresh(TokenPair tokens) async {
    try {
      final response = await plainClient.post<Map<String, dynamic>>(
        refreshPath,
        data: {
          'refresh_token': tokens.refreshToken,
          ...?await deviceInfo?.call(),
        },
      );
      final data = response.data!;
      final refreshed = TokenPair(
        accessToken: data['access_token'] as String,
        refreshToken: data['refresh_token'] as String,
      );
      await tokenStore.write(refreshed);
      return refreshed;
    } on DioException catch (e) {
      // 只有服务端明确拒绝时才视为登录失效；网络错误保留令牌，下次再试
      final status = e.response?.statusCode;
      if (status != null && status >= 400 && status < 500) {
        await tokenStore.clear();
        onSessionExpired();
      }
      return null;
    }
  }
}
