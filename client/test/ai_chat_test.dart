import 'dart:async';
import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/misc.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:iris/app/tv.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/core/api/api_error.dart';
import 'package:iris/core/api/token_store.dart';
import 'package:iris/core/providers.dart';
import 'package:iris/core/server.dart';
import 'package:iris/core/widgets/ai_wait_indicator.dart';
import 'package:iris/features/ai/ai_availability.dart';
import 'package:iris/features/ai/ai_chat_api.dart';
import 'package:iris/features/ai/ai_chat_controller.dart';
import 'package:iris/features/ai/ai_chat_drawer.dart';
import 'package:iris/features/ai/ai_chat_page.dart';
import 'package:iris/features/ai/ai_chat_view.dart';
import 'package:iris/features/ai/ai_chat_widgets.dart';
import 'package:iris/features/ai/ai_stream_transport.dart';
import 'package:iris/features/auth/auth_controller.dart';
import 'package:iris/features/ai/ai_entry_button.dart';

import 'support/fake_api.dart';

// ---- 假传输层：测试自己决定什么时候推哪个事件 ----

String frame(String event, Object data) =>
    'event: $event\ndata: ${jsonEncode(data)}\n\n';

class FakeCall {
  FakeCall(this.request, this.abort);

  final AiStreamRequest request;
  final AiAbort abort;
  final body = StreamController<List<int>>();

  String get content =>
      (jsonDecode(request.body) as Map<String, dynamic>)['content'] as String;
  bool get aborted => abort.isAborted;

  void emit(String event, Object data) =>
      body.add(utf8.encode(frame(event, data)));

  void end() => body.close();
}

class FakeAiTransport implements AiStreamTransport {
  final calls = <FakeCall>[];

  /// 依次抛给后面的 post，模拟连不上
  final failures = <Object>[];

  @override
  Future<AiStreamResponse> post(AiStreamRequest request, AiAbort abort) async {
    if (failures.isNotEmpty) throw failures.removeAt(0);
    final call = FakeCall(request, abort);
    calls.add(call);
    // 真实传输层在 abort 后会关掉连接
    abort.signal.then((_) {
      if (!call.body.isClosed) call.body.close();
    });
    return AiStreamResponse(statusCode: 200, body: call.body.stream);
  }
}

const networkDown = ApiError(code: 'network', message: '网络连接失败，请检查网络后重试');

// ---- 数据 ----

Map<String, Object?> chatJson(int id, {String title = '周末片单'}) => {
  'id': id,
  'title': title,
  'created_at': '2026-10-03T10:00:00Z',
  'updated_at': '2026-10-03T10:00:00Z',
};

Map<String, Object?> card(int id, String title) => {
  'id': id,
  'library_id': 1,
  'kind': 'movie',
  'title': title,
  'genres': <String>[],
  'poster': <Object>[],
  'backdrop': <Object>[],
  'watched': false,
};

Map<String, Object?> userMessage(int id, String text) => {
  'id': id,
  'role': 'user',
  'content': text,
  'steps': <Object?>[],
  'items': <Object?>[],
  'suggestions': <String>[],
  'thinking_seconds': null,
  'status': 'done',
  'error': null,
  'created_at': '2026-10-03T10:00:00Z',
};

Map<String, Object?> assistantMessage(
  int id,
  String text, {
  String status = 'done',
  String? error,
  double? thinking,
  List<Map<String, Object?>> steps = const [],
  List<Map<String, Object?>> items = const [],
  List<String> suggestions = const [],
}) => {
  ...userMessage(id, text),
  'role': 'assistant',
  'steps': steps,
  'items': items,
  'suggestions': suggestions,
  'thinking_seconds': thinking,
  'status': status,
  'error': error,
};

Map<String, Object?> detailJson(int id, List<Map<String, Object?>> messages) =>
    {...chatJson(id), 'messages': messages};

Map<String, Object?> done([int id = 99]) => {
  'message_id': id,
  'input_tokens': 1,
  'output_tokens': 1,
};

final sendButton = find.widgetWithIcon(IconButton, Icons.arrow_upward_rounded);

const starter = '推荐一部适合周末看的轻松电影';

// ---- 测试环境 ----

class _NoUser extends AuthController {
  @override
  Future<AppUser?> build() async => null;
}

class Harness {
  Harness(this.api, this.transport, this.router, this.tester);

  final FakeAdminApi api;
  final FakeAiTransport transport;
  final GoRouter router;
  final WidgetTester tester;

  FakeCall get call => transport.calls.last;

  ProviderContainer get container =>
      ProviderScope.containerOf(tester.element(find.byType(MaterialApp)));

  String get location => router.routerDelegate.state.uri.toString();

  /// 推一个事件并让界面跟上。
  Future<void> emit(String event, Object data) async {
    call.emit(event, data);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));
  }

  /// 推一个完整的 `done` 并等所有动画（卡片淡入）走完，但不能 pumpAndSettle（等待指示无限循环）。
  Future<void> finish() async {
    await emit('done', done());
    await tester.pump(const Duration(milliseconds: 800));
  }
}

Future<Harness> pumpChat(
  WidgetTester tester, {
  String? question,
  String location = '/ai',
  void Function(FakeAdminApi api)? setup,
  bool reduced = false,
  Size size = const Size(390, 844),
  bool emptyHistory = true,
  List<Override> overrides = const [],
}) async {
  final api = FakeAdminApi();
  if (emptyHistory) {
    api
      ..on('GET /api/ai/chats', {'chats': <Object?>[]})
      ..on('POST /api/ai/chats', chatJson(1), status: 201);
  }
  setup?.call(api);
  final dio = Dio(BaseOptions(baseUrl: 'http://test'))..httpClientAdapter = api;
  final transport = FakeAiTransport();
  final router = GoRouter(
    initialLocation: question == null
        ? location
        : '/ai?q=${Uri.encodeQueryComponent(question)}',
    routes: [
      GoRoute(
        path: '/',
        builder: (context, _) => Scaffold(
          body: Center(
            child: TextButton(
              onPressed: () => showAiChatDrawer(context),
              child: const Text('打开抽屉'),
            ),
          ),
        ),
      ),
      GoRoute(
        path: '/ai',
        builder: (_, state) =>
            AiChatPage(initialQuestion: state.uri.queryParameters['q']),
      ),
      GoRoute(
        path: '/search',
        builder: (_, state) =>
            Scaffold(body: Text('搜索页 ${state.uri.queryParameters['q']}')),
      ),
      GoRoute(
        path: '/item/:id',
        builder: (_, state) =>
            Scaffold(body: Text('详情页 ${state.pathParameters['id']}')),
      ),
    ],
  );
  addTearDown(router.dispose);
  tester.view.physicalSize = size * 2;
  tester.view.devicePixelRatio = 2;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        apiClientProvider.overrideWithValue(dio),
        apiBaseUrlProvider.overrideWithValue('http://test'),
        tokenStoreProvider.overrideWithValue(
          MemoryTokenStore(
            const TokenPair(accessToken: 'a', refreshToken: 'r'),
          ),
        ),
        aiStreamTransportProvider.overrideWithValue(transport),
        aiReconnectDelayProvider.overrideWithValue(Duration.zero),
        authControllerProvider.overrideWith(_NoUser.new),
        ...overrides,
      ],
      child: MaterialApp.router(
        theme: buildTheme().copyWith(splashFactory: NoSplash.splashFactory),
        routerConfig: router,
        builder: (context, child) => MediaQuery(
          data: MediaQuery.of(context).copyWith(disableAnimations: reduced),
          child: child!,
        ),
      ),
    ),
  );
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 50));
  return Harness(api, transport, router, tester);
}

/// 点一个入门问句 / 建议胶囊，让请求发出去。
Future<void> tapText(WidgetTester tester, String text) async {
  await tester.tap(find.text(text));
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 50));
}

double opacityOf(WidgetTester tester, int card) => tester
    .widget<FadeTransition>(
      find
          .descendant(
            of: find.byType(AiCardReveal).at(card),
            matching: find.byType(FadeTransition),
          )
          .first,
    )
    .opacity
    .value;

void main() {
  group('empty chat', () {
    testWidgets('shows the starter questions and nothing else', (tester) async {
      final h = await pumpChat(tester);
      for (final q in aiStarterQuestions) {
        expect(find.text(q), findsOneWidget);
      }
      expect(aiStarterQuestions, hasLength(4));
      expect(find.text('想看点什么？'), findsOneWidget);
      // 空对话不会在服务端建会话
      expect(h.api.requests, ['GET /api/ai/chats']);
      expect(h.transport.calls, isEmpty);
    });

    testWidgets('tapping a starter creates the chat and streams the answer', (
      tester,
    ) async {
      final h = await pumpChat(tester);
      await tapText(tester, starter);

      expect(h.api.requests, contains('POST /api/ai/chats'));
      expect(h.transport.calls, hasLength(1));
      expect(h.call.content, starter);
      expect(
        h.call.request.url.toString(),
        'http://test/api/ai/chats/1/messages',
      );
      expect(h.call.request.headers['Authorization'], 'Bearer a');
      // 用户气泡，入门问句收起，等待指示
      expect(find.text(starter), findsOneWidget);
      expect(find.text('想看点什么？'), findsNothing);
      expect(find.text('正在思考…'), findsOneWidget);
      expect(find.text('停止'), findsOneWidget);
    });
  });

  group('a full round', () {
    testWidgets('thinking, steps, cards, text, suggestions, done', (
      tester,
    ) async {
      final h = await pumpChat(tester);
      await tapText(tester, starter);
      await h.emit('start', {'chat_id': 1, 'message_id': 10});

      // 思考中：只有等待指示
      await h.emit('thinking', {'delta': '用户想找轻松的', 'seconds': null});
      expect(find.text('正在思考…'), findsOneWidget);
      expect(find.textContaining('已思考'), findsNothing);
      await h.emit('thinking', {'delta': '', 'seconds': 2.4});
      // 思考结束：收成一行，可展开看原文
      expect(find.text('已思考 2 秒'), findsOneWidget);
      expect(find.text('用户想找轻松的'), findsNothing);
      await tester.tap(find.text('已思考 2 秒'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 300));
      expect(find.text('用户想找轻松的'), findsOneWidget);
      await tester.tap(find.text('已思考 2 秒'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 300));
      expect(find.text('用户想找轻松的'), findsNothing);

      // 过渡语会在 tool_start 时被丢弃
      await h.emit('text', {'delta': '我来查一下'});
      expect(find.text('我来查一下'), findsOneWidget);
      await h.emit('tool_start', {
        'id': 'c1',
        'name': 'search_library',
        'label': '正在搜索资料库…',
      });
      expect(find.text('我来查一下'), findsNothing);
      expect(find.text('正在搜索资料库…'), findsOneWidget);

      await h.emit('tool_end', {
        'id': 'c1',
        'name': 'search_library',
        'summary': '找到 2 部',
        'ok': true,
        'items': [card(5, '喜剧之王'), card(6, '功夫')],
      });
      // 步骤完成：文案换成结果；卡片立刻出现，在正文之前
      expect(find.text('正在搜索资料库…'), findsNothing);
      expect(find.text('找到 2 部'), findsOneWidget);
      expect(find.text('喜剧之王'), findsOneWidget);
      expect(find.text('功夫'), findsOneWidget);

      await h.emit('text', {'delta': '为你找到 **2 部**：\n\n- 第一项\n- '});
      await h.emit('text', {'delta': '第二项'});
      // 正文开始后步骤收成一行
      expect(find.text('查了 1 步 · 找到 2 部'), findsOneWidget);
      expect(find.text('找到 2 部'), findsNothing);
      expect(find.textContaining('为你找到'), findsOneWidget);
      expect(find.textContaining('**'), findsNothing);
      expect(find.text('第一项'), findsOneWidget);
      expect(find.text('第二项'), findsOneWidget);
      // 卡片在正文之上
      expect(
        tester.getTopLeft(find.text('喜剧之王')).dy,
        lessThan(tester.getTopLeft(find.textContaining('为你找到')).dy),
      );

      await h.emit('suggestions', {
        'items': ['再来点冷门的'],
      });
      // 没结束前不显示建议
      expect(find.text('再来点冷门的'), findsNothing);
      await h.finish();

      expect(find.text('再来点冷门的'), findsOneWidget);
      expect(find.text('停止'), findsNothing);
      expect(find.byTooltip('发送'), findsOneWidget);
      // 点步骤收起行展开看每一步
      await tester.tap(find.text('查了 1 步 · 找到 2 部'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 300));
      expect(find.text('找到 2 部'), findsOneWidget);

      // 点建议直接发送
      await tapText(tester, '再来点冷门的');
      expect(h.transport.calls, hasLength(2));
      expect(h.call.content, '再来点冷门的');
      // 旧的建议在新一轮开始后消失
      expect(find.text('再来点冷门的'), findsOneWidget); // 只剩用户气泡
    });

    testWidgets('failed step shows the reason with a warm red icon', (
      tester,
    ) async {
      final h = await pumpChat(tester);
      await tapText(tester, starter);
      await h.emit('tool_start', {
        'id': 'c1',
        'name': 'search_library',
        'label': '正在搜索资料库…',
      });
      await h.emit('tool_end', {
        'id': 'c1',
        'name': 'search_library',
        'summary': '资料库暂时无法搜索',
        'ok': false,
        'items': <Object?>[],
      });
      expect(find.text('资料库暂时无法搜索'), findsOneWidget);
      final icon = tester.widget<Icon>(find.byIcon(Icons.error_outline));
      expect(icon.color, CinemaColors.error);
    });

    testWidgets('cards fade in one by one, 40ms apart', (tester) async {
      final h = await pumpChat(tester);
      await tapText(tester, starter);
      await h.emit('tool_start', {'id': 'c1', 'name': 'x', 'label': '找片…'});
      emitToolEnd(h, [card(1, 'A'), card(2, 'B'), card(3, 'C')]);
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 20));
      // 第一张已经开始淡入，第二张还在等自己的 40ms
      expect(opacityOf(tester, 0), greaterThan(0));
      expect(opacityOf(tester, 1), 0);
      await tester.pump(const Duration(milliseconds: 40));
      expect(opacityOf(tester, 1), greaterThan(0));
      expect(opacityOf(tester, 2), 0);
      await tester.pump(const Duration(milliseconds: 600));
      for (var i = 0; i < 3; i++) {
        expect(opacityOf(tester, i), 1);
      }
      // 上移 4%
      expect(
        find.descendant(
          of: find.byType(AiCardReveal),
          matching: find.byType(SlideTransition),
        ),
        findsWidgets,
      );
    });

    testWidgets('more than 10 items end with a "view all" card', (
      tester,
    ) async {
      final h = await pumpChat(tester);
      await tapText(tester, starter);
      await h.emit('tool_start', {'id': 'c1', 'name': 'x', 'label': '找片…'});
      emitToolEnd(h, [for (var i = 1; i <= 12; i++) card(i, '片$i')]);
      await tester.pump();
      await tester.pump(const Duration(seconds: 2));
      await h.emit('text', {'delta': '找到了'});

      // 横向列表是懒加载的，滚到最后
      await tester.drag(find.byType(AiItemRow), const Offset(-3000, 0));
      await tester.pump(const Duration(milliseconds: 600));
      expect(find.text('查看全部'), findsOneWidget);
      expect(find.text('12 部'), findsOneWidget);
      expect(find.text('片11'), findsNothing);

      await tester.tap(find.text('查看全部'));
      await tester.pumpAndSettleSafe();
      expect(h.location, '/search?q=${Uri.encodeQueryComponent(starter)}');
      expect(find.text('搜索页 $starter'), findsOneWidget);
    });

    testWidgets('tapping a card opens the item and the chat survives', (
      tester,
    ) async {
      final h = await pumpChat(tester);
      await tapText(tester, starter);
      await h.emit('tool_start', {'id': 'c1', 'name': 'x', 'label': '找片…'});
      emitToolEnd(h, [card(5, '喜剧之王')]);
      await tester.pump(const Duration(seconds: 1));
      await h.emit('text', {'delta': '给你'});
      await h.finish();

      await tester.tap(find.text('喜剧之王'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      expect(h.location, '/item/5');
      expect(find.text('详情页 5'), findsOneWidget);

      h.router.pop();
      await tester.pump(const Duration(milliseconds: 500));
      expect(find.text('喜剧之王'), findsOneWidget);
      expect(find.text('给你'), findsOneWidget);
      expect(h.api.requests.where((r) => r == 'GET /api/ai/chats/1'), isEmpty);
    });
  });

  group('stop', () {
    testWidgets('closes the connection, keeps the text, marks it stopped', (
      tester,
    ) async {
      final h = await pumpChat(tester);
      await tapText(tester, starter);
      await h.emit('text', {'delta': '这是一部'});
      expect(find.text('停止'), findsOneWidget);

      await tester.tap(find.text('停止'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));

      expect(h.call.aborted, isTrue);
      expect(find.text('这是一部'), findsOneWidget);
      expect(find.text('已停止'), findsOneWidget);
      expect(find.text('停止'), findsNothing);
      expect(find.byTooltip('发送'), findsOneWidget);
      // 停止后可以继续提问
      await tester.enterText(find.byType(TextField), '换一部');
      await tester.pump();
      await tester.tap(find.byTooltip('发送'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));
      expect(h.transport.calls, hasLength(2));
      expect(h.call.content, '换一部');
    });

    testWidgets('stopping while only waiting still shows 已停止', (tester) async {
      final h = await pumpChat(tester);
      await tapText(tester, starter);
      await tester.tap(find.text('停止'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));
      expect(find.text('已停止'), findsOneWidget);
      expect(find.text('正在思考…'), findsNothing);
      expect(h.call.aborted, isTrue);
    });

    testWidgets('running steps do not keep spinning after stop', (
      tester,
    ) async {
      final h = await pumpChat(tester);
      await tapText(tester, starter);
      await h.emit('tool_start', {
        'id': 'c1',
        'name': 'x',
        'label': '正在搜索资料库…',
      });
      expect(find.byType(AiWaitIndicator), findsOneWidget);
      await tester.tap(find.text('停止'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));
      expect(find.byType(AiWaitIndicator), findsNothing);
    });

    testWidgets('leaving the page disconnects', (tester) async {
      final h = await pumpChat(tester);
      await tapText(tester, starter);
      await tester.pumpWidget(const SizedBox());
      await tester.pump();
      expect(h.transport.calls.single.aborted, isTrue);
    });
  });

  group('errors', () {
    testWidgets('error event: keeps what was generated, shows retry', (
      tester,
    ) async {
      final h = await pumpChat(tester);
      await tapText(tester, starter);
      await h.emit('text', {'delta': '已经生成的内容'});
      await h.emit('error', {
        'code': 'ai_unavailable',
        'message': 'AI 暂不可用：超时',
      });

      expect(find.text('已经生成的内容'), findsOneWidget);
      expect(find.text('AI 暂不可用：超时'), findsOneWidget);
      expect(find.text('重试'), findsOneWidget);
      expect(find.byIcon(Icons.error_outline), findsOneWidget);
      expect(find.text('停止'), findsNothing);

      // 重试：同一个问题再问一次，原内容保留，新回答接在后面
      await tester.tap(find.text('重试'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));
      expect(h.transport.calls, hasLength(2));
      expect(h.call.content, starter);
      expect(find.text('已经生成的内容'), findsOneWidget);
      expect(find.text('重试'), findsNothing); // 旧的错误不再提供重试
      await h.emit('text', {'delta': '这次好了'});
      await h.finish();
      expect(find.text('这次好了'), findsOneWidget);
      expect(find.text('已经生成的内容'), findsOneWidget);
      expect(find.text('重试'), findsNothing);
    });

    testWidgets('HTTP error (409 ai_not_configured) shows the message', (
      tester,
    ) async {
      final h = await pumpChat(
        tester,
        setup: (api) => api.on('POST /api/ai/chats', {
          'error': {'code': 'ai_not_configured', 'message': '管理员还没有配置 AI'},
        }, status: 409),
      );
      await tapText(tester, starter);
      expect(find.text('管理员还没有配置 AI'), findsOneWidget);
      expect(find.text('重试'), findsOneWidget);
      expect(h.transport.calls, isEmpty);
    });

    testWidgets('a dropped network reconnects once and succeeds', (
      tester,
    ) async {
      final h = await pumpChat(tester);
      h.transport.failures.add(networkDown);
      await tapText(tester, starter);
      await tester.pump(const Duration(milliseconds: 50));
      // 第一次连不上，自动重连，没有显示错误
      expect(find.text('重试'), findsNothing);
      expect(find.text(networkDown.message), findsNothing);
      expect(h.transport.calls, hasLength(1));
      await h.emit('text', {'delta': '重连后的回答'});
      await h.finish();
      expect(find.text('重连后的回答'), findsOneWidget);
      // 只有一个用户气泡
      expect(find.text(starter), findsOneWidget);
    });

    testWidgets('a stream cut mid-way reconnects and starts the text over', (
      tester,
    ) async {
      final h = await pumpChat(tester);
      await tapText(tester, starter);
      await h.emit('text', {'delta': '说到一半'});
      expect(find.text('说到一半'), findsOneWidget);
      h.call.end(); // 连接断了，没有 done
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));
      expect(h.transport.calls, hasLength(2));
      expect(find.text('说到一半'), findsNothing);
      await h.emit('text', {'delta': '重新开始'});
      await h.finish();
      expect(find.text('重新开始'), findsOneWidget);
      expect(find.text('重试'), findsNothing);
    });

    testWidgets('failing twice shows the network error with retry', (
      tester,
    ) async {
      final h = await pumpChat(tester);
      h.transport.failures.addAll([networkDown, networkDown]);
      await tapText(tester, starter);
      await tester.pump(const Duration(milliseconds: 100));
      expect(find.text(networkDown.message), findsOneWidget);
      expect(find.byIcon(Icons.wifi_off_rounded), findsOneWidget);
      expect(find.text('重试'), findsOneWidget);

      // 网络恢复后点重试
      await tester.tap(find.text('重试'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));
      await h.emit('text', {'delta': '好了'});
      await h.finish();
      expect(find.text('好了'), findsOneWidget);
    });

    testWidgets('a server error is not retried automatically', (tester) async {
      final h = await pumpChat(tester);
      h.transport.failures.add(
        const ApiError(code: 'internal', message: '服务器出错了', statusCode: 500),
      );
      await tapText(tester, starter);
      await tester.pump(const Duration(milliseconds: 100));
      expect(find.text('服务器出错了'), findsOneWidget);
      expect(h.transport.calls, isEmpty);
    });
  });

  group('duplicate sends', () {
    testWidgets('sending twice in a row only asks once', (tester) async {
      final h = await pumpChat(tester);
      final notifier = h.container.read(
        aiChatControllerProvider(null).notifier,
      );
      final first = notifier.send('第一问');
      final second = notifier.send('第一问');
      final third = notifier.send('第二问');
      await tester.pump(const Duration(milliseconds: 50));
      expect(h.transport.calls, hasLength(1));
      expect(
        h.api.requests.where((r) => r == 'POST /api/ai/chats'),
        hasLength(1),
      );
      expect(find.text('第一问'), findsOneWidget);
      expect(find.text('第二问'), findsNothing);
      await h.finish();
      await Future.wait([first, second, third]);
    });

    testWidgets(
      'the send button turns into stop, so a second tap cannot send',
      (tester) async {
        final h = await pumpChat(tester);
        await tester.enterText(find.byType(TextField), '你好');
        await tester.pump();
        await tester.tap(find.byTooltip('发送'));
        await tester.pump();
        await tester.pump(const Duration(milliseconds: 50));
        expect(find.byTooltip('发送'), findsNothing);
        expect(find.text('停止'), findsOneWidget);
        expect(
          tester.widget<TextField>(find.byType(TextField)).controller!.text,
          '',
        );
        expect(h.transport.calls, hasLength(1));
      },
    );

    testWidgets('empty or blank input cannot be sent', (tester) async {
      final h = await pumpChat(tester);
      expect(tester.widget<IconButton>(sendButton).onPressed, isNull);
      await tester.enterText(find.byType(TextField), '   ');
      await tester.pump();
      await h.container
          .read(aiChatControllerProvider(null).notifier)
          .send('  ');
      expect(h.transport.calls, isEmpty);
    });

    testWidgets('Enter on the keyboard sends and keeps focus', (tester) async {
      final h = await pumpChat(tester);
      await tester.tap(find.byType(TextField));
      await tester.enterText(find.byType(TextField), '键盘发送');
      await tester.testTextInput.receiveAction(TextInputAction.send);
      await tester.pump(const Duration(milliseconds: 50));
      expect(h.transport.calls.single.content, '键盘发送');
      expect(
        tester.widget<TextField>(find.byType(TextField)).focusNode?.hasFocus ??
            FocusManager.instance.primaryFocus != null,
        isTrue,
      );
    });
  });

  group('opening', () {
    testWidgets('initial question creates a new chat and sends it', (
      tester,
    ) async {
      final h = await pumpChat(
        tester,
        question: '最近有什么高分科幻片',
        setup: (api) => api.on('GET /api/ai/chats', {
          'chats': [chatJson(7)],
        }),
      );
      // 不去读旧会话
      expect(h.api.requests, isNot(contains('GET /api/ai/chats')));
      expect(h.api.requests, contains('POST /api/ai/chats'));
      expect(h.transport.calls.single.content, '最近有什么高分科幻片');
      expect(find.text('最近有什么高分科幻片'), findsOneWidget);
      expect(find.text('想看点什么？'), findsNothing);
    });

    testWidgets('continues the most recent chat', (tester) async {
      final h = await pumpChat(
        tester,
        setup: (api) => api
          ..on('GET /api/ai/chats', {
            'chats': [chatJson(7), chatJson(3, title: '更早的')],
          })
          ..on(
            'GET /api/ai/chats/7',
            detailJson(7, [
              userMessage(1, '推荐点什么'),
              assistantMessage(2, '看看《功夫》'),
            ]),
          ),
      );
      expect(find.text('推荐点什么'), findsOneWidget);
      expect(find.text('看看《功夫》'), findsOneWidget);
      expect(find.text('想看点什么？'), findsNothing);
      // 接着问：用的是已有的会话，不新建
      await tester.enterText(find.byType(TextField), '再来一部');
      await tester.pump();
      await tester.tap(find.byTooltip('发送'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));
      expect(h.call.request.url.path, '/api/ai/chats/7/messages');
      expect(h.api.requests, isNot(contains('POST /api/ai/chats')));
    });

    testWidgets('a failed load shows the error and retries', (tester) async {
      final h = await pumpChat(
        tester,
        emptyHistory: false,
        setup: (api) => api.on('GET /api/ai/chats', {
          'error': {'code': 'internal', 'message': '服务暂时不可用'},
        }, status: 500),
      );
      expect(find.text('服务暂时不可用'), findsOneWidget);
      expect(find.byTooltip('发送'), findsOneWidget);
      expect(tester.widget<IconButton>(sendButton).onPressed, isNull);
      h.api.on('GET /api/ai/chats', {'chats': <Object?>[]});
      await tester.tap(find.text('重试'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));
      expect(find.text('想看点什么？'), findsOneWidget);
    });
  });

  group('history messages', () {
    testWidgets('render steps, cards, thinking seconds, stopped and error', (
      tester,
    ) async {
      await pumpChat(
        tester,
        setup: (api) => api
          ..on('GET /api/ai/chats', {
            'chats': [chatJson(7)],
          })
          ..on(
            'GET /api/ai/chats/7',
            detailJson(7, [
              userMessage(1, '推荐轻松的'),
              assistantMessage(
                2,
                '给你 **两部**',
                thinking: 3.0,
                steps: [
                  {
                    'name': 'search_library',
                    'label': '正在搜索资料库…',
                    'summary': '找到 2 部',
                    'ok': true,
                    'item_ids': [5, 6],
                  },
                ],
                items: [card(5, '喜剧之王'), card(6, '功夫')],
                suggestions: ['旧的建议'],
              ),
              userMessage(3, '再推荐'),
              assistantMessage(4, '我想想', status: 'stopped'),
              userMessage(5, '继续'),
              assistantMessage(6, '说到一半', status: 'error', error: 'AI 暂不可用：超时'),
            ]),
          ),
      );
      await tester.pump(const Duration(seconds: 1));

      expect(find.text('已思考 3 秒'), findsOneWidget);
      // 历史消息只有秒数，不能展开
      expect(find.byIcon(Icons.expand_more), findsOneWidget); // 只有步骤行那一个
      expect(find.text('查了 1 步 · 找到 2 部'), findsOneWidget);
      expect(find.text('找到 2 部'), findsNothing);
      expect(find.text('喜剧之王'), findsOneWidget);
      expect(find.text('功夫'), findsOneWidget);
      expect(find.textContaining('给你'), findsOneWidget);
      expect(find.textContaining('**'), findsNothing);
      // 历史会话里的卡片不播入场动画
      expect(find.byType(AiCardReveal), findsNothing);

      // 只有最后一条助手消息有重试，也只有它可能有建议
      expect(find.text('旧的建议'), findsNothing);
      expect(find.text('我想想'), findsOneWidget);
      expect(find.text('已停止'), findsOneWidget);
      expect(find.text('说到一半'), findsOneWidget);
      expect(find.text('AI 暂不可用：超时'), findsOneWidget);
      expect(find.text('重试'), findsOneWidget);
      expect(find.text('停止'), findsNothing);
    });

    testWidgets('retry on a history error resends the question before it', (
      tester,
    ) async {
      final h = await pumpChat(
        tester,
        setup: (api) => api
          ..on('GET /api/ai/chats', {
            'chats': [chatJson(7)],
          })
          ..on(
            'GET /api/ai/chats/7',
            detailJson(7, [
              userMessage(1, '推荐轻松的'),
              assistantMessage(2, '', status: 'error', error: '出了点问题，请稍后重试'),
            ]),
          ),
      );
      await tester.tap(find.text('重试'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));
      expect(h.call.content, '推荐轻松的');
      expect(h.call.request.url.path, '/api/ai/chats/7/messages');
    });

    testWidgets('suggestions of the last answer are clickable', (tester) async {
      final h = await pumpChat(
        tester,
        setup: (api) => api
          ..on('GET /api/ai/chats', {
            'chats': [chatJson(7)],
          })
          ..on(
            'GET /api/ai/chats/7',
            detailJson(7, [
              userMessage(1, '推荐'),
              assistantMessage(2, '好的', suggestions: ['更短的', '更老的']),
            ]),
          ),
      );
      expect(find.text('更短的'), findsOneWidget);
      await tester.tap(find.text('更老的'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));
      expect(h.call.content, '更老的');
    });
  });

  group('menu', () {
    Future<void> openMenu(WidgetTester tester, String item) async {
      await tester.tap(find.byTooltip('更多'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 600));
      await tester.tap(find.text(item));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 400));
    }

    testWidgets('new chat stops the answer and clears the screen', (
      tester,
    ) async {
      final h = await pumpChat(tester);
      await tapText(tester, starter);
      await h.emit('text', {'delta': '回答中'});
      await openMenu(tester, '新对话');
      expect(h.transport.calls.single.aborted, isTrue);
      expect(find.text('回答中'), findsNothing);
      expect(find.text('想看点什么？'), findsOneWidget);
      // 新对话再发送会建新会话
      h.api.on('POST /api/ai/chats', chatJson(2), status: 201);
      await tapText(tester, aiStarterQuestions[1]);
      expect(h.call.request.url.path, '/api/ai/chats/2/messages');
    });

    testWidgets('history lists chats, switches to one, deletes after confirm', (
      tester,
    ) async {
      final h = await pumpChat(
        tester,
        setup: (api) => api
          ..on('GET /api/ai/chats', {
            'chats': [chatJson(7, title: '周末片单'), chatJson(3, title: '科幻')],
          })
          ..on('GET /api/ai/chats/7', detailJson(7, [userMessage(1, '第七个')]))
          ..on('GET /api/ai/chats/3', detailJson(3, [userMessage(1, '第三个')]))
          ..on('DELETE /api/ai/chats/3', null, status: 204),
      );
      expect(find.text('第七个'), findsOneWidget);
      await openMenu(tester, '历史对话');
      expect(find.text('周末片单'), findsOneWidget);
      expect(find.text('科幻'), findsOneWidget);

      // 删除前要确认
      await tester.tap(find.byTooltip('删除').last);
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 300));
      expect(find.text('删除对话「科幻」？'), findsOneWidget);
      await tester.tap(find.text('取消'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 300));
      expect(h.api.requests, isNot(contains('DELETE /api/ai/chats/3')));

      await tester.tap(find.byTooltip('删除').last);
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 300));
      await tester.tap(find.text('删除'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 300));
      expect(h.api.requests, contains('DELETE /api/ai/chats/3'));

      // 切换到另一个会话
      await tester.tap(find.text('周末片单').last);
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      expect(find.text('第七个'), findsOneWidget);
    });

    testWidgets('deleting the current chat returns to an empty chat', (
      tester,
    ) async {
      final h = await pumpChat(
        tester,
        setup: (api) => api
          ..on('GET /api/ai/chats', {
            'chats': [chatJson(7)],
          })
          ..on('GET /api/ai/chats/7', detailJson(7, [userMessage(1, '第七个')]))
          ..on('DELETE /api/ai/chats/7', null, status: 204),
      );
      await openMenu(tester, '历史对话');
      await tester.tap(find.byTooltip('删除'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 300));
      await tester.tap(find.text('删除'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      expect(h.api.requests, contains('DELETE /api/ai/chats/7'));
      expect(find.text('第七个'), findsNothing);
      expect(find.text('想看点什么？'), findsOneWidget);
    });

    testWidgets('history shows an empty hint', (tester) async {
      await pumpChat(tester);
      await openMenu(tester, '历史对话');
      expect(find.text('还没有历史对话'), findsOneWidget);
    });
  });

  group('scrolling', () {
    List<Map<String, Object?>> longHistory() => [
      for (var i = 0; i < 20; i++) ...[
        userMessage(i * 2 + 1, '问题 $i'),
        assistantMessage(i * 2 + 2, '回答 $i\n\n${'这是一段很长的回答。' * 12}'),
      ],
    ];

    ScrollPosition position(WidgetTester tester) => tester
        .state<ScrollableState>(
          find
              .descendant(
                of: find.byType(AiChatView),
                matching: find.byType(Scrollable),
              )
              .first,
        )
        .position;

    testWidgets('starts at the bottom; scrolling up shows 回到最新', (
      tester,
    ) async {
      await pumpChat(
        tester,
        setup: (api) => api
          ..on('GET /api/ai/chats', {
            'chats': [chatJson(7)],
          })
          ..on('GET /api/ai/chats/7', detailJson(7, longHistory())),
      );
      await tester.pump(const Duration(milliseconds: 100));
      expect(position(tester).extentAfter, lessThan(1));
      expect(find.byTooltip('回到最新'), findsNothing);

      await tester.drag(
        find
            .descendant(
              of: find.byType(AiChatView),
              matching: find.byType(ListView),
            )
            .first,
        const Offset(0, 400),
      );
      await tester.pump(const Duration(milliseconds: 400));
      expect(position(tester).extentAfter, greaterThan(100));
      expect(find.byTooltip('回到最新'), findsOneWidget);

      await tester.tap(find.byTooltip('回到最新'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 600));
      expect(position(tester).extentAfter, lessThan(1));
      expect(find.byTooltip('回到最新'), findsNothing);
    });

    testWidgets(
      'follows streaming text while at the bottom, stops once you scroll up',
      (tester) async {
        final h = await pumpChat(
          tester,
          setup: (api) => api
            ..on('GET /api/ai/chats', {
              'chats': [chatJson(7)],
            })
            ..on('GET /api/ai/chats/7', detailJson(7, longHistory())),
        );
        await tester.pump(const Duration(milliseconds: 100));
        await tester.enterText(find.byType(TextField), '新问题');
        await tester.pump();
        await tester.tap(find.byTooltip('发送'));
        await tester.pump();
        await tester.pump(const Duration(milliseconds: 50));
        for (var i = 0; i < 6; i++) {
          await h.emit('text', {'delta': '流式的一行内容。\n\n'});
        }
        expect(position(tester).extentAfter, lessThan(1));

        await tester.drag(
          find
              .descendant(
                of: find.byType(AiChatView),
                matching: find.byType(ListView),
              )
              .first,
          const Offset(0, 500),
        );
        await tester.pump(const Duration(milliseconds: 400));
        final before = position(tester).pixels;
        for (var i = 0; i < 4; i++) {
          await h.emit('text', {'delta': '继续输出一行内容。\n\n'});
        }
        expect(position(tester).pixels, before);
        expect(find.byTooltip('回到最新'), findsOneWidget);
      },
    );
  });

  group('haptics', () {
    testWidgets('one light impact on send and one on completion', (
      tester,
    ) async {
      final taps = <String?>[];
      tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        SystemChannels.platform,
        (call) async {
          if (call.method == 'HapticFeedback.vibrate') {
            taps.add(call.arguments as String?);
          }
          return null;
        },
      );
      addTearDown(
        () => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
          SystemChannels.platform,
          null,
        ),
      );
      final h = await pumpChat(tester);
      await tapText(tester, starter);
      expect(taps, ['HapticFeedbackType.lightImpact']);
      await h.emit('text', {'delta': 'x'});
      expect(taps, hasLength(1));
      await h.finish();
      expect(taps, [
        'HapticFeedbackType.lightImpact',
        'HapticFeedbackType.lightImpact',
      ]);
    });

    testWidgets('stopping gives no completion haptic', (tester) async {
      final taps = <String?>[];
      tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        SystemChannels.platform,
        (call) async {
          if (call.method == 'HapticFeedback.vibrate') taps.add('x');
          return null;
        },
      );
      addTearDown(
        () => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
          SystemChannels.platform,
          null,
        ),
      );
      await pumpChat(tester);
      await tapText(tester, starter);
      await tester.tap(find.text('停止'));
      await tester.pump(const Duration(milliseconds: 100));
      expect(taps, hasLength(1)); // 只有发送那一次
    });

    testWidgets('no haptics on desktop', (tester) async {
      final taps = <String?>[];
      tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        SystemChannels.platform,
        (call) async {
          if (call.method == 'HapticFeedback.vibrate') taps.add('x');
          return null;
        },
      );
      addTearDown(
        () => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
          SystemChannels.platform,
          null,
        ),
      );
      debugDefaultTargetPlatformOverride = TargetPlatform.macOS;
      final h = await pumpChat(tester);
      await tapText(tester, starter);
      await h.finish();
      debugDefaultTargetPlatformOverride = null;
      expect(taps, isEmpty);
    });
  });

  group('reduced motion', () {
    testWidgets(
      'no slide on cards, no loop: the wait indicator counts seconds',
      (tester) async {
        final h = await pumpChat(tester, reduced: true);
        await tapText(tester, starter);
        expect(find.text('正在思考…'), findsOneWidget);
        await tester.pump(const Duration(seconds: 2));
        expect(find.text('正在思考 · 2s'), findsOneWidget);

        await h.emit('tool_start', {'id': 'c1', 'name': 'x', 'label': '找片…'});
        emitToolEnd(h, [card(1, 'A'), card(2, 'B')]);
        await tester.pump(const Duration(milliseconds: 100));
        expect(find.byType(AiCardReveal), findsNWidgets(2));
        expect(
          find.descendant(
            of: find.byType(AiCardReveal),
            matching: find.byType(SlideTransition),
          ),
          findsNothing,
        );
        // 仍然淡入
        expect(
          find.descendant(
            of: find.byType(AiCardReveal),
            matching: find.byType(FadeTransition),
          ),
          findsWidgets,
        );
      },
    );

    testWidgets('with motion the indicator breathes and cards slide', (
      tester,
    ) async {
      final h = await pumpChat(tester);
      await tapText(tester, starter);
      await tester.pump(const Duration(seconds: 2));
      expect(find.text('正在思考…'), findsOneWidget);
      await h.emit('tool_start', {'id': 'c1', 'name': 'x', 'label': '找片…'});
      emitToolEnd(h, [card(1, 'A')]);
      await tester.pump(const Duration(milliseconds: 20));
      expect(
        find.descendant(
          of: find.byType(AiCardReveal),
          matching: find.byType(SlideTransition),
        ),
        findsWidgets,
      );
    });
  });

  group('drawer', () {
    testWidgets('slides in from the right, 420 wide, with the same chat', (
      tester,
    ) async {
      await pumpChat(tester, location: '/', size: const Size(1280, 800));
      await tester.tap(find.text('打开抽屉'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));
      // 动画途中：还在屏幕右侧之外
      expect(tester.getTopLeft(find.byType(AiChatView)).dx, greaterThan(860));
      await tester.pump(const Duration(milliseconds: 500));
      expect(tester.getSize(find.byType(AiChatView)).width, aiDrawerWidth);
      expect(
        tester.getTopLeft(find.byType(AiChatView)).dx,
        1280 - aiDrawerWidth,
      );
      expect(find.text('想看点什么？'), findsOneWidget);
      expect(find.text('AI 助手'), findsOneWidget);

      await tester.tap(find.byTooltip('关闭'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 600));
      expect(find.byType(AiChatView), findsNothing);
    });

    testWidgets('reduced motion only fades', (tester) async {
      await pumpChat(
        tester,
        location: '/',
        size: const Size(1280, 800),
        reduced: true,
      );
      await tester.tap(find.text('打开抽屉'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));
      expect(
        tester.getTopLeft(find.byType(AiChatView)).dx,
        1280 - aiDrawerWidth,
      );
    });

    testWidgets('Esc closes it', (tester) async {
      await pumpChat(tester, location: '/', size: const Size(1280, 800));
      await tester.tap(find.text('打开抽屉'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 600));
      expect(find.byType(AiChatView), findsOneWidget);
      await tester.sendKeyEvent(LogicalKeyboardKey.escape);
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 600));
      expect(find.byType(AiChatView), findsNothing);
    });

    testWidgets('tapping outside closes it', (tester) async {
      await pumpChat(tester, location: '/', size: const Size(1280, 800));
      await tester.tap(find.text('打开抽屉'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 600));
      await tester.tapAt(const Offset(100, 400));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 600));
      expect(find.byType(AiChatView), findsNothing);
    });

    testWidgets('navigating from a card closes the drawer', (tester) async {
      final h = await pumpChat(
        tester,
        location: '/',
        size: const Size(1280, 800),
        setup: (api) => api
          ..on('GET /api/ai/chats', {
            'chats': [chatJson(7)],
          })
          ..on(
            'GET /api/ai/chats/7',
            detailJson(7, [
              userMessage(1, '推荐'),
              assistantMessage(2, '好的', items: [card(5, '喜剧之王')]),
            ]),
          ),
      );
      await tester.tap(find.text('打开抽屉'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 600));
      await tester.tap(find.text('喜剧之王'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 700));
      expect(h.location, '/item/5');
      expect(find.byType(AiChatView), findsNothing);
      expect(find.text('详情页 5'), findsOneWidget);
    });
  });

  group('top bar entry', () {
    Future<GoRouter> pumpEntry(
      WidgetTester tester, {
      required bool chat,
      bool tv = false,
      Size size = const Size(390, 844),
    }) async {
      tester.view.physicalSize = size * 2;
      tester.view.devicePixelRatio = 2;
      addTearDown(tester.view.reset);
      final router = GoRouter(
        routes: [
          GoRoute(
            path: '/',
            builder: (_, _) =>
                Scaffold(appBar: AppBar(actions: const [AiEntryButton()])),
          ),
          GoRoute(
            path: '/ai',
            builder: (_, _) => const Scaffold(body: Text('AI 全屏页')),
          ),
        ],
      );
      addTearDown(router.dispose);
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            aiAvailabilityProvider.overrideWithValue(
              AiAvailability(chat: chat),
            ),
            televisionProvider.overrideWithValue(tv),
            authControllerProvider.overrideWith(_NoUser.new),
            apiBaseUrlProvider.overrideWithValue('http://test'),
            apiClientProvider.overrideWithValue(
              Dio(BaseOptions(baseUrl: 'http://test'))
                ..httpClientAdapter = FakeAdminApi(),
            ),
            tokenStoreProvider.overrideWithValue(MemoryTokenStore()),
          ],
          child: MaterialApp.router(
            theme: buildTheme().copyWith(splashFactory: NoSplash.splashFactory),
            routerConfig: router,
          ),
        ),
      );
      await tester.pump(const Duration(milliseconds: 300));
      return router;
    }

    testWidgets('hidden when chat is not configured', (tester) async {
      await pumpEntry(tester, chat: false);
      expect(find.byTooltip('AI 助手'), findsNothing);
    });

    testWidgets('hidden on TV', (tester) async {
      await pumpEntry(tester, chat: true, tv: true);
      expect(find.byTooltip('AI 助手'), findsNothing);
    });

    testWidgets('narrow screens push the full-screen page', (tester) async {
      await pumpEntry(tester, chat: true);
      await tester.tap(find.byTooltip('AI 助手'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      expect(find.text('AI 全屏页'), findsOneWidget);
    });

    testWidgets('wide screens open the drawer instead', (tester) async {
      await pumpEntry(tester, chat: true, size: const Size(1280, 800));
      await tester.tap(find.byTooltip('AI 助手'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 600));
      expect(find.text('AI 全屏页'), findsNothing);
      expect(find.byType(AiChatView), findsOneWidget);
      expect(tester.getSize(find.byType(AiChatView)).width, aiDrawerWidth);
    });
  });
}

/// 推一个 tool_end（带条目卡片），不等界面。
void emitToolEnd(Harness h, List<Map<String, Object?>> items) =>
    h.call.emit('tool_end', {
      'id': 'c1',
      'name': 'x',
      'summary': '找到 ${items.length} 部',
      'ok': true,
      'items': items,
    });

extension on WidgetTester {
  /// 页面跳转后等路由动画走完；不用 pumpAndSettle 以免被等待指示的循环卡住。
  Future<void> pumpAndSettleSafe() async {
    await pump();
    await pump(const Duration(milliseconds: 600));
  }
}
