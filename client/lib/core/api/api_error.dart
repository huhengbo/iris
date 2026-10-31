import 'package:dio/dio.dart';

enum ApiErrorKind {
  network,
  unauthorized,
  forbidden,
  rateLimited,
  resourceUnavailable,
  missingFile,
  notFound,
  conflict,
  validation,
  server,
  unknown,
}

/// 后端统一错误格式 {"error": {"code", "message"}} 在前端的表示。
class ApiError implements Exception {
  const ApiError({
    required this.code,
    required this.message,
    this.statusCode,
    this.retryAfter,
  });

  final String code;
  final String message;
  final int? statusCode;
  final int? retryAfter;

  bool get isUnauthorized => statusCode == 401;

  ApiErrorKind get kind {
    if (code == 'network') return ApiErrorKind.network;
    if (code == 'resource_unavailable') {
      return ApiErrorKind.resourceUnavailable;
    }
    if (code == 'file_missing' ||
        code == 'resource_file_gone' ||
        code == 'file_not_found' ||
        code == 'playable_missing') {
      return ApiErrorKind.missingFile;
    }
    if (statusCode == 401) return ApiErrorKind.unauthorized;
    if (statusCode == 403 || code == 'forbidden') return ApiErrorKind.forbidden;
    if (statusCode == 429 ||
        code == 'rate_limited' ||
        code == 'resource_rate_limited') {
      return ApiErrorKind.rateLimited;
    }
    if (statusCode == 404 ||
        code == 'not_found' ||
        code.endsWith('_not_found')) {
      return ApiErrorKind.notFound;
    }
    if (statusCode == 409 ||
        code.endsWith('_conflict') ||
        code.endsWith('_in_use') ||
        code.endsWith('_running') ||
        code == 'sync_running') {
      return ApiErrorKind.conflict;
    }
    if (statusCode == 400 ||
        statusCode == 422 ||
        code == 'validation_error' ||
        code.startsWith('package_')) {
      return ApiErrorKind.validation;
    }
    if (statusCode != null && statusCode! >= 500) return ApiErrorKind.server;
    return ApiErrorKind.unknown;
  }

  /// 是否值得在当前页面提供“重试”；具体动作由场景决定。
  bool get retryable => switch (kind) {
    ApiErrorKind.network ||
    ApiErrorKind.rateLimited ||
    ApiErrorKind.resourceUnavailable ||
    ApiErrorKind.server => true,
    _ => code == 'play_token_expired' || code == 'play_link_invalid',
  };

  static ApiError from(Object error) {
    if (error is ApiError) return error;
    if (error is DioException) {
      final response = error.response;
      final data = response?.data;
      if (data is Map && data['error'] is Map) {
        final body = data['error'] as Map;
        return ApiError(
          code: '${body['code'] ?? 'unknown'}',
          message: '${body['message'] ?? '请求失败'}',
          statusCode: response?.statusCode,
          retryAfter: _retryAfter(response),
        );
      }
      if (response != null) {
        return ApiError(
          code: 'http_${response.statusCode}',
          message: '服务器返回错误（${response.statusCode}）',
          statusCode: response.statusCode,
          retryAfter: _retryAfter(response),
        );
      }
      return const ApiError(code: 'network', message: '网络连接失败，请检查网络后重试');
    }
    return const ApiError(code: 'unknown', message: '发生未知错误');
  }

  @override
  String toString() => 'ApiError($code, $statusCode): $message';
}

int? _retryAfter(Response<dynamic>? response) =>
    int.tryParse(response?.headers.value('retry-after') ?? '');
