import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/core/api/api_error.dart';
import 'package:iris/core/api/token_store.dart';
import 'package:iris/features/ai/ai_chat_api.dart';
import 'package:iris/features/ai/ai_chat_models.dart';
import 'package:iris/features/ai/ai_sse.dart';
import 'package:iris/features/ai/ai_stream_transport.dart';
import 'package:iris/features/ai/ai_stream_transport_io.dart';

String frame(String event, Object data) =>
    'event: $event\ndata: ${jsonEncode(data)}\n\n';

Future<List<SseFrame>> parse(Iterable<List<int>> chunks) =>
    sseFrames(Stream.fromIterable(chunks)).toList();

/// 把字节串按给定位置切开。
List<List<int>> splitAt(List<int> bytes, List<int> cuts) {
  final out = <List<int>>[];
  var start = 0;
  for (final cut in [...cuts, bytes.length]) {
    out.add(bytes.sublist(start, cut));
    start = cut;
  }
  return out;
}

void main() {
  group('SseParser', () {
    test('parses one event with event and data lines', () {
      final frames = SseParser().add('event: text\ndata: {"delta":"嗨"}\n\n');
      expect(frames, hasLength(1));
      expect(frames.single.event, 'text');
      expect(frames.single.data, '{"delta":"嗨"}');
    });

    test('an event split across many chunks is assembled', () {
      final parser = SseParser();
      const text =
          'event: text\ndata: {"delta":"你好"}\n\nevent: done\ndata: {}\n\n';
      final frames = <SseFrame>[];
      // 一个字符一个字符地喂
      for (final rune in text.runes) {
        frames.addAll(parser.add(String.fromCharCode(rune)));
      }
      expect(frames.map((f) => f.event), ['text', 'done']);
      expect(frames.first.data, '{"delta":"你好"}');
    });

    test('CRLF, lone CR and a CRLF cut between chunks', () {
      final parser = SseParser();
      final frames = [
        ...parser.add('event: a\r\ndata: 1\r\n\r\n'),
        ...parser.add('event: b\rdata: 2\r\r'),
        // \r\n 被切开：\r 在上一块，\n 在下一块
        ...parser.add('event: c\r'),
        ...parser.add('\ndata: 3\r'),
        ...parser.add('\n\r'),
        ...parser.add('\n'),
      ];
      expect(frames.map((f) => '${f.event}:${f.data}'), ['a:1', 'b:2', 'c:3']);
    });

    test('comment lines and keep-alives are ignored', () {
      final frames = SseParser().add(
        ': ping\n\nevent: text\n: 中间的注释\ndata: x\n\n:\n\n',
      );
      expect(frames.map((f) => '${f.event}:${f.data}'), ['text:x']);
    });

    test('event without data does not dispatch and does not leak', () {
      final frames = SseParser().add('event: text\n\ndata: y\n\n');
      // 第一个事件没有 data 被丢弃，名字也不带到下一个事件
      expect(frames.map((f) => '${f.event}:${f.data}'), ['message:y']);
    });

    test('multiple data lines are joined with newline; one leading space', () {
      final frames = SseParser().add('data: a\ndata:  b\ndata:c\n\n');
      expect(frames.single.data, 'a\n b\nc');
    });

    test('a half event at the end of the stream is not dispatched', () {
      final parser = SseParser();
      expect(parser.add('event: text\ndata: {"del'), isEmpty);
    });

    test('BOM at the start is skipped', () {
      final frames = SseParser().add('\uFEFFevent: a\ndata: 1\n\n');
      expect(frames.single.event, 'a');
    });
  });

  group('sseFrames (bytes)', () {
    test(
      'multi-byte characters cut between chunks are decoded intact',
      () async {
        final bytes = utf8.encode(frame('text', {'delta': '光圈 ✨你好'}));
        // 在每一个字节之后都切一刀，覆盖所有可能切断多字节字符的位置
        final frames = await parse([
          for (final b in bytes) [b],
        ]);
        expect(frames, hasLength(1));
        expect(jsonDecode(frames.single.data), {'delta': '光圈 ✨你好'});
      },
    );

    test('cuts at arbitrary positions give the same result', () async {
      final bytes = utf8.encode(
        frame('text', {'delta': '第一段'}) + frame('text', {'delta': '第二段🎬'}),
      );
      final expected = await parse([bytes]);
      for (var a = 1; a < bytes.length - 1; a += 3) {
        final got = await parse(
          splitAt(bytes, [a, (a + 7).clamp(0, bytes.length - 1)]),
        );
        expect(
          got.map((f) => f.data).toList(),
          expected.map((f) => f.data).toList(),
          reason: 'cut at $a',
        );
      }
    });
  });

  group('aiEvents', () {
    Future<List<AiEvent>> events(String text) =>
        aiEvents(Stream.value(utf8.encode(text)), 'http://test').toList();

    test('maps every documented event', () async {
      final got = await events(
        frame('start', {'chat_id': 1, 'message_id': 2}) +
            frame('thinking', {'delta': '想', 'seconds': null}) +
            frame('thinking', {'delta': '', 'seconds': 1.5}) +
            frame('tool_start', {
              'id': 'c1',
              'name': 'search_library',
              'label': '正在搜索…',
            }) +
            frame('tool_end', {
              'id': 'c1',
              'name': 'search_library',
              'summary': '找到 1 部',
              'ok': true,
              'items': [
                {
                  'id': 5,
                  'library_id': 1,
                  'kind': 'movie',
                  'title': '流浪地球',
                  'genres': <String>[],
                  'poster': <Object>[],
                  'backdrop': <Object>[],
                  'watched': false,
                },
              ],
            }) +
            frame('text', {'delta': '好'}) +
            frame('suggestions', {
              'items': ['再来一部'],
            }) +
            frame('done', {
              'message_id': 3,
              'input_tokens': 10,
              'output_tokens': 20,
            }) +
            frame('error', {'code': 'internal', 'message': '出错了'}),
      );
      expect(got.map((e) => e.runtimeType), [
        AiStarted,
        AiThinking,
        AiThinking,
        AiToolStarted,
        AiToolEnded,
        AiTextDelta,
        AiSuggestions,
        AiDone,
        AiFailed,
      ]);
      expect((got[1] as AiThinking).seconds, isNull);
      expect((got[2] as AiThinking).seconds, 1.5);
      final end = got[4] as AiToolEnded;
      expect(end.items.single.title, '流浪地球');
      expect(end.ok, isTrue);
    });

    test('unknown events and non-object data are ignored', () async {
      final got = await events(
        'event: ping\ndata: not json\n\n'
        'event: future_thing\ndata: {"a":1}\n\n'
        'event: text\ndata: [1,2]\n\n'
        '${frame('text', {'delta': 'ok'})}',
      );
      expect(got, hasLength(1));
      expect((got.single as AiTextDelta).delta, 'ok');
    });

    test('a known event with broken JSON fails the stream', () async {
      expect(
        events('event: text\ndata: {"delta":\n\n'),
        throwsA(isA<FormatException>()),
      );
    });
  });

  group('AiChatApi', () {
    late _ScriptedTransport transport;
    late MemoryTokenStore tokens;
    late _AuthMeAdapter adapter;

    AiChatApi api() {
      final dio = Dio(BaseOptions(baseUrl: 'http://test'))
        ..httpClientAdapter = adapter;
      return AiChatApi(
        dio: dio,
        tokens: tokens,
        baseUrl: 'http://test',
        transport: transport,
      );
    }

    setUp(() {
      transport = _ScriptedTransport();
      tokens = MemoryTokenStore(
        const TokenPair(accessToken: 'old', refreshToken: 'r'),
      );
      adapter = _AuthMeAdapter(tokens);
    });

    test('sends the bearer token and the JSON body', () async {
      transport.responses.add(
        _body(
          200,
          frame('done', {
            'message_id': 1,
            'input_tokens': 0,
            'output_tokens': 0,
          }),
        ),
      );
      final got = await api().sendMessage(7, '你好', AiAbort()).toList();
      expect(got.single, isA<AiDone>());
      final request = transport.requests.single;
      expect(request.url.toString(), 'http://test/api/ai/chats/7/messages');
      expect(request.headers['Authorization'], 'Bearer old');
      expect(request.headers['Accept'], 'text/event-stream');
      expect(jsonDecode(request.body), {'content': '你好'});
    });

    test('refreshes the token once on 401 and retries', () async {
      transport.responses
        ..add(_body(401, '{"error":{"code":"unauthorized","message":"x"}}'))
        ..add(_body(200, frame('text', {'delta': 'a'})));
      final got = await api().sendMessage(1, 'q', AiAbort()).toList();
      expect((got.single as AiTextDelta).delta, 'a');
      expect(adapter.refreshed, 1);
      expect(transport.requests.map((r) => r.headers['Authorization']), [
        'Bearer old',
        'Bearer new',
      ]);
    });

    test('a second 401 is reported, not retried again', () async {
      transport.responses
        ..add(_body(401, '{"error":{"code":"unauthorized","message":"登录过期"}}'))
        ..add(_body(401, '{"error":{"code":"unauthorized","message":"登录过期"}}'));
      await expectLater(
        api().sendMessage(1, 'q', AiAbort()).toList(),
        throwsA(
          isA<ApiError>()
              .having((e) => e.statusCode, 'status', 401)
              .having((e) => e.message, 'message', '登录过期'),
        ),
      );
      expect(transport.requests, hasLength(2));
    });

    test('HTTP errors become ApiError with the server message', () async {
      transport.responses.add(
        _body(
          409,
          '{"error":{"code":"ai_not_configured","message":"还没有配置 AI"}}',
        ),
      );
      await expectLater(
        api().sendMessage(1, 'q', AiAbort()).toList(),
        throwsA(
          isA<ApiError>()
              .having((e) => e.code, 'code', 'ai_not_configured')
              .having((e) => e.message, 'message', '还没有配置 AI'),
        ),
      );
    });

    test('non-JSON error bodies fall back to the status code', () async {
      transport.responses.add(_body(502, '<html>bad gateway</html>'));
      await expectLater(
        api().sendMessage(1, 'q', AiAbort()).toList(),
        throwsA(
          isA<ApiError>()
              .having((e) => e.code, 'code', 'http_502')
              .having((e) => e.kind, 'kind', ApiErrorKind.server),
        ),
      );
    });
  });

  group('DioAiStreamTransport (real socket)', () {
    test(
      'delivers chunks as they arrive, not after the response ends',
      () async {
        final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
        addTearDown(() => server.close(force: true));
        final release = Completer<void>();
        String? auth;
        String? requestBody;
        unawaited(
          server.forEach((request) async {
            auth = request.headers.value('authorization');
            requestBody = await utf8.decoder.bind(request).join();
            request.response
              ..headers.contentType = ContentType(
                'text',
                'event-stream',
                charset: 'utf-8',
              )
              ..bufferOutput = false
              ..write(frame('text', {'delta': '第一'}));
            await request.response.flush();
            // 第二个事件要等客户端确认收到第一个之后才发：如果传输层缓冲到结束，这里会死锁
            await release.future;
            request.response.write(
              frame('done', {
                'message_id': 1,
                'input_tokens': 0,
                'output_tokens': 0,
              }),
            );
            await request.response.close();
          }),
        );

        final transport = DioAiStreamTransport();
        final response = await transport.post(
          AiStreamRequest(
            url: Uri.parse(
              'http://127.0.0.1:${server.port}/api/ai/chats/1/messages',
            ),
            headers: {
              'Content-Type': 'application/json',
              'Authorization': 'Bearer t',
            },
            body: '{"content":"嗨"}',
          ),
          AiAbort(),
        );
        expect(response.statusCode, 200);
        final seen = <String>[];
        await for (final event in aiEvents(response.body, 'http://test')) {
          seen.add(event.runtimeType.toString());
          if (event is AiTextDelta) release.complete();
        }
        expect(seen, ['AiTextDelta', 'AiDone']);
        expect(auth, 'Bearer t');
        expect(requestBody, '{"content":"嗨"}');
      },
    );

    test('abort closes the connection', () async {
      // 用裸 socket 当服务端，才能看到客户端是否真的断开了 TCP 连接
      final server = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
      addTearDown(server.close);
      final closed = Completer<void>();
      server.listen((socket) {
        socket.listen((_) {
          socket.add(
            utf8.encode(
              'HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\n'
              'transfer-encoding: chunked\r\n\r\n6\r\nhello\n\r\n',
            ),
          );
        }, onDone: closed.complete);
      });
      final abort = AiAbort();
      final response = await DioAiStreamTransport().post(
        AiStreamRequest(
          url: Uri.parse('http://127.0.0.1:${server.port}/x'),
          headers: const {},
          body: '{}',
        ),
        abort,
      );
      final first = Completer<void>();
      response.body.listen((_) {
        if (!first.isCompleted) first.complete();
      }, onError: (Object _) {});
      await first.future;
      expect(closed.isCompleted, isFalse);
      abort.abort();
      await closed.future.timeout(const Duration(seconds: 5));
    });

    test('connection failure is reported as a network error', () async {
      final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
      final port = server.port;
      await server.close(force: true);
      await expectLater(
        DioAiStreamTransport().post(
          AiStreamRequest(
            url: Uri.parse('http://127.0.0.1:$port/x'),
            headers: const {},
            body: '{}',
          ),
          AiAbort(),
        ),
        throwsA(
          isA<ApiError>().having((e) => e.kind, 'kind', ApiErrorKind.network),
        ),
      );
    });
  });
}

AiStreamResponse _body(int status, String text) =>
    AiStreamResponse(statusCode: status, body: Stream.value(utf8.encode(text)));

class _ScriptedTransport implements AiStreamTransport {
  final responses = <AiStreamResponse>[];
  final requests = <AiStreamRequest>[];

  @override
  Future<AiStreamResponse> post(AiStreamRequest request, AiAbort abort) async {
    requests.add(request);
    return responses.removeAt(0);
  }
}

/// 只会回答 `/api/auth/me`：被调用时把令牌存储换成新的，模拟拦截器刷新。
class _AuthMeAdapter implements HttpClientAdapter {
  _AuthMeAdapter(this.tokens);

  final MemoryTokenStore tokens;
  int refreshed = 0;

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<List<int>>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    if (options.path == '/api/auth/me') {
      refreshed++;
      await tokens.write(
        const TokenPair(accessToken: 'new', refreshToken: 'r'),
      );
      return ResponseBody.fromString(
        '{}',
        200,
        headers: {
          Headers.contentTypeHeader: [Headers.jsonContentType],
        },
      );
    }
    return ResponseBody.fromString('{}', 404);
  }

  @override
  void close({bool force = false}) {}
}
