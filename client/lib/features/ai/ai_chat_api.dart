import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../core/api/token_store.dart';
import '../../core/providers.dart';
import 'ai_chat_models.dart';
import 'ai_sse.dart';
import 'ai_stream_transport.dart';
import 'ai_stream_transport_factory.dart';

/// 平台传输层。测试里覆盖成假的，用它喂事件序列。
final aiStreamTransportProvider = Provider<AiStreamTransport>(
  (ref) => createPlatformAiStreamTransport(),
);

final aiChatApiProvider = Provider<AiChatApi>(
  (ref) => AiChatApi(
    dio: ref.watch(apiClientProvider),
    tokens: ref.watch(tokenStoreProvider),
    baseUrl: ref.watch(apiBaseUrlProvider),
    transport: ref.watch(aiStreamTransportProvider),
  ),
);

/// 当前用户的会话列表，打开历史面板时读取。
final aiChatListProvider = FutureProvider.autoDispose<List<AiChatSummary>>(
  (ref) => ref.watch(aiChatApiProvider).listChats(),
);

/// AI 对话的接口：会话 REST，以及 `POST /api/ai/chats/{id}/messages` 的 SSE 流。
///
/// REST 走带鉴权拦截器的 [Dio]；SSE 走 [AiStreamTransport]（不经过 Dio 的 XHR），
/// 所以令牌在这里手动附加：先读存储里的访问令牌，401 时借一次受保护请求触发
/// 拦截器刷新（刷新逻辑和排队只在 `AuthInterceptor` 一处），再用新令牌重发一次。
class AiChatApi {
  AiChatApi({
    required this.dio,
    required this.tokens,
    required this.baseUrl,
    required this.transport,
  });

  final Dio dio;
  final TokenStore tokens;
  final String baseUrl;
  final AiStreamTransport transport;

  /// 最近更新的在前。
  Future<List<AiChatSummary>> listChats() async {
    final response = await dio.get<Map<String, dynamic>>('/api/ai/chats');
    return [
      for (final chat in response.data!['chats'] as List<dynamic>)
        AiChatSummary.fromJson(chat as Map<String, dynamic>),
    ];
  }

  Future<AiChatSummary> createChat() async {
    final response = await dio.post<Map<String, dynamic>>('/api/ai/chats');
    return AiChatSummary.fromJson(response.data!);
  }

  Future<AiChatDetail> getChat(int id, AiKeys keys) async {
    final response = await dio.get<Map<String, dynamic>>('/api/ai/chats/$id');
    return AiChatDetail.fromJson(response.data!, baseUrl, keys);
  }

  Future<void> deleteChat(int id) => dio.delete<void>('/api/ai/chats/$id');

  /// 发一条消息并返回回答的事件流。取消订阅或 [abort] 都会断开连接，
  /// 服务端把已生成的部分存为「已停止」。HTTP 错误和连接失败抛 [ApiError]。
  Stream<AiEvent> sendMessage(
    int chatId,
    String content,
    AiAbort abort,
  ) async* {
    var response = await _open(chatId, content, abort);
    if (response.statusCode == 401) {
      // 不读这份响应体：取消订阅让连接释放
      response.body.listen(null).cancel().ignore();
      await _refreshTokens();
      response = await _open(chatId, content, abort);
    }
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw await _errorOf(response);
    }
    yield* aiEvents(response.body, baseUrl);
  }

  Future<AiStreamResponse> _open(
    int chatId,
    String content,
    AiAbort abort,
  ) async {
    final pair = await tokens.read();
    return transport.post(
      AiStreamRequest(
        url: Uri.parse('$baseUrl/api/ai/chats/$chatId/messages'),
        headers: {
          'Content-Type': Headers.jsonContentType,
          'Accept': 'text/event-stream',
          'Cache-Control': 'no-cache',
          if (pair != null) 'Authorization': 'Bearer ${pair.accessToken}',
        },
        body: jsonEncode({'content': content}),
      ),
      abort,
    );
  }

  /// 令牌过期时，用任一受保护的轻量请求触发拦截器的「刷新并重试」。
  /// 刷新失败时拦截器已清空令牌并通知登录失效，这里抛出的 401 交给调用方展示。
  Future<void> _refreshTokens() async {
    try {
      await dio.get<void>('/api/auth/me');
    } on DioException catch (e) {
      throw ApiError.from(e);
    }
  }

  Future<ApiError> _errorOf(AiStreamResponse response) async {
    final bytes = <int>[];
    try {
      await for (final chunk in response.body) {
        bytes.addAll(chunk);
        if (bytes.length > 64 * 1024) break;
      }
      final body = jsonDecode(utf8.decode(bytes, allowMalformed: true));
      if (body is Map && body['error'] is Map) {
        final error = body['error'] as Map;
        return ApiError(
          code: '${error['code'] ?? 'unknown'}',
          message: '${error['message'] ?? '请求失败'}',
          statusCode: response.statusCode,
        );
      }
    } on Object {
      // 响应体不是约定的错误格式：只用状态码
    }
    return ApiError(
      code: 'http_${response.statusCode}',
      message: '服务器返回错误（${response.statusCode}）',
      statusCode: response.statusCode,
    );
  }
}
