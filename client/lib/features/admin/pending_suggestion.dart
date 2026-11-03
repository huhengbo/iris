part of 'pending_page.dart';

/// 待处理行里的 AI 匹配建议（DESIGN.md「匹配建议」）：分析中、有候选、没把握、已忽略和已撤销几种状态。
class _SuggestionLine extends StatefulWidget {
  const _SuggestionLine({
    super.key,
    required this.work,
    required this.analyzing,
    required this.busy,
    required this.onAdopt,
    required this.onPickOther,
    required this.onIgnore,
    required this.onReanalyze,
  });

  final PendingWork work;

  /// 还没有建议，但后台任务会处理它
  final bool analyzing;
  final bool busy;
  final VoidCallback onAdopt;
  final VoidCallback onPickOther;
  final VoidCallback onIgnore;
  final VoidCallback onReanalyze;

  @override
  State<_SuggestionLine> createState() => _SuggestionLineState();
}

enum _SuggestionMenu { other, ignore, reanalyze }

class _SuggestionLineState extends State<_SuggestionLine> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) {
    final suggestion = widget.work.suggestion;
    if (suggestion == null) {
      return widget.analyzing
          ? const Padding(
              padding: EdgeInsets.only(top: 4),
              child: AiWaitIndicator(label: 'AI 分析中'),
            )
          : const SizedBox.shrink();
    }
    final text = Theme.of(context).textTheme;
    final muted = text.bodySmall?.copyWith(color: CinemaColors.text2);
    // 不在待处理状态的旧建议（忽略、撤销、采用或自动匹配后又取消匹配）：只提供重新分析
    if (suggestion.status != 'pending') {
      return Row(
        children: [
          const Icon(
            Icons.auto_awesome_outlined,
            size: 16,
            color: CinemaColors.text2,
          ),
          const SizedBox(width: 6),
          Expanded(
            child: Text(switch (suggestion.status) {
              'ignored' => '已忽略 AI 建议',
              'reverted' => '已撤销 AI 自动匹配',
              _ => '之前的 AI 建议已用过，匹配被取消后可以重新分析',
            }, style: muted),
          ),
          TextButton(
            onPressed: widget.busy ? null : widget.onReanalyze,
            child: const Text('重新分析'),
          ),
        ],
      );
    }
    final adoptable = suggestion.adoptable;
    final headline = adoptable
        ? '${suggestion.label} · ${(suggestion.confidence * 100).round()}%'
        : 'AI 没有把握';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: InkWell(
                onTap: suggestion.steps.isEmpty
                    ? null
                    : () => setState(() => _expanded = !_expanded),
                borderRadius: BorderRadius.circular(CinemaRadius.small),
                child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 4),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Icon(
                        Icons.auto_awesome,
                        size: 16,
                        color: CinemaColors.amber,
                      ),
                      const SizedBox(width: 6),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              headline,
                              style: text.bodyMedium,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                            Text(
                              suggestion.reason,
                              style: muted,
                              maxLines: _expanded ? null : 1,
                              overflow: _expanded
                                  ? null
                                  : TextOverflow.ellipsis,
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
            if (adoptable)
              TextButton(
                onPressed: widget.busy ? null : widget.onAdopt,
                child: Text(
                  '采用',
                  semanticsLabel: '采用 AI 建议 ${suggestion.label}',
                ),
              ),
            PopupMenuButton<_SuggestionMenu>(
              tooltip: 'AI 建议的更多操作',
              enabled: !widget.busy,
              onSelected: (value) => switch (value) {
                _SuggestionMenu.other => widget.onPickOther(),
                _SuggestionMenu.ignore => widget.onIgnore(),
                _SuggestionMenu.reanalyze => widget.onReanalyze(),
              },
              itemBuilder: (_) => [
                if (adoptable) ...const [
                  PopupMenuItem(
                    value: _SuggestionMenu.other,
                    child: Text('换一个'),
                  ),
                  PopupMenuItem(
                    value: _SuggestionMenu.ignore,
                    child: Text('忽略'),
                  ),
                ],
                const PopupMenuItem(
                  value: _SuggestionMenu.reanalyze,
                  child: Text('重新分析'),
                ),
              ],
            ),
          ],
        ),
        AnimatedSize(
          duration: Motion.short,
          curve: Motion.emphasized,
          alignment: Alignment.topLeft,
          child: _expanded
              ? Padding(
                  padding: const EdgeInsets.only(left: 22, bottom: 4),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    spacing: 2,
                    children: [
                      for (final step in suggestion.steps)
                        Row(
                          children: [
                            Icon(
                              step.ok ? Icons.check : Icons.error_outline,
                              size: 14,
                              color: step.ok
                                  ? CinemaColors.text2
                                  : CinemaColors.error,
                            ),
                            const SizedBox(width: 6),
                            Expanded(child: Text(step.summary, style: muted)),
                          ],
                        ),
                    ],
                  ),
                )
              : const SizedBox(width: double.infinity),
        ),
      ],
    );
  }
}
