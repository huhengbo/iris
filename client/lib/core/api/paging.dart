import '../server.dart';

/// 可能很长的列表的一页：items 是当前页，total 是总数（ADR 0017）
class PageOf<T> {
  const PageOf({required this.items, required this.total});

  factory PageOf.fromJson(
    Map<String, dynamic> json,
    T Function(Map<String, dynamic>) parse,
  ) => PageOf(
    items: [
      for (final item in json['items'] as List<dynamic>)
        parse(item as Map<String, dynamic>),
    ],
    total: json['total'] as int,
  );

  final List<T> items;
  final int total;
}

/// 一张图片的各档尺寸，地址已拼成当前服务器上的完整地址。
class Artwork {
  const Artwork(this.sizes);

  factory Artwork.fromJson(Object? json, String baseUrl) => Artwork([
    for (final item in json as List<dynamic>)
      (
        width: (item as Map<String, dynamic>)['width'] as int,
        url: resolveServerUrl(baseUrl, item['url'] as String),
      ),
  ]);

  static const none = Artwork([]);

  /// 宽度从小到大
  final List<({int width, String url})> sizes;

  bool get isEmpty => sizes.isEmpty;

  /// 不小于显示宽度（物理像素）的最小一档；都不够时用最大的
  String? pick(double pixels) {
    if (sizes.isEmpty) return null;
    for (final size in sizes) {
      if (size.width >= pixels) return size.url;
    }
    return sizes.last.url;
  }
}
