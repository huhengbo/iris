import '../../core/api/api_error.dart';
import '../library/browse_api.dart';

/// 会话列表里的一项（`ChatOut`）。
class AiChatSummary {
  const AiChatSummary({
    required this.id,
    required this.title,
    required this.createdAt,
    required this.updatedAt,
  });

  factory AiChatSummary.fromJson(Map<String, dynamic> json) => AiChatSummary(
    id: json['id'] as int,
    title: json['title'] as String,
    createdAt: json['created_at'] as String,
    updatedAt: json['updated_at'] as String,
  );

  final int id;
  final String title;
  final String createdAt;
  final String updatedAt;

  DateTime? get updated => DateTime.tryParse(updatedAt)?.toLocal();
}

/// 会话详情（`ChatDetailOut`）：消息已经转成界面用的 [AiMessage]。
class AiChatDetail {
  const AiChatDetail({required this.summary, required this.messages});

  factory AiChatDetail.fromJson(
    Map<String, dynamic> json,
    String baseUrl,
    AiKeys keys,
  ) {
    final messages = <AiMessage>[];
    var question = '';
    for (final raw in json['messages'] as List<dynamic>) {
      final message = AiMessage.fromJson(
        raw as Map<String, dynamic>,
        baseUrl,
        key: keys.next(),
        question: question,
      );
      if (message.isUser) question = message.text;
      messages.add(message);
    }
    return AiChatDetail(
      summary: AiChatSummary.fromJson(json),
      messages: messages,
    );
  }

  final AiChatSummary summary;
  final List<AiMessage> messages;
}

/// 本地消息的递增编号，列表项的 key 用它，不依赖服务端 id（流式期间还没有）。
class AiKeys {
  int _next = 0;
  int next() => _next++;
}

/// 一次工具调用（`ChatStepOut`，流式期间由 tool_start / tool_end 拼出来）。
class AiStep {
  const AiStep({
    this.callId,
    required this.name,
    required this.label,
    this.summary = '',
    this.ok = true,
    this.running = false,
    this.itemIds = const [],
  });

  factory AiStep.fromJson(Map<String, dynamic> json) => AiStep(
    name: json['name'] as String,
    label: json['label'] as String,
    summary: json['summary'] as String,
    ok: json['ok'] as bool,
    itemIds: (json['item_ids'] as List<dynamic>).cast<int>(),
  );

  /// 流式期间服务端给的调用编号，用来把 tool_end 对到 tool_start。
  final String? callId;
  final String name;

  /// 进行中的文案，如「正在搜索资料库…」
  final String label;

  /// 完成后的一句话结果；失败时是原因
  final String summary;
  final bool ok;
  final bool running;
  final List<int> itemIds;

  AiStep finished({required String summary, required bool ok}) => AiStep(
    callId: callId,
    name: name,
    label: label,
    summary: summary,
    ok: ok,
    itemIds: itemIds,
  );
}

enum AiMessageStatus { streaming, done, stopped, error }

/// 一条对话消息。用户消息只用 [text]；助手消息的各部分按到达顺序逐步填满。
class AiMessage {
  const AiMessage({
    required this.key,
    required this.isUser,
    this.id,
    this.text = '',
    this.steps = const [],
    this.items = const [],
    this.suggestions = const [],
    this.thinkingText = '',
    this.thinkingSeconds,
    this.thinking = false,
    this.status = AiMessageStatus.done,
    this.error,
    this.question = '',
    this.live = false,
  });

  /// 刚发出、还没有任何内容的助手消息。
  factory AiMessage.pending({required int key, required String question}) =>
      AiMessage(
        key: key,
        isUser: false,
        status: AiMessageStatus.streaming,
        question: question,
        live: true,
      );

  factory AiMessage.user({required int key, required String text}) =>
      AiMessage(key: key, isUser: true, text: text, live: true);

  factory AiMessage.fromJson(
    Map<String, dynamic> json,
    String baseUrl, {
    required int key,
    required String question,
  }) {
    final isUser = json['role'] == 'user';
    final error = json['error'] as String?;
    return AiMessage(
      key: key,
      id: json['id'] as int,
      isUser: isUser,
      text: json['content'] as String,
      steps: [
        for (final s in json['steps'] as List<dynamic>)
          AiStep.fromJson(s as Map<String, dynamic>),
      ],
      items: [
        for (final i in json['items'] as List<dynamic>)
          MediaCard.fromJson(i as Map<String, dynamic>, baseUrl),
      ],
      suggestions: (json['suggestions'] as List<dynamic>).cast<String>(),
      thinkingSeconds: (json['thinking_seconds'] as num?)?.toDouble(),
      status: switch (json['status'] as String) {
        'stopped' => AiMessageStatus.stopped,
        'error' => AiMessageStatus.error,
        _ => AiMessageStatus.done,
      },
      error: error == null ? null : ApiError(code: 'ai_error', message: error),
      question: isUser ? '' : question,
    );
  }

  /// 列表项的 key，本地唯一。
  final int key;
  final int? id;
  final bool isUser;

  /// 用户消息是问句，助手消息是正文（Markdown 子集）。
  final String text;
  final List<AiStep> steps;
  final List<MediaCard> items;
  final List<String> suggestions;

  /// 流式期间收到的思考增量拼成的文本；历史消息没有。
  final String thinkingText;
  final double? thinkingSeconds;

  /// 正在思考（收到了增量，还没收到结束的 seconds）
  final bool thinking;
  final AiMessageStatus status;
  final ApiError? error;

  /// 触发这条助手消息的问句，「查看全部」和重试用它。
  final String question;

  /// 本次会话里新产生的消息才播入场动画；打开历史会话时直接显示。
  final bool live;

  bool get streaming => status == AiMessageStatus.streaming;

  AiMessage copyWith({
    int? id,
    String? text,
    List<AiStep>? steps,
    List<MediaCard>? items,
    List<String>? suggestions,
    String? thinkingText,
    double? thinkingSeconds,
    bool? thinking,
    AiMessageStatus? status,
    ApiError? error,
  }) => AiMessage(
    key: key,
    isUser: isUser,
    id: id ?? this.id,
    text: text ?? this.text,
    steps: steps ?? this.steps,
    items: items ?? this.items,
    suggestions: suggestions ?? this.suggestions,
    thinkingText: thinkingText ?? this.thinkingText,
    thinkingSeconds: thinkingSeconds ?? this.thinkingSeconds,
    thinking: thinking ?? this.thinking,
    status: status ?? this.status,
    error: error ?? this.error,
    question: question,
    live: live,
  );
}

// ---- SSE 事件（`start`、`thinking` 等的 data，见 openapi 的 *Data）----

sealed class AiEvent {
  const AiEvent();

  /// 按事件名解析 data；不认识的事件返回 null（服务端以后加事件不影响旧客户端）。
  static AiEvent? parse(
    String name,
    Map<String, dynamic> data,
    String baseUrl,
  ) => switch (name) {
    'start' => AiStarted.fromJson(data),
    'thinking' => AiThinking.fromJson(data),
    'tool_start' => AiToolStarted.fromJson(data),
    'tool_end' => AiToolEnded.fromJson(data, baseUrl),
    'text' => AiTextDelta.fromJson(data),
    'suggestions' => AiSuggestions.fromJson(data),
    'done' => AiDone.fromJson(data),
    'error' => AiFailed.fromJson(data),
    _ => null,
  };
}

final class AiStarted extends AiEvent {
  const AiStarted({required this.chatId, required this.messageId});

  factory AiStarted.fromJson(Map<String, dynamic> json) => AiStarted(
    chatId: json['chat_id'] as int,
    messageId: json['message_id'] as int,
  );

  final int chatId;
  final int messageId;
}

final class AiThinking extends AiEvent {
  const AiThinking({required this.delta, this.seconds});

  factory AiThinking.fromJson(Map<String, dynamic> json) => AiThinking(
    delta: json['delta'] as String,
    seconds: (json['seconds'] as num?)?.toDouble(),
  );

  final String delta;

  /// 为 null 是思考中的增量；有值是这一段思考结束，delta 为空
  final double? seconds;
}

final class AiToolStarted extends AiEvent {
  const AiToolStarted({
    required this.id,
    required this.name,
    required this.label,
  });

  factory AiToolStarted.fromJson(Map<String, dynamic> json) => AiToolStarted(
    id: json['id'] as String,
    name: json['name'] as String,
    label: json['label'] as String,
  );

  final String id;
  final String name;
  final String label;
}

final class AiToolEnded extends AiEvent {
  const AiToolEnded({
    required this.id,
    required this.name,
    required this.summary,
    required this.ok,
    required this.items,
  });

  factory AiToolEnded.fromJson(Map<String, dynamic> json, String baseUrl) =>
      AiToolEnded(
        id: json['id'] as String,
        name: json['name'] as String,
        summary: json['summary'] as String,
        ok: json['ok'] as bool,
        items: [
          for (final i in json['items'] as List<dynamic>)
            MediaCard.fromJson(i as Map<String, dynamic>, baseUrl),
        ],
      );

  final String id;
  final String name;
  final String summary;
  final bool ok;
  final List<MediaCard> items;
}

final class AiTextDelta extends AiEvent {
  const AiTextDelta(this.delta);

  factory AiTextDelta.fromJson(Map<String, dynamic> json) =>
      AiTextDelta(json['delta'] as String);

  final String delta;
}

final class AiSuggestions extends AiEvent {
  const AiSuggestions(this.items);

  factory AiSuggestions.fromJson(Map<String, dynamic> json) =>
      AiSuggestions((json['items'] as List<dynamic>).cast<String>());

  final List<String> items;
}

final class AiDone extends AiEvent {
  const AiDone({
    required this.messageId,
    required this.inputTokens,
    required this.outputTokens,
  });

  factory AiDone.fromJson(Map<String, dynamic> json) => AiDone(
    messageId: json['message_id'] as int,
    inputTokens: json['input_tokens'] as int,
    outputTokens: json['output_tokens'] as int,
  );

  final int messageId;
  final int inputTokens;
  final int outputTokens;
}

/// 流里的 `error` 事件。
final class AiFailed extends AiEvent {
  const AiFailed({required this.code, required this.message});

  factory AiFailed.fromJson(Map<String, dynamic> json) => AiFailed(
    code: json['code'] as String,
    message: json['message'] as String,
  );

  final String code;
  final String message;
}
