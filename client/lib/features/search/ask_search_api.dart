import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_error.dart';
import '../../core/providers.dart';
import '../library/browse_api.dart';

/// AI 找片：把一句描述转成影视墙已有的筛选条件，并给出结果（ADR 0030）。
/// 服务端已按当前用户的可见范围过滤。

/// 问句词：命中就认为用户在描述需求而不是输入片名。
const _questionWords = [
  '推荐',
  '有没有',
  '想看',
  '适合',
  '哪些',
  '哪部',
  '什么',
  '怎么',
  '类似',
  '像',
  '不要',
  '最近',
  '高分',
  '好看',
  '口碑',
  '没看过',
  '请',
  '吗',
  '？',
  '?',
];

/// 描述句至少这么长（字数）才按长度判断，片名很少这么长。
const _describeLength = 10;
final _cjk = RegExp(r'[一-鿿]');

/// 输入看起来像描述句，而不是片名或人名。
bool looksLikeQuestion(String query) {
  final text = query.trim();
  if (text.runes.length < 4) return false;
  if (_questionWords.any(text.contains)) return true;
  return text.runes.length >= _describeLength && _cjk.hasMatch(text);
}

/// 影视墙的筛选条件，加上一句用于语义召回的描述。没填的条件不参与筛选。
class AskFilters {
  const AskFilters({
    this.kind,
    this.genres = const [],
    this.yearFrom,
    this.yearTo,
    this.minRating,
    this.maxRuntime,
    this.watched,
    this.libraryId,
    this.semanticQuery,
  });

  factory AskFilters.fromJson(Map<String, dynamic> json) => AskFilters(
    kind: json['kind'] as String?,
    genres: [
      for (final genre in json['genres'] as List<dynamic>? ?? const [])
        genre as String,
    ],
    yearFrom: json['year_from'] as int?,
    yearTo: json['year_to'] as int?,
    minRating: (json['min_rating'] as num?)?.toDouble(),
    maxRuntime: json['max_runtime'] as int?,
    watched: json['watched'] as bool?,
    libraryId: json['library_id'] as int?,
    semanticQuery: json['semantic_query'] as String?,
  );

  /// movie 或 series
  final String? kind;

  /// 满足任意一个即可
  final List<String> genres;
  final int? yearFrom;
  final int? yearTo;
  final double? minRating;

  /// 分钟
  final int? maxRuntime;

  /// true 只要看过的，false 只要没看过的
  final bool? watched;
  final int? libraryId;

  /// 难以用条件表达的偏好，用于语义召回
  final String? semanticQuery;

  Map<String, dynamic> toJson() => {
    'kind': ?kind,
    if (genres.isNotEmpty) 'genres': genres,
    'year_from': ?yearFrom,
    'year_to': ?yearTo,
    'min_rating': ?minRating,
    'max_runtime': ?maxRuntime,
    'watched': ?watched,
    'library_id': ?libraryId,
    'semantic_query': ?semanticQuery,
  };

  bool get isEmpty => toJson().isEmpty;

  /// 逐项替换；传 null 表示保持原值，要去掉某一项用 [without]。
  AskFilters copyWith({
    String? kind,
    List<String>? genres,
    int? yearFrom,
    int? yearTo,
    double? minRating,
    int? maxRuntime,
    bool? watched,
    String? semanticQuery,
  }) => AskFilters(
    kind: kind ?? this.kind,
    genres: genres ?? this.genres,
    yearFrom: yearFrom ?? this.yearFrom,
    yearTo: yearTo ?? this.yearTo,
    minRating: minRating ?? this.minRating,
    maxRuntime: maxRuntime ?? this.maxRuntime,
    watched: watched ?? this.watched,
    libraryId: libraryId,
    semanticQuery: semanticQuery ?? this.semanticQuery,
  );

  /// 去掉一项条件。
  AskFilters without(AskCondition condition) => switch (condition) {
    AskCondition.kind => AskFilters(
      genres: genres,
      yearFrom: yearFrom,
      yearTo: yearTo,
      minRating: minRating,
      maxRuntime: maxRuntime,
      watched: watched,
      libraryId: libraryId,
      semanticQuery: semanticQuery,
    ),
    AskCondition.genres => AskFilters(
      kind: kind,
      yearFrom: yearFrom,
      yearTo: yearTo,
      minRating: minRating,
      maxRuntime: maxRuntime,
      watched: watched,
      libraryId: libraryId,
      semanticQuery: semanticQuery,
    ),
    AskCondition.years => AskFilters(
      kind: kind,
      genres: genres,
      minRating: minRating,
      maxRuntime: maxRuntime,
      watched: watched,
      libraryId: libraryId,
      semanticQuery: semanticQuery,
    ),
    AskCondition.rating => AskFilters(
      kind: kind,
      genres: genres,
      yearFrom: yearFrom,
      yearTo: yearTo,
      maxRuntime: maxRuntime,
      watched: watched,
      libraryId: libraryId,
      semanticQuery: semanticQuery,
    ),
    AskCondition.runtime => AskFilters(
      kind: kind,
      genres: genres,
      yearFrom: yearFrom,
      yearTo: yearTo,
      minRating: minRating,
      watched: watched,
      libraryId: libraryId,
      semanticQuery: semanticQuery,
    ),
    AskCondition.watched => AskFilters(
      kind: kind,
      genres: genres,
      yearFrom: yearFrom,
      yearTo: yearTo,
      minRating: minRating,
      maxRuntime: maxRuntime,
      libraryId: libraryId,
      semanticQuery: semanticQuery,
    ),
    AskCondition.library => AskFilters(
      kind: kind,
      genres: genres,
      yearFrom: yearFrom,
      yearTo: yearTo,
      minRating: minRating,
      maxRuntime: maxRuntime,
      watched: watched,
      semanticQuery: semanticQuery,
    ),
    AskCondition.semantic => AskFilters(
      kind: kind,
      genres: genres,
      yearFrom: yearFrom,
      yearTo: yearTo,
      minRating: minRating,
      maxRuntime: maxRuntime,
      watched: watched,
      libraryId: libraryId,
    ),
  };

  /// 当前生效的条件，用来显示成可编辑的胶囊。
  List<AskCondition> get active => [
    if (kind != null) AskCondition.kind,
    if (genres.isNotEmpty) AskCondition.genres,
    if (yearFrom != null || yearTo != null) AskCondition.years,
    if (minRating != null) AskCondition.rating,
    if (maxRuntime != null) AskCondition.runtime,
    if (watched != null) AskCondition.watched,
    if (libraryId != null) AskCondition.library,
    if (semanticQuery != null && semanticQuery!.trim().isNotEmpty)
      AskCondition.semantic,
  ];

  /// 胶囊上的文字，例如「喜剧 · 爱情」「2000 年后」「2 小时内」。
  String describe(AskCondition condition) => switch (condition) {
    AskCondition.kind => kind == 'series' ? '剧集' : '电影',
    AskCondition.genres => genres.join(' · '),
    AskCondition.years => switch ((yearFrom, yearTo)) {
      (final from?, final to?) when from == to => '$from 年',
      (final from?, final to?) => '$from–$to 年',
      (final from?, null) => '$from 年后',
      (null, final to?) => '$to 年前',
      _ => '',
    },
    AskCondition.rating => '评分 ${minRating!.toStringAsFixed(1)}+',
    AskCondition.runtime =>
      maxRuntime! % 60 == 0 ? '${maxRuntime! ~/ 60} 小时内' : '$maxRuntime 分钟内',
    AskCondition.watched => watched! ? '看过的' : '没看过的',
    AskCondition.library => '指定资料库',
    AskCondition.semantic => '像：${semanticQuery!.trim()}',
  };
}

enum AskCondition {
  kind,
  genres,
  years,
  rating,
  runtime,
  watched,
  library,
  semantic,
}

class AskSearchState {
  const AskSearchState({
    required this.filters,
    required this.items,
    required this.total,
    required this.semanticUsed,
    this.updating = false,
    this.updateError,
  });

  final AskFilters filters;
  final List<MediaCard> items;
  final int total;

  /// 结果是否用了语义召回（按相近程度排序）
  final bool semanticUsed;

  /// 修改条件后正在重新查询
  final bool updating;

  /// 重新查询失败的原因；上一次的结果仍然保留
  final String? updateError;

  AskSearchState copyWith({
    AskFilters? filters,
    List<MediaCard>? items,
    int? total,
    bool? semanticUsed,
    bool? updating,
    String? updateError,
  }) => AskSearchState(
    filters: filters ?? this.filters,
    items: items ?? this.items,
    total: total ?? this.total,
    semanticUsed: semanticUsed ?? this.semanticUsed,
    updating: updating ?? false,
    updateError: updateError,
  );
}

/// 搜索页 AI 区块的数据。参数是搜索词，进入时请求模型理解问句；
/// 之后用户改筛选条件只重新查询，不再请求模型。
///
/// 不自动重试：每次重试都是一次模型调用，失败了直接显示「AI 暂不可用」。
final askSearchProvider = AsyncNotifierProvider.autoDispose
    .family<AskSearchNotifier, AskSearchState, String>(
      AskSearchNotifier.new,
      retry: (_, _) => null,
    );

class AskSearchNotifier extends AsyncNotifier<AskSearchState> {
  AskSearchNotifier(this.query);

  final String query;

  static const pageSize = 12;

  @override
  Future<AskSearchState> build() async {
    final baseUrl = ref.watch(apiBaseUrlProvider);
    final response = await ref
        .watch(apiClientProvider)
        .post<Map<String, dynamic>>(
          '/api/ai/ask-search',
          data: {'query': query, 'limit': pageSize},
        );
    final data = response.data!;
    return AskSearchState(
      filters: AskFilters.fromJson(data['understood'] as Map<String, dynamic>),
      items: _items(data, baseUrl),
      total: data['total'] as int,
      semanticUsed: data['semantic_used'] as bool,
    );
  }

  /// 用修改后的条件重新查询；失败时保留上一次的结果并记下原因。
  Future<void> refine(AskFilters filters) async {
    final current = state.value;
    if (current == null) return;
    final baseUrl = ref.read(apiBaseUrlProvider);
    state = AsyncData(current.copyWith(filters: filters, updating: true));
    try {
      final response = await ref
          .read(apiClientProvider)
          .post<Map<String, dynamic>>(
            '/api/ai/ask-search/query',
            data: {'filters': filters.toJson(), 'limit': pageSize},
          );
      final data = response.data!;
      state = AsyncData(
        AskSearchState(
          filters: filters,
          items: _items(data, baseUrl),
          total: data['total'] as int,
          semanticUsed: data['semantic_used'] as bool,
        ),
      );
    } on DioException catch (e) {
      state = AsyncData(
        current.copyWith(updateError: ApiError.from(e).message),
      );
    }
  }

  List<MediaCard> _items(Map<String, dynamic> data, String baseUrl) => [
    for (final item in data['items'] as List<dynamic>)
      MediaCard.fromJson(item as Map<String, dynamic>, baseUrl),
  ];
}
