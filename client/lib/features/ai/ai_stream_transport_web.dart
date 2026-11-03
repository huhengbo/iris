import 'dart:async';
import 'dart:js_interop';

import 'package:web/web.dart' as web;

import '../../core/api/api_error.dart';
import 'ai_stream_transport.dart';

AiStreamTransport createPlatformAiStreamTransport() => FetchAiStreamTransport();

/// Web：Dio 在浏览器里走 XHR，`ResponseType.stream` 也要等整个响应到齐，
/// 打字机效果会变成一次性出现。这里直接用 `fetch`，读 `response.body` 的
/// `ReadableStream`，每个块到达就往外给；`AbortController.abort()` 关闭连接。
class FetchAiStreamTransport implements AiStreamTransport {
  @override
  Future<AiStreamResponse> post(AiStreamRequest request, AiAbort abort) async {
    final controller = web.AbortController();
    abort.signal.then((_) => controller.abort());
    final headers = web.Headers();
    for (final entry in request.headers.entries) {
      headers.append(entry.key, entry.value);
    }
    final web.Response response;
    try {
      response = await web.window
          .fetch(
            request.url.toString().toJS,
            web.RequestInit(
              method: 'POST',
              headers: headers,
              body: request.body.toJS,
              signal: controller.signal,
            ),
          )
          .toDart;
    } on Object {
      // 已经被「停止」中止：交给上层按停止处理
      if (abort.isAborted) rethrow;
      throw const ApiError(code: 'network', message: '网络连接失败，请检查网络后重试');
    }
    final stream = response.body;
    if (stream == null) {
      return AiStreamResponse(
        statusCode: response.status,
        body: const Stream.empty(),
      );
    }
    return AiStreamResponse(
      statusCode: response.status,
      body: _chunks(stream, abort),
    );
  }

  /// 逐块读取。订阅者取消（或停止）时 cancel reader，浏览器随之关闭连接。
  Stream<List<int>> _chunks(web.ReadableStream stream, AiAbort abort) {
    final reader = stream.getReader() as web.ReadableStreamDefaultReader;
    late final StreamController<List<int>> out;
    var cancelled = false;

    Future<void> pump() async {
      try {
        while (!cancelled) {
          final result = await reader.read().toDart;
          if (result.done) break;
          final value = result.value;
          if (value != null && !cancelled) {
            out.add((value as JSUint8Array).toDart);
          }
        }
        if (!cancelled) await out.close();
      } on Object catch (e, stack) {
        if (cancelled) return;
        if (abort.isAborted) {
          await out.close();
        } else {
          out.addError(
            ApiError(code: 'network', message: '网络连接失败，请检查网络后重试'),
            stack,
          );
          await out.close();
        }
      }
    }

    out = StreamController<List<int>>(
      onListen: () => unawaited(pump()),
      onCancel: () async {
        cancelled = true;
        try {
          await reader.cancel().toDart;
        } on Object {
          // 连接已断开，没有更多要清理的
        }
      },
    );
    return out.stream;
  }
}
