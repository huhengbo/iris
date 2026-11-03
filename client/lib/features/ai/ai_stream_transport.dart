import 'dart:async';

/// 「停止」信号：触发后传输层必须关闭连接。
class AiAbort {
  final _completer = Completer<void>();

  /// 触发时完成；已触发过则立即完成。
  Future<void> get signal => _completer.future;

  bool get isAborted => _completer.isCompleted;

  void abort() {
    if (!_completer.isCompleted) _completer.complete();
  }
}

class AiStreamRequest {
  const AiStreamRequest({
    required this.url,
    required this.headers,
    required this.body,
  });

  final Uri url;
  final Map<String, String> headers;

  /// 已编码好的 JSON 文本
  final String body;
}

class AiStreamResponse {
  const AiStreamResponse({required this.statusCode, required this.body});

  final int statusCode;

  /// 响应体的原始字节，到达一块给一块（不是整体缓冲后才给）。
  final Stream<List<int>> body;
}

/// 发一个 POST 并以字节流读回响应的传输层。把「真流式」留给平台实现：
/// IO 用 Dio 的 `ResponseType.stream`，Web 用 `fetch` + `ReadableStream`
/// （Dio 在 Web 走 XHR，响应会被整体缓冲）。
///
/// - 非 2xx 不抛错，原样返回状态码和响应体，由调用方解析错误。
/// - 连接失败抛 `ApiError(code: 'network')`。
/// - [abort] 触发后关闭连接；此后 [AiStreamResponse.body] 以错误或结束收尾。
abstract interface class AiStreamTransport {
  Future<AiStreamResponse> post(AiStreamRequest request, AiAbort abort);
}
