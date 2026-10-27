part of 'browse_api.dart';

// ---- 分页列表 ----

enum SortKey {
  added('最近添加'),
  release('上映时间'),
  rating('评分'),
  name('名称');

  const SortKey(this.label);

  final String label;

  /// 不指定方向时服务端的默认：名称从 A 到 Z，其余从新到旧、从高到低
  bool get defaultAscending => this == SortKey.name;

  static SortKey parse(String? value) =>
      values.where((k) => k.name == value).firstOrNull ?? SortKey.added;
}

enum ProblemStatus {
  noPoster('无海报'),
  multipleVersions('多个版本'),
  missing('有失效文件'),
  recentMetadata('最近修改');

  const ProblemStatus(this.label);

  final String label;

  static ProblemStatus? parse(String? value) => switch (value) {
    'no_poster' => noPoster,
    'multiple_versions' => multipleVersions,
    'missing' => missing,
    'recent_metadata' => recentMetadata,
    _ => null,
  };

  String get queryValue => switch (this) {
    noPoster => 'no_poster',
    multipleVersions => 'multiple_versions',
    missing => 'missing',
    recentMetadata => 'recent_metadata',
  };
}

/// 海报墙的排序和筛选，和页面地址的查询参数一一对应。
class LibraryQuery {
  const LibraryQuery({
    required this.libraryId,
    this.sort = SortKey.added,
    this.ascending,
    this.genre,
    this.year,
    this.watched,
    this.favorite,
    this.watchlist,
    this.status,
    this.letter,
  });

  factory LibraryQuery.fromUri(int libraryId, Map<String, String> params) {
    final sort = SortKey.parse(params['sort']);
    return LibraryQuery(
      libraryId: libraryId,
      sort: sort,
      ascending: switch (params['order']) {
        'asc' => true,
        'desc' => false,
        _ => null,
      },
      genre: params['genre']?.trim().nullIfEmpty,
      year: int.tryParse(params['year'] ?? ''),
      watched: switch (params['watched']) {
        'true' => true,
        'false' => false,
        _ => null,
      },
      // 服务端只支持 favorite=true；其他值视为没筛选
      favorite: params['favorite'] == 'true' ? true : null,
      watchlist: params['watchlist'] == 'true' ? true : null,
      status: ProblemStatus.parse(params['status']),
      // 只有按名称排序时才有 A-Z 跳转；其他值按没选处理
      letter: sort != SortKey.name
          ? null
          : switch (params['letter']?.toUpperCase()) {
              final value? when RegExp(r'^[A-Z#]$').hasMatch(value) => value,
              _ => null,
            },
    );
  }

  final int libraryId;
  final SortKey sort;

  /// 为空时按排序方式的默认方向
  final bool? ascending;
  final String? genre;
  final int? year;

  /// 只看已看（true）或未看（false）的；为空时不筛选
  final bool? watched;

  /// 只看已收藏的；为空时不筛选
  final bool? favorite;

  /// 只看稍后观看里的；为空时不筛选
  final bool? watchlist;
  final ProblemStatus? status;

  /// 按名称排序时的 A-Z 跳转：A-Z 或 #（非拉丁字母）
  final String? letter;

  bool get isAscending => ascending ?? sort.defaultAscending;
  bool get filtered =>
      genre != null ||
      year != null ||
      watched != null ||
      favorite != null ||
      watchlist != null ||
      status != null ||
      letter != null;

  /// 页面地址上的参数，默认值不写出来
  Map<String, String> toParams() => {
    if (sort != SortKey.added) 'sort': sort.name,
    if (ascending != null && ascending != sort.defaultAscending)
      'order': ascending! ? 'asc' : 'desc',
    'genre': ?genre,
    if (year != null) 'year': '$year',
    if (watched != null) 'watched': '$watched',
    if (favorite == true) 'favorite': 'true',
    if (watchlist == true) 'watchlist': 'true',
    if (status != null) 'status': status!.queryValue,
    if (letter != null && sort == SortKey.name) 'letter': letter!,
  };

  LibraryQuery copyWith({
    SortKey? sort,
    bool? ascending,
    (String?,)? genre,
    (int?,)? year,
    (bool?,)? watched,
    (bool?,)? favorite,
    (bool?,)? watchlist,
    (ProblemStatus?,)? status,
    (String?,)? letter,
  }) => LibraryQuery(
    libraryId: libraryId,
    sort: sort ?? this.sort,
    // 换排序方式时回到它的默认方向
    ascending: sort != null && sort != this.sort
        ? null
        : ascending ?? this.ascending,
    genre: genre == null ? this.genre : genre.$1,
    year: year == null ? this.year : year.$1,
    watched: watched == null ? this.watched : watched.$1,
    favorite: favorite == null ? this.favorite : favorite.$1,
    watchlist: watchlist == null ? this.watchlist : watchlist.$1,
    status: status == null ? this.status : status.$1,
    // 换排序方式时不再保留首字母跳转
    letter: sort != null && sort != SortKey.name
        ? null
        : letter == null
        ? this.letter
        : letter.$1,
  );

  /// 去掉全部筛选，保留排序
  LibraryQuery cleared() => copyWith(
    genre: (null,),
    year: (null,),
    watched: (null,),
    favorite: (null,),
    watchlist: (null,),
    status: (null,),
    letter: (null,),
  );

  @override
  bool operator ==(Object other) =>
      other is LibraryQuery &&
      other.libraryId == libraryId &&
      other.sort == sort &&
      other.isAscending == isAscending &&
      other.genre == genre &&
      other.year == year &&
      other.watched == watched &&
      other.favorite == favorite &&
      other.watchlist == watchlist &&
      other.status == status &&
      other.letter == letter;

  @override
  int get hashCode => Object.hash(
    libraryId,
    sort,
    isAscending,
    genre,
    year,
    watched,
    favorite,
    watchlist,
    status,
    letter,
  );
}

/// 已经取到的条目。滚到底部附近时取下一页，失败时停在原地等重试。
class CardList {
  const CardList({
    required this.items,
    required this.total,
    this.loadingMore = false,
    this.moreError,
    this.discovery,
  });

  final List<MediaCard> items;
  final int total;
  final bool loadingMore;
  final Object? moreError;
  final SearchDiscovery? discovery;

  bool get hasMore => items.length < total;
}

abstract class _CardListNotifier extends AsyncNotifier<CardList> {
  Future<PageOf<MediaCard>> fetch(Dio dio, String baseUrl, int offset);

  SearchDiscovery? get discovery => null;

  @override
  Future<CardList> build() async {
    keepProviderAlive(ref);
    final page = await fetch(
      ref.watch(apiClientProvider),
      ref.watch(apiBaseUrlProvider),
      0,
    );
    return CardList(items: page.items, total: page.total, discovery: discovery);
  }

  /// 取下一页。正在取、已经取完或上次失败（等 [retryMore]）时什么都不做。
  Future<void> loadMore() async {
    final current = state.value;
    if (current == null ||
        current.loadingMore ||
        current.moreError != null ||
        !current.hasMore) {
      return;
    }
    state = AsyncData(
      CardList(
        items: current.items,
        total: current.total,
        loadingMore: true,
        discovery: current.discovery,
      ),
    );
    try {
      final page = await fetch(
        ref.read(apiClientProvider),
        ref.read(apiBaseUrlProvider),
        current.items.length,
      );
      if (!ref.mounted) return;
      state = AsyncData(
        CardList(
          items: [...current.items, ...page.items],
          total: page.total,
          discovery: current.discovery,
        ),
      );
    } catch (error) {
      if (!ref.mounted) return;
      state = AsyncData(
        CardList(
          items: current.items,
          total: current.total,
          moreError: error,
          discovery: current.discovery,
        ),
      );
    }
  }

  Future<void> retryMore() {
    final current = state.value;
    if (current != null) {
      state = AsyncData(
        CardList(
          items: current.items,
          total: current.total,
          discovery: current.discovery,
        ),
      );
    }
    return loadMore();
  }
}

final libraryItemsProvider = AsyncNotifierProvider.autoDispose
    .family<LibraryItems, CardList, LibraryQuery>(LibraryItems.new);

class LibraryItems extends _CardListNotifier {
  LibraryItems(this.query);

  final LibraryQuery query;

  @override
  Future<PageOf<MediaCard>> fetch(Dio dio, String baseUrl, int offset) async {
    final response = await dio.get<Map<String, dynamic>>(
      '/api/libraries/${query.libraryId}/items',
      queryParameters: {
        'sort': query.sort.name,
        'order': query.isAscending ? 'asc' : 'desc',
        'genre': ?query.genre,
        'year': ?query.year,
        'watched': ?query.watched,
        'favorite': ?query.favorite,
        'watchlist': ?query.watchlist,
        'status': ?query.status?.queryValue,
        'letter': ?query.letter,
        'offset': offset,
        'limit': browsePageSize,
      },
    );
    return PageOf.fromJson(
      response.data!,
      (json) => MediaCard.fromJson(json, baseUrl),
    );
  }
}

final searchResultsProvider = AsyncNotifierProvider.autoDispose
    .family<SearchResults, CardList, String>(SearchResults.new);

/// 按片名、原名和英文名搜索。查询词为空时不发请求。
class SearchResults extends _CardListNotifier {
  SearchResults(this.query);

  final String query;
  SearchDiscovery? _discovery;

  @override
  SearchDiscovery? get discovery => _discovery;

  @override
  Future<PageOf<MediaCard>> fetch(Dio dio, String baseUrl, int offset) async {
    if (query.trim().isEmpty) return const PageOf(items: [], total: 0);
    final response = await dio.get<Map<String, dynamic>>(
      '/api/search',
      queryParameters: {
        'q': query.trim(),
        'offset': offset,
        'limit': browsePageSize,
      },
    );
    final data = response.data!;
    if (offset == 0) {
      _discovery = SearchDiscovery.fromJson(data, baseUrl);
    }
    return PageOf.fromJson(data, (json) => MediaCard.fromJson(json, baseUrl));
  }
}
