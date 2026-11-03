// Web 用 fetch 才能增量读取响应；其他平台走 Dio 的流式响应。
export 'ai_stream_transport_io.dart'
    if (dart.library.js_interop) 'ai_stream_transport_web.dart';
