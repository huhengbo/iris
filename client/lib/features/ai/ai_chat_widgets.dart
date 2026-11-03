import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../app/motion.dart';
import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/widgets/ai_wait_indicator.dart';
import '../../core/widgets/api_error_style.dart';
import '../../core/widgets/focus_ring.dart';
import '../library/browse_api.dart';
import '../library/poster_card.dart';
import 'ai_chat_models.dart';
import 'ai_markdown.dart';

/// 对话内容左右留白，和页面边距一致。
const aiGutter = CinemaSpacing.gutter;

/// 空对话里的入门问句。
const aiStarterQuestions = [
  '推荐一部适合周末看的轻松电影',
  '最近有什么高分科幻片',
  '我还没看过的悬疑片',
  '评分高又不太长的电影',
];

/// 条目卡片一排最多放几张，多出来的收进「查看全部」。
const aiMaxCards = 10;

/// 一条消息。用户消息是右对齐的 surface2 气泡；助手消息没有气泡，
/// 自上而下：思考行 → 步骤行 → 条目卡片 → 正文 → 建议追问。
class AiMessageView extends StatelessWidget {
  const AiMessageView({
    super.key,
    required this.message,
    required this.isLast,
    required this.busy,
    required this.onSuggestion,
    required this.onRetry,
  });

  final AiMessage message;

  /// 只有最后一条助手消息显示建议追问和「重试」
  final bool isLast;
  final bool busy;
  final ValueChanged<String> onSuggestion;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    if (message.isUser) return _UserBubble(text: message.text);
    final m = message;
    final waiting =
        m.streaming &&
        m.text.isEmpty &&
        !m.thinking &&
        !m.steps.any((s) => s.running);
    final sections = <Widget>[
      if (m.thinking || m.thinkingSeconds != null)
        _Padded(child: AiThinkingRow(message: m)),
      if (m.steps.isNotEmpty) _Padded(child: AiStepsGroup(message: m)),
      if (m.items.isNotEmpty)
        AiItemRow(
          items: m.items,
          question: m.question,
          heroScope: 'ai:${m.key}',
          animate: m.live,
        ),
      if (m.text.isNotEmpty)
        _Padded(child: SelectionArea(child: AiMarkdown(m.text))),
      if (waiting) const _Padded(child: AiWaitIndicator()),
      if (m.status == AiMessageStatus.stopped)
        _Padded(
          child: Text(
            '已停止',
            style: Theme.of(context).textTheme.bodySmall!
                .copyWith(color: CinemaColors.text2),
          ),
        ),
      if (m.status == AiMessageStatus.error && m.error != null)
        _Padded(
          child: AiErrorLine(
            error: m.error!,
            onRetry: isLast && !busy ? onRetry : null,
          ),
        ),
      if (isLast &&
          !busy &&
          m.status == AiMessageStatus.done &&
          m.suggestions.isNotEmpty)
        _Padded(
          child: AiSuggestionChips(
            suggestions: m.suggestions,
            onPick: onSuggestion,
          ),
        ),
    ];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (var i = 0; i < sections.length; i++) ...[
          if (i > 0) const SizedBox(height: 12),
          sections[i],
        ],
      ],
    );
  }
}

class _Padded extends StatelessWidget {
  const _Padded({required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(horizontal: aiGutter),
    child: child,
  );
}

class _UserBubble extends StatelessWidget {
  const _UserBubble({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(horizontal: aiGutter),
    child: Align(
      alignment: Alignment.centerRight,
      child: LayoutBuilder(
        builder: (context, constraints) => ConstrainedBox(
          constraints: BoxConstraints(maxWidth: constraints.maxWidth * 0.82),
          child: DecoratedBox(
            decoration: BoxDecoration(
              color: CinemaColors.surface2,
              borderRadius: BorderRadius.circular(CinemaRadius.card),
            ),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              child: SelectionArea(
                child: Text(
                  text,
                  style: Theme.of(context).textTheme.bodyMedium!
                      .copyWith(color: CinemaColors.text0),
                ),
              ),
            ),
          ),
        ),
      ),
    ),
  );
}

/// 展开收起的高度变化走 [Motion.short]，减少动态效果时即时。
class _Collapsible extends StatelessWidget {
  const _Collapsible({required this.open, required this.child});

  final bool open;
  final Widget child;

  @override
  Widget build(BuildContext context) => AnimatedSize(
    duration: Motion.reduced(context) ? Duration.zero : Motion.short,
    curve: Motion.emphasized,
    alignment: Alignment.topLeft,
    child: open ? child : const SizedBox(width: double.infinity),
  );
}

/// 一行可点的「标题 + 展开箭头」，思考行和步骤行共用。
class _DisclosureRow extends StatelessWidget {
  const _DisclosureRow({
    required this.label,
    required this.open,
    required this.onTap,
  });

  final String label;
  final bool open;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final style = Theme.of(context).textTheme.bodySmall!
        .copyWith(color: CinemaColors.text2);
    final row = Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Flexible(
          child: Text(
            label,
            style: style,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ),
        if (onTap != null) ...[
          const SizedBox(width: 2),
          AnimatedRotation(
            turns: open ? 0.5 : 0,
            duration: Motion.reduced(context) ? Duration.zero : Motion.short,
            curve: Motion.emphasized,
            child: const Icon(
              Icons.expand_more,
              size: 18,
              color: CinemaColors.text2,
            ),
          ),
        ],
      ],
    );
    if (onTap == null) return row;
    return Semantics(
      button: true,
      expanded: open,
      label: label,
      child: ExcludeSemantics(
        child: FocusRing(
          radius: CinemaRadius.small,
          child: InkWell(
            borderRadius: BorderRadius.circular(CinemaRadius.small),
            onTap: onTap,
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 2),
              child: row,
            ),
          ),
        ),
      ),
    );
  }
}

/// 思考行：思考中只显示等待指示；结束后收成「已思考 N 秒」，有原文时可展开。
class AiThinkingRow extends StatefulWidget {
  const AiThinkingRow({super.key, required this.message});

  final AiMessage message;

  @override
  State<AiThinkingRow> createState() => _AiThinkingRowState();
}

class _AiThinkingRowState extends State<AiThinkingRow> {
  bool _open = false;

  @override
  Widget build(BuildContext context) {
    final m = widget.message;
    if (m.thinking) return const AiWaitIndicator();
    final seconds = m.thinkingSeconds;
    if (seconds == null) return const SizedBox.shrink();
    final text = m.thinkingText.trim();
    final label = '已思考 ${math.max(1, seconds.round())} 秒';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _DisclosureRow(
          label: label,
          open: _open,
          onTap: text.isEmpty ? null : () => setState(() => _open = !_open),
        ),
        _Collapsible(
          open: _open && text.isNotEmpty,
          child: Padding(
            padding: const EdgeInsets.only(top: 6),
            child: SizedBox(
              width: double.infinity,
              child: DecoratedBox(
                decoration: BoxDecoration(
                  color: CinemaColors.surface2,
                  borderRadius: BorderRadius.circular(CinemaRadius.card),
                ),
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Text(
                    text,
                    style: Theme.of(context).textTheme.bodySmall!
                        .copyWith(color: CinemaColors.text1),
                  ),
                ),
              ),
            ),
          ),
        ),
      ],
    );
  }
}

/// 步骤行：运行中每次工具调用一行；正文开始输出（或消息结束）后整组收成
/// 「查了 N 步 · 汇总」，点开看每一步。
class AiStepsGroup extends StatefulWidget {
  const AiStepsGroup({super.key, required this.message});

  final AiMessage message;

  @override
  State<AiStepsGroup> createState() => _AiStepsGroupState();
}

class _AiStepsGroupState extends State<AiStepsGroup> {
  /// 用户手动展开 / 收起后不再跟随自动状态
  bool? _manual;

  String get _summary {
    final steps = widget.message.steps;
    final shown = steps.lastWhere(
      (s) => s.ok && !s.running,
      orElse: () => steps.last,
    );
    return shown.summary;
  }

  @override
  Widget build(BuildContext context) {
    final m = widget.message;
    final live = m.streaming && m.text.isEmpty;
    final open = _manual ?? live;
    final summary = _summary;
    final label =
        '查了 ${m.steps.length} 步${summary.isEmpty ? '' : ' · $summary'}';
    final list = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (var i = 0; i < m.steps.length; i++) ...[
          if (i > 0) const SizedBox(height: 6),
          _StepRow(step: m.steps[i]),
        ],
      ],
    );
    if (live && _manual == null) return list;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _DisclosureRow(
          label: label,
          open: open,
          onTap: () => setState(() => _manual = !open),
        ),
        _Collapsible(
          open: open,
          child: Padding(padding: const EdgeInsets.only(top: 6), child: list),
        ),
      ],
    );
  }
}

class _StepRow extends StatelessWidget {
  const _StepRow({required this.step});

  final AiStep step;

  @override
  Widget build(BuildContext context) {
    if (step.running) return AiWaitIndicator(label: step.label);
    final style = Theme.of(context).textTheme.bodyMedium!
        .copyWith(color: step.ok ? CinemaColors.text1 : CinemaColors.text0);
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox.square(
          dimension: AiWaitIndicator.size,
          child: Icon(
            step.ok ? Icons.check : Icons.error_outline,
            size: 16,
            color: step.ok ? CinemaColors.text2 : CinemaColors.error,
          ),
        ),
        const SizedBox(width: 8),
        Expanded(child: Text(step.summary, style: style)),
      ],
    );
  }
}

/// 条目卡片：一排可横向滚动的海报卡（宽 96），逐张淡入 + 上移 4%，间隔 40ms；
/// 超过 [aiMaxCards] 部时最后一张是「查看全部 N 部」，进入搜索结果。
class AiItemRow extends StatelessWidget {
  const AiItemRow({
    super.key,
    required this.items,
    required this.question,
    required this.heroScope,
    required this.animate,
  });

  final List<MediaCard> items;
  final String question;

  /// Hero 标签的前缀，保证同屏多条消息里的同一部片子标签不重复
  final String heroScope;
  final bool animate;

  static const cardWidth = 96.0;
  static const _vertical = 8.0;

  @override
  Widget build(BuildContext context) {
    final scaler = MediaQuery.textScalerOf(context);
    final posterHeight = cardWidth * 3 / 2;
    final textHeight = PosterCard.textLines(subtitle: false)
        .map(scaler.scale)
        .fold(0.0, (a, b) => a + b);
    final more = items.length > aiMaxCards;
    final shown = more ? items.take(aiMaxCards).toList() : items;
    return SizedBox(
      height: posterHeight + 8 + textHeight.ceilToDouble() + _vertical * 2 + 2,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(
          horizontal: aiGutter,
          vertical: _vertical,
        ),
        itemCount: shown.length + (more ? 1 : 0),
        separatorBuilder: (_, _) => const SizedBox(width: 12),
        itemBuilder: (context, i) {
          final Widget card = i < shown.length
              ? PosterCard(
                  media: shown[i],
                  heroTag: '$heroScope:${shown[i].id}',
                )
              : _ViewAllCard(
                  total: items.length,
                  height: posterHeight,
                  onTap: () => context.go(
                    question.isEmpty
                        ? '/search'
                        : '/search?q=${Uri.encodeQueryComponent(question)}',
                  ),
                );
          return SizedBox(
            key: ValueKey(i < shown.length ? shown[i].id : 'all'),
            width: cardWidth,
            child: animate ? AiCardReveal(index: i, child: card) : card,
          );
        },
      ),
    );
  }
}

/// 淡入 + 上移 4%，第 [index] 张晚 40ms × index 开始。减少动态效果时只淡入。
class AiCardReveal extends StatefulWidget {
  const AiCardReveal({super.key, required this.index, required this.child});

  final int index;
  final Widget child;

  static const stagger = Duration(milliseconds: 40);

  @override
  State<AiCardReveal> createState() => _AiCardRevealState();
}

class _AiCardRevealState extends State<AiCardReveal>
    with SingleTickerProviderStateMixin {
  late final Duration _delay = AiCardReveal.stagger * widget.index;
  late final _controller = AnimationController(
    vsync: this,
    duration: _delay + Motion.short,
  )..forward();

  late final _curve = CurvedAnimation(
    parent: _controller,
    curve: Interval(
      _delay.inMicroseconds / (_delay + Motion.short).inMicroseconds,
      1,
      curve: Motion.emphasized,
    ),
  );

  @override
  void dispose() {
    _curve.dispose();
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final faded = FadeTransition(opacity: _curve, child: widget.child);
    if (Motion.reduced(context)) return faded;
    return SlideTransition(
      position: Tween(
        begin: const Offset(0, 0.04),
        end: Offset.zero,
      ).animate(_curve),
      child: faded,
    );
  }
}

class _ViewAllCard extends StatelessWidget {
  const _ViewAllCard({
    required this.total,
    required this.height,
    required this.onTap,
  });

  final int total;
  final double height;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    return Align(
      alignment: Alignment.topCenter,
      child: SizedBox(
        height: height,
        child: Semantics(
          button: true,
          label: '查看全部 $total 部',
          child: ExcludeSemantics(
            child: FocusRing(
              radius: CinemaRadius.poster,
              child: Material(
                color: CinemaColors.surface2,
                borderRadius: BorderRadius.circular(CinemaRadius.poster),
                child: InkWell(
                  borderRadius: BorderRadius.circular(CinemaRadius.poster),
                  onTap: onTap,
                  child: Center(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(
                          '查看全部',
                          style: textTheme.bodySmall!.copyWith(
                            color: CinemaColors.text0,
                          ),
                        ),
                        Text(
                          '$total 部',
                          style: textTheme.bodySmall!.copyWith(
                            color: CinemaColors.text2,
                          ),
                        ),
                        const SizedBox(height: 6),
                        const Icon(
                          Icons.arrow_forward,
                          size: 18,
                          color: CinemaColors.text1,
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// 建议追问胶囊：点击直接发送。
class AiSuggestionChips extends StatelessWidget {
  const AiSuggestionChips({
    super.key,
    required this.suggestions,
    required this.onPick,
  });

  final List<String> suggestions;
  final ValueChanged<String> onPick;

  @override
  Widget build(BuildContext context) => Wrap(
    spacing: 8,
    runSpacing: 8,
    children: [
      for (final s in suggestions)
        ActionChip(label: Text(s), onPressed: () => onPick(s)),
    ],
  );
}

/// 出错的消息末尾就地显示：小图标 + 人话原因 +「重试」。已生成的内容保留在上面。
class AiErrorLine extends StatelessWidget {
  const AiErrorLine({super.key, required this.error, this.onRetry});

  final ApiError error;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    final style = apiErrorStyle(error.kind, Theme.of(context).colorScheme);
    return Row(
      crossAxisAlignment: CrossAxisAlignment.center,
      children: [
        Icon(style.icon, size: 18, color: style.color),
        const SizedBox(width: 8),
        Expanded(
          child: Text(
            error.message,
            style: Theme.of(context).textTheme.bodyMedium!
                .copyWith(color: CinemaColors.text1),
          ),
        ),
        if (onRetry != null)
          TextButton(onPressed: onRetry, child: const Text('重试')),
      ],
    );
  }
}

/// 空对话：几个入门问句。
class AiEmptyChat extends StatelessWidget {
  const AiEmptyChat({super.key, required this.onPick});

  final ValueChanged<String> onPick;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    return Center(
      child: SingleChildScrollView(
        padding: const EdgeInsets.symmetric(horizontal: aiGutter, vertical: 24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(
              Icons.auto_awesome_outlined,
              size: 40,
              color: CinemaColors.text2,
            ),
            const SizedBox(height: 12),
            Text(
              '想看点什么？',
              style: textTheme.titleMedium!.copyWith(color: CinemaColors.text0),
            ),
            const SizedBox(height: 4),
            Text(
              '描述心情或口味，我来帮你从资料库里找',
              textAlign: TextAlign.center,
              style: textTheme.bodySmall,
            ),
            const SizedBox(height: 20),
            Wrap(
              alignment: WrapAlignment.center,
              spacing: 8,
              runSpacing: 8,
              children: [
                for (final q in aiStarterQuestions)
                  ActionChip(label: Text(q), onPressed: () => onPick(q)),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
