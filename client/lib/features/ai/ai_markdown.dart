import 'package:flutter/material.dart';

import '../../app/theme.dart';

/// 回答正文里用到的 Markdown 子集：段落、列表（- * • 和 1.）、粗体。
/// 标题行去掉 # 后当作粗体段落；其余语法原样显示。不引入 Markdown 包。
enum AiBlockKind { paragraph, bullet, number }

class AiBlock {
  const AiBlock(this.kind, this.text, [this.number = 0]);

  final AiBlockKind kind;
  final String text;

  /// 有序列表的序号
  final int number;
}

final _bullet = RegExp(r'^[-*•]\s+(.*)$');
final _numbered = RegExp(r'^(\d{1,3})[.)、]\s+(.*)$');
final _heading = RegExp(r'^#{1,6}\s+(.*)$');

List<AiBlock> parseAiMarkdown(String source) {
  final blocks = <AiBlock>[];
  final paragraph = <String>[];
  void flush() {
    if (paragraph.isEmpty) return;
    blocks.add(AiBlock(AiBlockKind.paragraph, paragraph.join('\n')));
    paragraph.clear();
  }

  for (final raw in source.split(RegExp(r'\r?\n'))) {
    final line = raw.trim();
    if (line.isEmpty) {
      flush();
      continue;
    }
    if (_bullet.firstMatch(line) case final m?) {
      flush();
      blocks.add(AiBlock(AiBlockKind.bullet, m.group(1)!));
    } else if (_numbered.firstMatch(line) case final m?) {
      flush();
      blocks.add(
        AiBlock(AiBlockKind.number, m.group(2)!, int.parse(m.group(1)!)),
      );
    } else if (_heading.firstMatch(line) case final m?) {
      flush();
      blocks.add(AiBlock(AiBlockKind.paragraph, '**${m.group(1)}**'));
    } else {
      paragraph.add(line);
    }
  }
  flush();
  return blocks;
}

/// 行内粗体：`**` 成对切换。流式输出时结尾的 `**` 还没闭合，
/// 按已经加粗处理，不让星号闪一下。
List<InlineSpan> aiInlineSpans(String text, TextStyle style) {
  final spans = <InlineSpan>[];
  final bold = style.copyWith(
    fontWeight: FontWeight.w700,
    color: CinemaColors.text0,
  );
  final parts = text.split('**');
  for (var i = 0; i < parts.length; i++) {
    if (parts[i].isEmpty) continue;
    spans.add(TextSpan(text: parts[i], style: i.isOdd ? bold : style));
  }
  return spans;
}

/// 助手回答的正文：bodyMedium text0。
class AiMarkdown extends StatelessWidget {
  const AiMarkdown(this.source, {super.key});

  final String source;

  @override
  Widget build(BuildContext context) {
    final style = Theme.of(context).textTheme.bodyMedium!
        .copyWith(color: CinemaColors.text0);
    final blocks = parseAiMarkdown(source);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (var i = 0; i < blocks.length; i++)
          Padding(
            padding: EdgeInsets.only(
              top: i == 0
                  ? 0
                  : blocks[i].kind != AiBlockKind.paragraph &&
                        blocks[i - 1].kind == blocks[i].kind
                  ? 4
                  : 10,
            ),
            child: _block(blocks[i], style),
          ),
      ],
    );
  }

  Widget _block(AiBlock block, TextStyle style) {
    final body = Text.rich(
      TextSpan(children: aiInlineSpans(block.text, style)),
    );
    return switch (block.kind) {
      AiBlockKind.paragraph => body,
      AiBlockKind.bullet => _Hanging(marker: '•', style: style, child: body),
      AiBlockKind.number => _Hanging(
        marker: '${block.number}.',
        style: style,
        child: body,
      ),
    };
  }
}

/// 列表项：序号在左，文字换行时对齐到文字起点。
class _Hanging extends StatelessWidget {
  const _Hanging({
    required this.marker,
    required this.child,
    required this.style,
  });

  final String marker;
  final Widget child;
  final TextStyle style;

  @override
  Widget build(BuildContext context) => Row(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      SizedBox(
        width: 24,
        child: Text(marker, style: style.copyWith(color: CinemaColors.text2)),
      ),
      Expanded(child: child),
    ],
  );
}
