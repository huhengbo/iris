import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import 'ai_chat_api.dart';
import 'ai_chat_models.dart';
import 'ai_stream_transport.dart';

/// 网络断开后自动重连前等多久。测试里覆盖成零。
final aiReconnectDelayProvider = Provider<Duration>(
  (ref) => const Duration(milliseconds: 800),
);

/// 单条提问的长度上限，和后端 `SendMessageRequest` 一致。
const aiMaxQuestionLength = 2000;

class AiChatState {
  const AiChatState({
    this.loading = false,
    this.loadError,
    this.chatId,
    this.messages = const [],
    this.busy = false,
  });

  /// 正在读取会话（打开页面、切换历史会话）
  final bool loading;
  final ApiError? loadError;

  /// 还没发过消息的新对话为 null，第一次发送时才创建，空对话不会在服务端留下记录。
  final int? chatId;
  final List<AiMessage> messages;

  /// 有一条回答正在生成：发送被忽略，发送键换成「停止」
  final bool busy;

  bool get isEmpty => messages.isEmpty;

  AiChatState copyWith({
    bool? loading,
    int? chatId,
    List<AiMessage>? messages,
    bool? busy,
  }) => AiChatState(
    loading: loading ?? this.loading,
    chatId: chatId ?? this.chatId,
    messages: messages ?? this.messages,
    busy: busy ?? this.busy,
  );
}

/// 一个对话窗口（全屏页或抽屉）的状态。参数 [initialQuestion] 是带进来的问句：
/// 有就新建对话并发送它，没有就接着最近一次的对话。离开页面（provider 释放）
/// 时断开正在进行的请求，服务端会把已生成的部分存为「已停止」。
final aiChatControllerProvider = NotifierProvider.autoDispose
    .family<AiChatController, AiChatState, String?>(AiChatController.new);

class AiChatController extends Notifier<AiChatState> {
  AiChatController(this.initialQuestion);

  final String? initialQuestion;
  final _keys = AiKeys();
  AiAbort? _abort;

  /// 每次切换会话 / 新对话加一，让上一场还在飞的请求结果作废。
  int _epoch = 0;

  AiChatApi get _api => ref.read(aiChatApiProvider);

  @override
  AiChatState build() {
    ref.onDispose(() {
      _epoch++;
      _abort?.abort();
    });
    Future.microtask(() {
      if (ref.mounted) unawaited(_open());
    });
    return const AiChatState(loading: true);
  }

  Future<void> _open() async {
    final question = initialQuestion?.trim();
    if (question != null && question.isNotEmpty) {
      state = const AiChatState();
      await send(question);
      return;
    }
    await _loadChat(null);
  }

  /// 读取会话：[id] 为空时取最近一个，没有就是空对话。
  Future<void> _loadChat(int? id) async {
    final epoch = ++_epoch;
    state = const AiChatState(loading: true);
    try {
      var target = id;
      if (target == null) {
        final chats = await _api.listChats();
        if (epoch != _epoch || !ref.mounted) return;
        if (chats.isEmpty) {
          state = const AiChatState();
          return;
        }
        target = chats.first.id;
      }
      final detail = await _api.getChat(target, _keys);
      if (epoch != _epoch || !ref.mounted) return;
      state = AiChatState(chatId: detail.summary.id, messages: detail.messages);
    } on Object catch (e) {
      if (epoch != _epoch || !ref.mounted) return;
      state = AiChatState(loadError: ApiError.from(e));
    }
  }

  /// 读取失败后的「重试」。
  Future<void> reload() => _loadChat(state.chatId);

  /// 新对话：停掉正在生成的，回到空对话。
  void newChat() {
    _epoch++;
    _abort?.abort();
    _abort = null;
    state = const AiChatState();
  }

  Future<void> openChat(int id) {
    _abort?.abort();
    _abort = null;
    return _loadChat(id);
  }

  /// 删除一个历史会话；删的是当前会话就回到空对话。失败时抛出，由界面提示。
  Future<void> deleteChat(int id) async {
    await _api.deleteChat(id);
    ref.invalidate(aiChatListProvider);
    if (ref.mounted && state.chatId == id) newChat();
  }

  /// 发送一条问句。正在生成、正在读取或问句为空时忽略（重复点击只发一次）。
  Future<void> send(String text) async {
    final content = text.trim();
    if (content.isEmpty ||
        content.length > aiMaxQuestionLength ||
        state.busy ||
        state.loading ||
        state.loadError != null) {
      return;
    }
    final reply = AiMessage.pending(key: _keys.next(), question: content);
    state = state.copyWith(
      busy: true,
      messages: [
        ...state.messages,
        AiMessage.user(key: _keys.next(), text: content),
        reply,
      ],
    );
    _haptic();
    await _run(reply.key, content);
  }

  /// 出错后用同一个问题重新提问：已生成的内容保留，新的回答接在后面。
  Future<void> retry() async {
    if (state.busy || state.loading) return;
    final last = state.messages.lastOrNull;
    if (last == null ||
        last.isUser ||
        last.status != AiMessageStatus.error ||
        last.question.isEmpty) {
      return;
    }
    final reply = AiMessage.pending(key: _keys.next(), question: last.question);
    state = state.copyWith(busy: true, messages: [...state.messages, reply]);
    _haptic();
    await _run(reply.key, last.question);
  }

  /// 停止生成：断开连接，已显示的内容保留，末尾标「已停止」。
  void stop() {
    if (!state.busy) return;
    _abort?.abort();
    _abort = null;
    final last = state.messages.lastOrNull;
    if (last != null && last.streaming) {
      _update(last.key, (m) => _settled(m, AiMessageStatus.stopped));
    }
    state = state.copyWith(busy: false);
  }

  Future<void> _run(int key, String content) async {
    final epoch = _epoch;
    final abort = _abort = AiAbort();
    bool stale() => !ref.mounted || epoch != _epoch || abort.isAborted;
    var reconnected = false;
    try {
      while (true) {
        try {
          var chatId = state.chatId;
          if (chatId == null) {
            chatId = (await _api.createChat()).id;
            if (stale()) return;
            state = state.copyWith(chatId: chatId);
          }
          final finished = await _consume(
            _api.sendMessage(chatId, content, abort),
            key,
            abort,
          );
          if (stale()) return;
          if (!finished) {
            // 连接没有以 done / error 收尾就断了
            throw const ApiError(code: 'network', message: '网络连接中断，请检查网络后重试');
          }
          return;
        } on Object catch (e) {
          if (stale()) return;
          final error = _asApiError(e);
          if (error.kind == ApiErrorKind.network && !reconnected) {
            // 网络断开先自动重连一次：丢掉这次收到的片段，从头再来
            reconnected = true;
            _update(
              key,
              (m) => AiMessage.pending(key: m.key, question: m.question),
            );
            await Future<void>.delayed(ref.read(aiReconnectDelayProvider));
            if (stale()) return;
            continue;
          }
          _update(key, (m) => _failed(m, error));
          return;
        }
      }
    } finally {
      // 取消订阅不会关闭 TCP 连接，要靠 abort；正常结束后再 abort 是空操作
      abort.abort();
      if (identical(_abort, abort)) {
        _abort = null;
        if (ref.mounted && epoch == _epoch) {
          state = state.copyWith(busy: false);
        }
      }
    }
  }

  /// 读完整个事件流。返回是否收到了 done / error；订阅被「停止」取消时按当前进度返回。
  Future<bool> _consume(Stream<AiEvent> events, int key, AiAbort abort) {
    final completer = Completer<bool>();
    var finished = false;
    late final StreamSubscription<AiEvent> subscription;
    void finish() {
      if (!completer.isCompleted) completer.complete(finished);
    }

    subscription = events.listen(
      (event) {
        try {
          if (_apply(key, event)) {
            finished = true;
            unawaited(subscription.cancel());
            finish();
          }
        } on Object catch (e, stack) {
          unawaited(subscription.cancel());
          if (!completer.isCompleted) completer.completeError(e, stack);
        }
      },
      onError: (Object e, StackTrace stack) {
        if (!completer.isCompleted) completer.completeError(e, stack);
      },
      onDone: finish,
      cancelOnError: true,
    );
    unawaited(
      abort.signal.then((_) {
        unawaited(subscription.cancel());
        finish();
      }),
    );
    return completer.future;
  }

  /// 把一个事件落到消息上。返回 true 表示这是结尾事件（done / error）。
  bool _apply(int key, AiEvent event) {
    switch (event) {
      case AiStarted():
        break;
      case AiThinking(:final delta, :final seconds):
        _update(
          key,
          (m) => seconds == null
              ? m.copyWith(thinking: true, thinkingText: m.thinkingText + delta)
              : m.copyWith(
                  thinking: false,
                  thinkingSeconds: (m.thinkingSeconds ?? 0) + seconds,
                ),
        );
      case AiToolStarted(:final id, :final name, :final label):
        // tool_start 之前已显示的文字是过渡语，丢掉
        _update(
          key,
          (m) => m.copyWith(
            text: '',
            thinking: false,
            steps: [
              ...m.steps,
              AiStep(callId: id, name: name, label: label, running: true),
            ],
          ),
        );
      case AiToolEnded(
        :final id,
        :final name,
        :final summary,
        :final ok,
        :final items,
      ):
        _update(key, (m) {
          final steps = [...m.steps];
          final at = steps.indexWhere((s) => s.callId == id && s.running);
          if (at >= 0) {
            steps[at] = steps[at].finished(summary: summary, ok: ok);
          } else {
            steps.add(
              AiStep(
                callId: id,
                name: name,
                label: '',
                summary: summary,
                ok: ok,
              ),
            );
          }
          final known = {for (final i in m.items) i.id};
          return m.copyWith(
            steps: steps,
            items: [
              ...m.items,
              for (final i in items)
                if (known.add(i.id)) i,
            ],
          );
        });
      case AiTextDelta(:final delta):
        _update(key, (m) => m.copyWith(text: m.text + delta, thinking: false));
      case AiSuggestions(:final items):
        _update(key, (m) => m.copyWith(suggestions: items));
      case AiDone(:final messageId):
        _update(
          key,
          (m) => _settled(m, AiMessageStatus.done).copyWith(id: messageId),
        );
        _haptic();
        ref.invalidate(aiChatListProvider);
        return true;
      case AiFailed(:final code, :final message):
        _update(key, (m) => _failed(m, ApiError(code: code, message: message)));
        ref.invalidate(aiChatListProvider);
        return true;
    }
    return false;
  }

  /// 收尾：没跑完的步骤和思考不再转圈。
  AiMessage _settled(AiMessage m, AiMessageStatus status) => m.copyWith(
    status: status,
    thinking: false,
    steps: [
      for (final s in m.steps)
        if (!s.running) s,
    ],
  );

  AiMessage _failed(AiMessage m, ApiError error) =>
      _settled(m, AiMessageStatus.error).copyWith(error: error);

  void _update(int key, AiMessage Function(AiMessage) change) {
    if (!ref.mounted) return;
    final messages = state.messages;
    final at = messages.indexWhere((m) => m.key == key);
    if (at < 0) return;
    state = state.copyWith(
      messages: [...messages]..[at] = change(messages[at]),
    );
  }

  ApiError _asApiError(Object error) {
    if (error is ApiError) return error;
    if (error is FormatException) {
      return const ApiError(code: 'bad_stream', message: '回答的格式不对，请重试');
    }
    final converted = ApiError.from(error);
    // 流读到一半抛出的 SocketException 之类：都是连接问题
    return converted.code == 'unknown'
        ? const ApiError(code: 'network', message: '网络连接失败，请检查网络后重试')
        : converted;
  }

  /// 发送和回答完成各一次轻触感，只在手机上。
  void _haptic() {
    if (kIsWeb) return;
    if (defaultTargetPlatform == TargetPlatform.android ||
        defaultTargetPlatform == TargetPlatform.iOS) {
      unawaited(HapticFeedback.lightImpact());
    }
  }
}
