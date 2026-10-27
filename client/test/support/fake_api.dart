import 'dart:convert';
import 'dart:typed_data';

import 'package:iris/app/theme.dart';
import 'package:iris/core/providers.dart';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

typedef Handler = (int, Object?) Function(Object? body);

/// 按「方法 路径」返回预设响应的假接口，并记录请求和请求体。
class FakeAdminApi implements HttpClientAdapter {
  final Map<String, Handler> routes = {};
  final List<String> requests = [];
  final Map<String, Object?> bodies = {};

  /// 额外的响应头，例如后台任务的 X-Task-Id
  final Map<String, Map<String, String>> responseHeaders = {};

  void on(
    String route,
    Object? body, {
    int status = 200,
    Map<String, String> headers = const {},
  }) {
    routes[route] = (_) => (status, body);
    responseHeaders[route] = headers;
  }

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    final query = options.queryParameters.entries
        .map((e) => '${e.key}=${e.value}')
        .join('&');
    final route =
        '${options.method} ${options.path}${query.isEmpty ? '' : '?$query'}';
    requests.add(route);
    Object? body;
    if (requestStream != null) {
      final raw = utf8.decode(
        await requestStream.expand((c) => c).toList(),
        allowMalformed: true,
      );
      // multipart 上传不是 JSON，原样记下文本
      body = (options.contentType ?? '').startsWith('multipart/')
          ? raw
          : jsonDecode(raw);
      bodies[route] = body;
    }
    final handler = routes[route];
    if (handler == null) {
      return _json(404, {
        'error': {'code': 'not_found', 'message': route},
      });
    }
    final (status, response) = handler(body);
    if (status == 204) return ResponseBody.fromString('', 204);
    return _json(status, response, responseHeaders[route] ?? const {});
  }

  ResponseBody _json(
    int status,
    Object? body, [
    Map<String, String> extra = const {},
  ]) => ResponseBody.fromString(
    jsonEncode(body),
    status,
    headers: {
      Headers.contentTypeHeader: [Headers.jsonContentType],
      for (final entry in extra.entries) entry.key: [entry.value],
    },
  );

  @override
  void close({bool force = false}) {}
}

Future<FakeAdminApi> pumpAdmin(
  WidgetTester tester,
  Widget page,
  void Function(FakeAdminApi api) setup, {
  List<dynamic> overrides = const [],
}) async {
  final api = FakeAdminApi();
  setup(api);
  final dio = Dio(BaseOptions(baseUrl: 'http://test'))..httpClientAdapter = api;
  tester.view.physicalSize = const Size(390 * 3, 1600 * 3);
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        apiClientProvider.overrideWithValue(dio),
        apiBaseUrlProvider.overrideWithValue('http://test'),
        ...overrides,
      ],
      child: MaterialApp(theme: buildTheme(), home: page),
    ),
  );
  await tester.pumpAndSettle();
  return api;
}
