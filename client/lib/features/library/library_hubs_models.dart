part of 'browse_api.dart';

/// 资料库「推荐」标签的一行（Plex 的 Hub）。继续观看行用 [recent]，其余用 [items]。
class HubRow {
  const HubRow({
    required this.kind,
    this.arg,
    required this.title,
    this.items = const [],
    this.recent = const [],
  });

  factory HubRow.fromJson(Map<String, dynamic> json, String baseUrl) => HubRow(
    kind: json['kind'] as String,
    arg: json['arg'] as String?,
    title: json['title'] as String,
    items: [
      for (final item in json['items'] as List<dynamic>? ?? const [])
        MediaCard.fromJson(item as Map<String, dynamic>, baseUrl),
    ],
    recent: [
      for (final item in json['recent'] as List<dynamic>? ?? const [])
        RecentPlay.fromJson(item as Map<String, dynamic>, baseUrl),
    ],
  );

  final String kind;
  final String? arg;
  final String title;
  final List<MediaCard> items;

  /// 继续观看行的横版剧照卡；其他行为空
  final List<RecentPlay> recent;

  bool get isEmpty => items.isEmpty && recent.isEmpty;
}

/// 首页上某个资料库的一个推荐行。
class HubShelf {
  const HubShelf({required this.library, required this.hub});

  factory HubShelf.fromJson(Map<String, dynamic> json, String baseUrl) =>
      HubShelf(
        library: LibrarySummary.fromJson(
          json['library'] as Map<String, dynamic>,
          baseUrl,
        ),
        hub: HubRow.fromJson(json['hub'] as Map<String, dynamic>, baseUrl),
      );

  final LibrarySummary library;
  final HubRow hub;
}

/// 「分类」标签的一张类型卡片。
class CategoryCard {
  const CategoryCard({
    required this.name,
    required this.itemCount,
    this.image = Artwork.none,
  });

  factory CategoryCard.fromJson(Map<String, dynamic> json, String baseUrl) =>
      CategoryCard(
        name: json['name'] as String,
        itemCount: json['item_count'] as int,
        image: Artwork.fromJson(json['image'], baseUrl),
      );

  final String name;
  final int itemCount;

  /// 代表图：有背景图时用背景图，否则是海报
  final Artwork image;
}
