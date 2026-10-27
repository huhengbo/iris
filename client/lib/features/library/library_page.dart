import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/shell.dart' show wideLayoutBreakpoint;
import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/widgets/async_view.dart';
import 'browse_api.dart';
import 'collections_page.dart';
import 'library_categories_tab.dart';
import 'library_recommend_tab.dart';
import 'library_tab.dart';

/// 资料库页的四个标签，顺序照搬 Plex，默认进「推荐」。
enum LibraryTab {
  recommend('推荐', null),
  library('资料库', 'library'),
  collections('合集', 'collections'),
  categories('分类', 'categories');

  const LibraryTab(this.label, this.value);

  final String label;

  /// 地址里的 tab 参数；推荐是默认值，不写出来
  final String? value;

  static LibraryTab fromParams(Map<String, String> params) =>
      values.where((tab) => tab.value == params['tab']).firstOrNull ??
      LibraryTab.recommend;
}

/// 「资料库」标签上属于排序和筛选的地址参数；从推荐行或分类进入时先清掉。
const _browseParams = {
  'sort',
  'order',
  'genre',
  'year',
  'watched',
  'favorite',
  'watchlist',
  'status',
  'letter',
};

/// 资料库页：顶部标签「推荐 / 资料库 / 合集 / 分类」，当前标签写在地址的 tab 参数里，
/// 刷新、返回和分享链接都能还原。/library/:id 和 /libraries/:id 两套路由共用这个页面。
class LibraryPage extends ConsumerStatefulWidget {
  const LibraryPage({super.key, required this.id, this.params = const {}});

  final int id;
  final Map<String, String> params;

  @override
  ConsumerState<LibraryPage> createState() => _LibraryPageState();
}

class _LibraryPageState extends ConsumerState<LibraryPage>
    with SingleTickerProviderStateMixin {
  late LibraryTab _tab = LibraryTab.fromParams(widget.params);
  late final TabController _controller = TabController(
    length: LibraryTab.values.length,
    initialIndex: _tab.index,
    vsync: this,
  );

  @override
  void didUpdateWidget(covariant LibraryPage oldWidget) {
    super.didUpdateWidget(oldWidget);
    final tab = LibraryTab.fromParams(widget.params);
    if (tab != _tab) {
      _tab = tab;
      _controller.index = tab.index;
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _goTo(
    LibraryTab tab, {
    Map<String, String> extra = const {},
    Set<String> clear = const {},
  }) {
    final params = {...GoRouterState.of(context).uri.queryParameters, ...extra};
    for (final key in clear) {
      params.remove(key);
    }
    if (tab.value == null) {
      params.remove('tab');
    } else {
      params['tab'] = tab.value!;
    }
    Router.neglect(
      context,
      () => context.go(
        Uri(
          path: GoRouterState.of(context).uri.path,
          queryParameters: params.isEmpty ? null : params,
        ).toString(),
      ),
    );
  }

  /// 推荐行右上角「全部」：切到「资料库」标签并清掉上一次的筛选，按这一行的排序或类型重新开始。
  void _openHub(HubRow hub) {
    final query = queryForHub(widget.id, hub);
    _goTo(LibraryTab.library, extra: query.toParams(), clear: _browseParams);
  }

  /// 分类卡片：切到「资料库」标签并带上这个类型，其他筛选和 A-Z 跳转清掉。
  void _openGenre(String genre) {
    _goTo(
      LibraryTab.library,
      extra: {'genre': genre},
      clear: {'year', 'watched', 'favorite', 'watchlist', 'status', 'letter'},
    );
  }

  @override
  Widget build(BuildContext context) {
    final library = ref.watch(libraryProvider(widget.id));
    final detail = library.value;
    // 宽屏从侧栏直接进各资料库，是顶层页面，不显示返回箭头
    final wide = MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint;
    final titleSpacing = wide ? CinemaSpacing.gutterFor(wide: true) : null;
    if (detail == null) {
      return Scaffold(
        appBar: AppBar(
          title: const Text('资料库'),
          automaticallyImplyLeading: !wide,
          titleSpacing: titleSpacing,
        ),
        body: library.hasError
            ? ErrorRetry(
                message: ApiError.from(library.error!).message,
                onRetry: () => ref.invalidate(libraryProvider(widget.id)),
              )
            : const Center(child: CircularProgressIndicator()),
      );
    }
    return Scaffold(
      appBar: AppBar(
        title: Text(detail.library.name),
        automaticallyImplyLeading: !wide,
        titleSpacing: titleSpacing,
        bottom: TabBar(
          controller: _controller,
          isScrollable: true,
          tabAlignment: TabAlignment.start,
          onTap: (index) => _goTo(LibraryTab.values[index]),
          tabs: [for (final tab in LibraryTab.values) Tab(text: tab.label)],
        ),
      ),
      body: switch (_tab) {
        LibraryTab.recommend => LibraryRecommendTab(
          libraryId: widget.id,
          onOpenHub: _openHub,
        ),
        LibraryTab.library => LibraryBrowseTab(
          libraryId: widget.id,
          params: widget.params,
          detail: detail,
        ),
        LibraryTab.collections => CollectionsPage(
          libraryId: widget.id,
          embedded: true,
        ),
        LibraryTab.categories => LibraryCategoriesTab(
          detail: detail,
          onOpenGenre: _openGenre,
        ),
      },
    );
  }
}
