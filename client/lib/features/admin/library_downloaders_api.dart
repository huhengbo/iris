import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';

const _librariesPath = '/api/admin/libraries';

/// 下载器列表的一个候选插件，带本资料库是否勾选和插件本身的状态。
class LibraryDownloader {
  const LibraryDownloader({
    required this.id,
    required this.name,
    required this.enabled,
    required this.pluginEnabled,
    this.unavailableReason,
    required this.canMatch,
  });

  factory LibraryDownloader.fromJson(Map<String, dynamic> json) =>
      LibraryDownloader(
        id: json['id'] as String,
        name: json['name'] as String? ?? json['id'] as String,
        enabled: json['enabled'] as bool? ?? false,
        pluginEnabled: json['plugin_enabled'] as bool? ?? true,
        unavailableReason: json['unavailable_reason'] as String?,
        canMatch: json['can_match'] as bool? ?? false,
      );

  final String id;
  final String name;

  /// 这个资料库勾选了它
  final bool enabled;

  /// 插件本身在「插件」页是启用的
  final bool pluginEnabled;

  /// 启用但不能请求的原因，例如没填 Key
  final String? unavailableReason;

  /// 同时提供 metadata.search 和 metadata.details，可以放在元数据列表第一位
  final bool canMatch;

  Map<String, Object> toRequest() => {'id': id, 'enabled': enabled};

  LibraryDownloader copyWith({bool? enabled}) => LibraryDownloader(
    id: id,
    name: name,
    enabled: enabled ?? this.enabled,
    pluginEnabled: pluginEnabled,
    unavailableReason: unavailableReason,
    canMatch: canMatch,
  );
}

/// 资料库的元数据、图片和字幕下载器，以及字幕下载的选项。
class LibraryDownloaders {
  const LibraryDownloaders({
    required this.metadata,
    required this.images,
    required this.subtitles,
    required this.subtitleLanguages,
    required this.subtitleSkipIfAudio,
  });

  factory LibraryDownloaders.fromJson(Map<String, dynamic> json) =>
      LibraryDownloaders(
        metadata: _downloaders(json['metadata']),
        images: _downloaders(json['images']),
        subtitles: _downloaders(json['subtitles']),
        subtitleLanguages: [
          for (final code
              in json['subtitle_languages'] as List<dynamic>? ?? const [])
            '$code',
        ],
        subtitleSkipIfAudio: json['subtitle_skip_if_audio'] as bool? ?? false,
      );

  final List<LibraryDownloader> metadata;
  final List<LibraryDownloader> images;
  final List<LibraryDownloader> subtitles;

  /// 自动下载字幕的语言，规范码如 zh-Hans、en
  final List<String> subtitleLanguages;

  /// 音轨已经是想要的语言时不再搜索字幕
  final bool subtitleSkipIfAudio;
}

List<LibraryDownloader> _downloaders(Object? json) => [
  for (final row in json as List<dynamic>? ?? const [])
    LibraryDownloader.fromJson(row as Map<String, dynamic>),
];

final libraryDownloadersProvider = FutureProvider.autoDispose
    .family<LibraryDownloaders, int>((ref, id) async {
      final response = await ref
          .watch(apiClientProvider)
          .get<Map<String, dynamic>>('$_librariesPath/$id/downloaders');
      return LibraryDownloaders.fromJson(response.data!);
    });

/// 只提交传入的键，其余列表和字幕选项保持不变。
Future<LibraryDownloaders> saveLibraryDownloaders(
  Dio dio,
  int id, {
  List<LibraryDownloader>? metadata,
  List<LibraryDownloader>? images,
  List<LibraryDownloader>? subtitles,
  List<String>? subtitleLanguages,
  bool? subtitleSkipIfAudio,
}) async {
  final data = <String, Object>{
    'metadata': ?_requests(metadata),
    'images': ?_requests(images),
    'subtitles': ?_requests(subtitles),
    'subtitle_languages': ?subtitleLanguages,
    'subtitle_skip_if_audio': ?subtitleSkipIfAudio,
  };
  final response = await dio.put<Map<String, dynamic>>(
    '$_librariesPath/$id/downloaders',
    data: data,
  );
  return LibraryDownloaders.fromJson(response.data!);
}

List<Map<String, Object>>? _requests(List<LibraryDownloader>? items) =>
    items == null ? null : [for (final d in items) d.toRequest()];
