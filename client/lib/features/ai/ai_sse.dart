import 'dart:convert';

import 'ai_chat_models.dart';

/// 一个 SSE 事件：`event:` 行给出名字，`data:` 行（可多行）拼成数据。
class SseFrame {
  const SseFrame(this.event, this.data);

  final String event;
  final String data;

  @override
  String toString() => 'SseFrame($event, $data)';
}

/// 增量的 SSE 行解析器（WHATWG EventSource 的行规则）：喂入任意切分的文本块，
/// 取回已经完整的事件。行结束符可以是 `\n`、`\r\n` 或单独的 `\r`（\r\n 被切在
/// 两个块之间也能识别）；以 `:` 开头的是注释；没有 data 的事件不触发；
/// 流结束时没有以空行收尾的半个事件丢弃，因为 JSON 可能是残缺的。
class SseParser {
  String _buffer = '';
  bool _skipLineFeed = false;
  bool _first = true;
  String? _event;
  final _data = <String>[];

  List<SseFrame> add(String chunk) {
    final frames = <SseFrame>[];
    var text = chunk;
    if (_first && text.isNotEmpty) {
      _first = false;
      if (text.codeUnitAt(0) == 0xFEFF) text = text.substring(1);
    }
    if (_skipLineFeed && text.isNotEmpty) {
      // 上一块以 \r 结尾，这里开头的 \n 属于同一个换行
      if (text.codeUnitAt(0) == 0x0A) text = text.substring(1);
      _skipLineFeed = false;
    }
    _buffer += text;
    var start = 0;
    for (var i = 0; i < _buffer.length; i++) {
      final unit = _buffer.codeUnitAt(i);
      if (unit != 0x0A && unit != 0x0D) continue;
      _line(_buffer.substring(start, i), frames);
      if (unit == 0x0D) {
        if (i + 1 < _buffer.length) {
          if (_buffer.codeUnitAt(i + 1) == 0x0A) i++;
        } else {
          _skipLineFeed = true;
        }
      }
      start = i + 1;
    }
    _buffer = _buffer.substring(start);
    return frames;
  }

  void _line(String line, List<SseFrame> frames) {
    if (line.isEmpty) {
      if (_data.isNotEmpty) {
        frames.add(SseFrame(_event ?? 'message', _data.join('\n')));
      }
      _event = null;
      _data.clear();
      return;
    }
    if (line.startsWith(':')) return;
    final colon = line.indexOf(':');
    final field = colon < 0 ? line : line.substring(0, colon);
    var value = colon < 0 ? '' : line.substring(colon + 1);
    if (value.startsWith(' ')) value = value.substring(1);
    switch (field) {
      case 'event':
        _event = value;
      case 'data':
        _data.add(value);
      // id、retry 用不到
    }
  }
}

/// 字节流 -> SSE 事件。UTF-8 多字节字符被切在两个块之间时由解码器接上。
Stream<SseFrame> sseFrames(Stream<List<int>> bytes) async* {
  final parser = SseParser();
  await for (final text in utf8.decoder.bind(bytes)) {
    for (final frame in parser.add(text)) {
      yield frame;
    }
  }
}

/// 字节流 -> 类型化的聊天事件。不认识的事件名跳过；认识的事件 data 不是合法 JSON
/// 时抛 [FormatException]（流已经不可信，不假装成功）。
Stream<AiEvent> aiEvents(Stream<List<int>> bytes, String baseUrl) async* {
  await for (final frame in sseFrames(bytes)) {
    final Object? data;
    try {
      data = jsonDecode(frame.data);
    } on FormatException {
      if (!_known.contains(frame.event)) continue;
      throw FormatException('聊天事件 ${frame.event} 的数据不是合法 JSON');
    }
    if (data is! Map<String, dynamic>) continue;
    final event = AiEvent.parse(frame.event, data, baseUrl);
    if (event != null) yield event;
  }
}

const _known = {
  'start',
  'thinking',
  'tool_start',
  'tool_end',
  'text',
  'suggestions',
  'done',
  'error',
};
