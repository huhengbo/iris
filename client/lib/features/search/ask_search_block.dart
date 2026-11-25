import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/motion.dart';
import '../../app/shell.dart' show wideLayoutBreakpoint;
import '../../app/theme.dart';
import '../../core/widgets/ai_wait_indicator.dart';
import '../../core/widgets/picker_sheet.dart';
import '../../core/widgets/text_prompt.dart';
import '../ai/ai_availability.dart';
import '../ai/ai_chat_drawer.dart';
import '../library/poster_card.dart';
import 'ask_search_api.dart';

/// 搜索页的 AI 区块（DESIGN.md「搜索页 AI 区块」）：「AI 理解为」+ 可编辑的筛选胶囊 +
/// 一排结果 + 「去对话继续问」。普通结果不受它影响，AI 失败时只剩一行说明。
class AskSearchBlock extends ConsumerWidget {
  const AskSearchBlock({super.key, required this.query, required this.gutter});

  final String query;
  final double gutter;

  /// 结果卡片的宽度，和 AI 对话里的条目卡片一致。
  static const cardWidth = 96.0;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final provider = askSearchProvider(query);
    final value = ref.watch(provider);
    final text = Theme.of(context).textTheme;
    return Padding(
      padding: EdgeInsets.fromLTRB(gutter, 12, gutter, 8),
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: CinemaColors.surface1,
          borderRadius: BorderRadius.circular(CinemaRadius.card),
        ),
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              switch (value) {
                AsyncLoading(value: null) => const AiWaitIndicator(
                  label: '正在理解你的问题…',
                ),
                AsyncError() => Text(
                  'AI 暂不可用',
                  style: text.bodyMedium?.copyWith(color: CinemaColors.text2),
                ),
                AsyncValue(:final value?) => _Content(
                  query: query,
                  state: value,
                  onChange: (filters) =>
                      ref.read(provider.notifier).refine(filters),
                ),
              },
            ],
          ),
        ),
      ),
    );
  }
}

class _Content extends ConsumerWidget {
  const _Content({
    required this.query,
    required this.state,
    required this.onChange,
  });

  final String query;
  final AskSearchState state;
  final ValueChanged<AskFilters> onChange;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final text = Theme.of(context).textTheme;
    final muted = text.bodySmall?.copyWith(color: CinemaColors.text2);
    final canChat = ref.watch(aiAvailabilityProvider).chat;
    final filters = state.filters;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            const Icon(Icons.auto_awesome, size: 16, color: CinemaColors.amber),
            const SizedBox(width: 6),
            Text('AI 理解为', style: text.titleSmall),
            if (state.updating) ...[
              const SizedBox(width: 12),
              const AiWaitIndicator(label: '正在查询…'),
            ],
          ],
        ),
        const SizedBox(height: 8),
        if (filters.isEmpty)
          Text('没有提取出具体条件，换个说法试试，或者去对话里问', style: muted)
        else
          Wrap(
            spacing: 8,
            runSpacing: 4,
            children: [
              for (final condition in filters.active)
                InputChip(
                  label: Text(filters.describe(condition)),
                  onPressed: state.updating
                      ? null
                      : () => _edit(context, condition, filters),
                  onDeleted: state.updating
                      ? null
                      : () => onChange(filters.without(condition)),
                  deleteButtonTooltipMessage: '去掉这个条件',
                ),
            ],
          ),
        if (state.updateError != null) ...[
          const SizedBox(height: 4),
          Text(
            state.updateError!,
            style: muted?.copyWith(color: CinemaColors.error),
          ),
        ],
        // 没有任何条件时不列结果：否则会把整个库按评分摆出来，像是 AI 的推荐
        if (!filters.isEmpty) ...[
          const SizedBox(height: 12),
          if (state.items.isEmpty)
            Text(state.updating ? '' : '库里没有符合这些条件的片', style: muted)
          else
            _Row(items: state, query: query),
        ],
        if (!filters.isEmpty && state.total > state.items.length)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(
              '共 ${state.total} 部，只显示前 ${state.items.length} 部',
              style: muted,
            ),
          ),
        if (canChat)
          Align(
            alignment: Alignment.centerRight,
            child: TextButton.icon(
              // Web 宽屏在右侧抽屉里继续，手机进全屏对话页
              onPressed: () =>
                  MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint
                  ? showAiChatDrawer(context, initialQuestion: query)
                  : context.push(
                      Uri(
                        path: '/ai',
                        queryParameters: {'q': query},
                      ).toString(),
                    ),
              icon: const Icon(Icons.chat_bubble_outline, size: 18),
              label: const Text('去对话继续问'),
            ),
          ),
      ],
    );
  }

  Future<void> _edit(
    BuildContext context,
    AskCondition condition,
    AskFilters filters,
  ) async {
    switch (condition) {
      case AskCondition.kind:
        onChange(
          filters
              .without(AskCondition.kind)
              .copyWith(kind: filters.kind == 'series' ? 'movie' : 'series'),
        );
      case AskCondition.watched:
        onChange(
          filters
              .without(AskCondition.watched)
              .copyWith(watched: !(filters.watched ?? false)),
        );
      case AskCondition.genres:
        final picked = await _pickGenres(context, filters.genres);
        if (picked == null) return;
        onChange(
          picked.isEmpty
              ? filters.without(AskCondition.genres)
              : filters.without(AskCondition.genres).copyWith(genres: picked),
        );
      case AskCondition.years:
        await _editYears(context, filters);
      case AskCondition.rating:
        await _editRating(context, filters);
      case AskCondition.runtime:
        await _editRuntime(context, filters);
      case AskCondition.semantic:
        final value = await showTextPrompt(
          context,
          title: '描述',
          label: '想找什么样的片',
          action: '确定',
          initial: filters.semanticQuery ?? '',
        );
        if (value == null) return;
        onChange(
          filters.without(AskCondition.semantic).copyWith(semanticQuery: value),
        );
      case AskCondition.library:
        // 资料库只能去掉，不能在这里改成别的
        onChange(filters.without(AskCondition.library));
    }
  }

  static const _commonGenres = [
    '剧情',
    '喜剧',
    '动作',
    '科幻',
    '悬疑',
    '惊悚',
    '爱情',
    '动画',
    '犯罪',
    '冒险',
    '奇幻',
    '纪录片',
  ];

  Future<List<String>?> _pickGenres(
    BuildContext context,
    List<String> current,
  ) => showPickerSheet<List<String>>(
    context,
    builder: (context) =>
        _GenrePickerBody(initial: current, presets: _commonGenres),
  );

  Future<void> _editYears(BuildContext context, AskFilters filters) async {
    const custom = _YearPreset.custom;
    final current = switch ((filters.yearFrom, filters.yearTo)) {
      (2020, null) => _YearPreset.after2020,
      (2010, 2019) => _YearPreset.y2010s,
      (2000, 2009) => _YearPreset.y2000s,
      (null, 1999) => _YearPreset.before2000,
      _ => null,
    };
    final picked = await showChoicePicker<_YearPreset>(
      context,
      title: '年份',
      current: current,
      choices: const [
        (value: _YearPreset.after2020, label: '2020 年以后', subtitle: null),
        (value: _YearPreset.y2010s, label: '2010–2019', subtitle: null),
        (value: _YearPreset.y2000s, label: '2000–2009', subtitle: null),
        (value: _YearPreset.before2000, label: '2000 年以前', subtitle: null),
        (value: custom, label: '自定义年份…', subtitle: null),
      ],
    );
    if (picked == null || !context.mounted) return;
    final preset = picked.$1;
    if (preset != custom) {
      final cleared = filters.without(AskCondition.years);
      onChange(cleared.copyWith(yearFrom: preset.from, yearTo: preset.to));
      return;
    }
    final initial = switch ((filters.yearFrom, filters.yearTo)) {
      (final f?, final t?) when f == t => '$f',
      (final f?, final t?) => '$f-$t',
      (final f?, null) => '$f-',
      (null, final t?) => '-$t',
      _ => '',
    };
    final value = await showTextPrompt(
      context,
      title: '自定义年份',
      label: '如 2023 或 2000-2010、2000-、-1999',
      action: '确定',
      initial: initial,
    );
    if (value == null) return;
    final parts = value.split(RegExp(r'[-–—~到至]'));
    final from = int.tryParse(parts.first.trim());
    final to = parts.length > 1 ? int.tryParse(parts[1].trim()) : from;
    final cleared = filters.without(AskCondition.years);
    onChange(
      from == null && to == null
          ? cleared
          : cleared.copyWith(yearFrom: from, yearTo: to),
    );
  }

  Future<void> _editRating(BuildContext context, AskFilters filters) async {
    const custom = -1.0;
    final picked = await showChoicePicker<double>(
      context,
      title: '最低评分',
      current: filters.minRating,
      choices: const [
        (value: 9.0, label: '9.0 分以上', subtitle: null),
        (value: 8.5, label: '8.5 分以上', subtitle: null),
        (value: 8.0, label: '8.0 分以上', subtitle: null),
        (value: 7.5, label: '7.5 分以上', subtitle: null),
        (value: 7.0, label: '7.0 分以上', subtitle: null),
        (value: custom, label: '自定义评分…', subtitle: null),
      ],
    );
    if (picked == null || !context.mounted) return;
    double? rating = picked.$1;
    if (rating == custom) {
      final value = await showTextPrompt(
        context,
        title: '自定义最低评分',
        label: '0 到 10',
        action: '确定',
        initial: filters.minRating?.toStringAsFixed(1) ?? '',
      );
      rating = double.tryParse(value ?? '');
      if (rating == null || rating < 0 || rating > 10) return;
    }
    onChange(filters.without(AskCondition.rating).copyWith(minRating: rating));
  }

  Future<void> _editRuntime(BuildContext context, AskFilters filters) async {
    const custom = -1;
    final picked = await showChoicePicker<int>(
      context,
      title: '最长时长',
      current: filters.maxRuntime,
      choices: const [
        (value: 90, label: '90 分钟以内', subtitle: null),
        (value: 120, label: '2 小时以内', subtitle: null),
        (value: 150, label: '2.5 小时以内', subtitle: null),
        (value: custom, label: '自定义时长…', subtitle: null),
      ],
    );
    if (picked == null || !context.mounted) return;
    int? minutes = picked.$1;
    if (minutes == custom) {
      final value = await showTextPrompt(
        context,
        title: '自定义最长时长',
        label: '分钟',
        action: '确定',
        initial: '${filters.maxRuntime ?? ''}',
      );
      minutes = int.tryParse(value ?? '');
      if (minutes == null || minutes < 1) return;
    }
    onChange(
      filters.without(AskCondition.runtime).copyWith(maxRuntime: minutes),
    );
  }
}

enum _YearPreset {
  after2020(2020, null),
  y2010s(2010, 2019),
  y2000s(2000, 2009),
  before2000(null, 1999),
  custom(null, null);

  const _YearPreset(this.from, this.to);
  final int? from;
  final int? to;
}

class _GenrePickerBody extends StatefulWidget {
  const _GenrePickerBody({required this.initial, required this.presets});

  final List<String> initial;
  final List<String> presets;

  @override
  State<_GenrePickerBody> createState() => _GenrePickerBodyState();
}

class _GenrePickerBodyState extends State<_GenrePickerBody> {
  late final Set<String> _selected = Set<String>.of(widget.initial);
  late final List<String> _options = [
    ...widget.presets,
    for (final g in widget.initial)
      if (!widget.presets.contains(g)) g,
  ];
  final _extra = TextEditingController();

  @override
  void dispose() {
    _extra.dispose();
    super.dispose();
  }

  void _apply() {
    final extras = _extra.text
        .split(RegExp(r'[、,，\s]+'))
        .where((g) => g.isNotEmpty);
    final result = <String>{..._selected, ...extras}.toList();
    Navigator.of(context).pop(result);
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const PickerHeader(title: '类型'),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final genre in _options)
              FilterChip(
                label: Text(genre),
                selected: _selected.contains(genre),
                onSelected: (on) => setState(() {
                  if (on) {
                    _selected.add(genre);
                  } else {
                    _selected.remove(genre);
                  }
                }),
              ),
          ],
        ),
        const SizedBox(height: 12),
        TextField(
          controller: _extra,
          textInputAction: TextInputAction.done,
          onSubmitted: (_) => _apply(),
          decoration: const InputDecoration(labelText: '其他类型（可选，顿号或逗号分隔）'),
        ),
        const SizedBox(height: 16),
        Row(
          mainAxisAlignment: MainAxisAlignment.end,
          children: [
            TextButton(
              onPressed: () => Navigator.of(context).pop(),
              child: const Text('取消'),
            ),
            const SizedBox(width: 8),
            FilledButton(onPressed: _apply, child: const Text('应用')),
          ],
        ),
      ],
    );
  }
}

/// 一排可横向滚动的结果，逐张淡入并上移 4%（减少动态效果时只淡入）。
class _Row extends StatelessWidget {
  const _Row({required this.items, required this.query});

  final AskSearchState items;
  final String query;

  @override
  Widget build(BuildContext context) {
    final reduced = Motion.reduced(context);
    return SizedBox(
      height: AskSearchBlock.cardWidth * 1.5 + 8 + 20 + 18,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        itemCount: items.items.length,
        separatorBuilder: (_, _) => const SizedBox(width: 8),
        itemBuilder: (context, index) {
          final media = items.items[index];
          return TweenAnimationBuilder<double>(
            tween: Tween(begin: 0, end: 1),
            duration: Motion.short,
            curve: Motion.emphasized,
            builder: (context, t, child) => Opacity(
              opacity: t,
              child: Transform.translate(
                offset: reduced ? Offset.zero : Offset(0, 6 * (1 - t)),
                child: child,
              ),
            ),
            child: SizedBox(
              width: AskSearchBlock.cardWidth,
              child: PosterCard(
                media: media,
                heroTag: 'ask:${media.id}',
                subtitle: media.year?.toString(),
              ),
            ),
          );
        },
      ),
    );
  }
}
