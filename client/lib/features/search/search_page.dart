import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/shell.dart';
import '../../app/theme.dart';
import '../../core/api/paging.dart';
import '../../core/format.dart';
import '../library/art_image.dart';
import '../library/browse_api.dart';
import '../library/poster_card.dart';
import '../../core/widgets/empty_state.dart';

final searchHistoryProvider =
    NotifierProvider<SearchHistoryNotifier, List<String>>(
      SearchHistoryNotifier.new,
    );

class SearchHistoryNotifier extends Notifier<List<String>> {
  static const _max = 8;

  @override
  List<String> build() => const [];

  void add(String query) {
    final value = query.trim();
    if (value.isEmpty) return;
    state = [
      value,
      ...state.where((item) => item != value),
    ].take(_max).toList();
  }

  void remove(String query) {
    state = [
      for (final item in state)
        if (item != query) item,
    ];
  }

  void clear() => state = const [];
}

/// 按片名、原名、英文名和演职员姓名搜索当前账号能看到的影片。
/// 查询词写在地址里（/search?q=），停止输入 300 毫秒后才搜。
class SearchPage extends ConsumerStatefulWidget {
  const SearchPage({super.key, this.query = ''});

  /// 地址里的查询词，是实际搜索的内容
  final String query;

  @override
  ConsumerState<SearchPage> createState() => _SearchPageState();
}

class _SearchPageState extends ConsumerState<SearchPage> {
  static const _debounce = Duration(milliseconds: 300);

  late final _controller = TextEditingController(text: widget.query);
  Timer? _timer;

  @override
  void didUpdateWidget(SearchPage oldWidget) {
    super.didUpdateWidget(oldWidget);
    // 从别处带着查询词跳过来时，输入框跟着地址走
    if (widget.query != oldWidget.query &&
        widget.query.trim() != _controller.text.trim()) {
      _controller.text = widget.query;
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    _controller.dispose();
    super.dispose();
  }

  void _onChanged(String text) {
    _timer?.cancel();
    _timer = Timer(_debounce, () => _search(text));
  }

  void _search(String text) {
    _timer?.cancel();
    final q = text.trim();
    if (!mounted || q == widget.query.trim()) return;
    ref.read(searchHistoryProvider.notifier).add(q);
    // go 才会更新地址；neglect 让浏览器替换当前历史而不是每搜一次加一条
    Router.neglect(
      context,
      () => context.go(
        Uri(
          path: '/search',
          queryParameters: q.isEmpty ? null : {'q': q},
        ).toString(),
      ),
    );
  }

  void _clear() {
    _controller.clear();
    _search('');
  }

  @override
  Widget build(BuildContext context) {
    final query = widget.query.trim();
    final results = ref.watch(searchResultsProvider(query));
    final discovery = results.value?.discovery;
    final hasDiscovery =
        discovery != null &&
        (discovery.people.isNotEmpty || discovery.episodes.isNotEmpty);
    final history = ref.watch(searchHistoryProvider);
    final notifier = ref.read(searchResultsProvider(query).notifier);
    final gutter = CinemaSpacing.gutterFor(
      wide: MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint,
    );
    return Scaffold(
      body: CustomScrollView(
        keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
        slivers: [
          SliverAppBar(
            pinned: true,
            automaticallyImplyLeading: false,
            titleSpacing: gutter,
            toolbarHeight: 64,
            title: TextField(
              controller: _controller,
              autofocus: query.isEmpty,
              textInputAction: TextInputAction.search,
              maxLength: 100,
              onChanged: _onChanged,
              onSubmitted: _search,
              decoration: InputDecoration(
                hintText: '片名、人名、原名或英文名',
                counterText: '',
                prefixIcon: const Icon(Icons.search),
                suffixIcon: ListenableBuilder(
                  listenable: _controller,
                  builder: (_, _) => _controller.text.isEmpty
                      ? const SizedBox.shrink()
                      : IconButton(
                          tooltip: '清除',
                          onPressed: _clear,
                          icon: const Icon(Icons.close),
                        ),
                ),
              ),
            ),
          ),
          if (query.isEmpty && history.isEmpty)
            const SliverFillRemaining(
              hasScrollBody: false,
              child: EmptyState(
                icon: Icons.search,
                title: '搜索影片和剧集',
                message: '中文片名、原名、英文名和演员、导演姓名都可以',
              ),
            )
          else if (query.isEmpty)
            SliverFillRemaining(
              hasScrollBody: false,
              child: _SearchHistory(
                gutter: gutter,
                history: history,
                onPick: (value) {
                  _controller.text = value;
                  _search(value);
                },
                onRemove: (value) =>
                    ref.read(searchHistoryProvider.notifier).remove(value),
                onClear: () => ref.read(searchHistoryProvider.notifier).clear(),
              ),
            )
          else ...[
            if (discovery != null)
              SliverToBoxAdapter(
                child: _DiscoveryResults(value: discovery, gutter: gutter),
              ),
            // 上面有人物或单集时，影片也给一个同级标题，分清各块
            if (hasDiscovery && (results.value?.items.isNotEmpty ?? false))
              SliverPadding(
                padding: EdgeInsets.fromLTRB(
                  gutter,
                  CinemaSpacing.section,
                  gutter,
                  CinemaSpacing.heading - 8,
                ),
                sliver: const SliverToBoxAdapter(
                  child: _SectionHeading('影片和剧集'),
                ),
              ),
            PagedPosterGrid(
              value: results,
              heroPrefix: 'search',
              padding: EdgeInsets.fromLTRB(gutter, 8, gutter, 0),
              subtitle: (media) => media.year?.toString(),
              onRetry: () => ref.invalidate(searchResultsProvider(query)),
              onLoadMore: notifier.loadMore,
              onRetryMore: notifier.retryMore,
              // 已有人物或单集结果时，不再提示「没有找到」
              empty: hasDiscovery
                  ? const SizedBox.shrink()
                  : EmptyState(
                      icon: Icons.search_off,
                      title: '没有找到「$query」',
                      message:
                          '检查拼写是否有误\n'
                          '缩短关键词，或只搜片名中的一部分\n'
                          '确认账号有权限访问对应资料库，且资料库已扫描',
                      action: TextButton(
                        onPressed: _clear,
                        child: const Text('清除搜索'),
                      ),
                    ),
            ),
          ],
        ],
      ),
    );
  }
}

/// 区块标题：和首页片架的标题同级（titleMedium + text0），读屏当作标题。
class _SectionHeading extends StatelessWidget {
  const _SectionHeading(this.title);

  final String title;

  @override
  Widget build(BuildContext context) => Semantics(
    header: true,
    child: Text(
      title,
      style: Theme.of(context).textTheme.titleMedium!
          .copyWith(color: CinemaColors.text0),
    ),
  );
}

/// 人物和单集：一行可横向滚动的胶囊（头像或剧照 + 名称），聚焦描边由主题的
/// Chip 样式给出。首尾留出 gutter，中间滑到屏幕边。
class _DiscoveryResults extends StatelessWidget {
  const _DiscoveryResults({required this.value, required this.gutter});

  final SearchDiscovery value;
  final double gutter;

  @override
  Widget build(BuildContext context) {
    if (value.people.isEmpty && value.episodes.isEmpty) {
      return const SizedBox.shrink();
    }
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (value.people.isNotEmpty)
            _section(context, '人物', [
              for (final person in value.people)
                _DiscoveryChip(
                  art: person.profile,
                  icon: Icons.person_outline,
                  label: person.name,
                  onPressed: () => context.push('/person/${person.id}'),
                ),
            ]),
          if (value.people.isNotEmpty && value.episodes.isNotEmpty)
            const SizedBox(height: CinemaSpacing.section),
          if (value.episodes.isNotEmpty)
            _section(context, '单集', [
              for (final episode in value.episodes)
                _DiscoveryChip(
                  art: episode.still,
                  icon: Icons.live_tv_outlined,
                  label: [
                    episode.showTitle,
                    if (episode.name case final name? when name.isNotEmpty)
                      name
                    else
                      formatEpisode(episode.seasonNumber, episode.number),
                  ].join(' · '),
                  onPressed: () => context.push(
                    '/item/${episode.itemId}/episode/${episode.id}?season=${episode.seasonId}',
                  ),
                ),
            ]),
        ],
      ),
    );
  }

  Widget _section(BuildContext context, String title, List<Widget> chips) =>
      Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: EdgeInsets.symmetric(horizontal: gutter),
            child: _SectionHeading(title),
          ),
          const SizedBox(height: CinemaSpacing.heading),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            padding: EdgeInsets.symmetric(horizontal: gutter),
            child: Row(
              children: [
                for (final (i, chip) in chips.indexed) ...[
                  if (i > 0) const SizedBox(width: 8),
                  chip,
                ],
              ],
            ),
          ),
        ],
      );
}

class _DiscoveryChip extends StatelessWidget {
  const _DiscoveryChip({
    required this.art,
    required this.icon,
    required this.label,
    required this.onPressed,
  });

  final Artwork art;
  final IconData icon;
  final String label;
  final VoidCallback onPressed;

  @override
  Widget build(BuildContext context) => ActionChip(
    // 头像和剧照按胶囊的默认尺寸裁成圆形，人物和单集一样大
    avatar: ClipOval(
      child: ArtImage(art: art, icon: icon),
    ),
    label: Text(label, maxLines: 1, overflow: TextOverflow.ellipsis),
    onPressed: onPressed,
  );
}

class _SearchHistory extends StatelessWidget {
  const _SearchHistory({
    required this.gutter,
    required this.history,
    required this.onPick,
    required this.onRemove,
    required this.onClear,
  });

  final double gutter;
  final List<String> history;
  final ValueChanged<String> onPick;
  final ValueChanged<String> onRemove;
  final VoidCallback onClear;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Align(
      alignment: Alignment.topCenter,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 640),
        child: Padding(
          padding: EdgeInsets.fromLTRB(
            gutter,
            CinemaSpacing.section,
            gutter,
            24,
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                '搜索影片和剧集',
                style: text.titleLarge!.copyWith(color: CinemaColors.text0),
              ),
              const SizedBox(height: 4),
              Text('中文片名、原名、英文名和演员、导演姓名都可以', style: text.bodySmall),
              const SizedBox(height: CinemaSpacing.section),
              Row(
                children: [
                  const Expanded(child: _SectionHeading('最近搜索')),
                  TextButton(onPressed: onClear, child: const Text('清空')),
                ],
              ),
              const SizedBox(height: CinemaSpacing.heading),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final value in history)
                    InputChip(
                      label: Text(value),
                      onPressed: () => onPick(value),
                      onDeleted: () => onRemove(value),
                    ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}
