import 'package:dio/dio.dart';

import '../../core/api/api_error.dart';
import 'ai_stream_transport.dart';

AiStreamTransport createPlatformAiStreamTransport() => DioAiStreamTransport();

/// Android / 桌面：Dio 的 `ResponseType.stream`，字节随到随给。
/// 不设接收超时：模型思考时可以很久没有数据；用户随时能点「停止」。
class DioAiStreamTransport implements AiStreamTransport {
  DioAiStreamTransport({Dio? dio})
    : _dio =
          dio ?? Dio(BaseOptions(connectTimeout: const Duration(seconds: 10)));

  final Dio _dio;

  @override
  Future<AiStreamResponse> post(AiStreamRequest request, AiAbort abort) async {
    final cancel = CancelToken();
    abort.signal.then((_) {
      if (!cancel.isCancelled) cancel.cancel('stopped');
    });
    try {
      final response = await _dio.postUri<ResponseBody>(
        request.url,
        data: request.body,
        cancelToken: cancel,
        options: Options(
          headers: request.headers,
          responseType: ResponseType.stream,
          receiveTimeout: Duration.zero,
          validateStatus: (_) => true,
        ),
      );
      return AiStreamResponse(
        statusCode: response.statusCode ?? 0,
        body: response.data!.stream,
      );
    } on DioException catch (e) {
      if (CancelToken.isCancel(e)) rethrow;
      throw ApiError.from(e);
    }
  }
}
