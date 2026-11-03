import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import '../../core/api/paging.dart';

export '../../core/api/paging.dart';
export '../../core/format.dart' show formatSize;

part 'catalog_models.dart';

const _admin = '/api/admin';

/// 管理列表每页的条数，和服务端默认值一致
const adminPageSize = 50;

final pendingSummaryProvider = FutureProvider.autoDispose<PendingSummary>((
  ref,
) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<Map<String, dynamic>>('$_admin/pending');
  return PendingSummary.fromJson(response.data!);
});

/// 参数是分页偏移
final unmatchedWorksProvider = FutureProvider.autoDispose
    .family<PageOf<PendingWork>, int>((ref, offset) async {
      final response = await ref
          .watch(apiClientProvider)
          .get<Map<String, dynamic>>(
            '$_admin/works',
            queryParameters: {
              'state': 'unmatched',
              'offset': offset,
              'limit': adminPageSize,
            },
          );
      return PageOf.fromJson(response.data!, PendingWork.fromJson);
    });

/// 是否会为未匹配的作品自动生成 AI 建议：配置了后台任务模型，并且没有关掉。
/// 用来决定没有建议的行显示「AI 分析中」还是什么都不显示。
final aiAnalysisEnabledProvider = FutureProvider.autoDispose<bool>((ref) async {
  final dio = ref.watch(apiClientProvider);
  try {
    final roles = await dio.get<Map<String, dynamic>>('$_admin/ai/roles');
    if (roles.data!['task'] == null) return false;
    final settings = await dio.get<Map<String, dynamic>>('$_admin/ai/settings');
    return settings.data!['suggest_matches'] as bool;
  } on DioException {
    return false;
  }
});

/// status 是 unplaced（没有归入条目）或 ignored（附加内容）；reason 为空时不筛选
final pendingFilesProvider = FutureProvider.autoDispose
    .family<PendingFilePage, ({String status, String? reason, int offset})>((
      ref,
      args,
    ) async {
      final response = await ref
          .watch(apiClientProvider)
          .get<Map<String, dynamic>>(
            '$_admin/files',
            queryParameters: {
              'status': args.status,
              'reason': ?args.reason,
              'offset': args.offset,
              'limit': adminPageSize,
            },
          );
      return PendingFilePage.fromJson(response.data!);
    });

final adminItemsProvider = FutureProvider.autoDispose
    .family<PageOf<AdminItem>, ({String query, int offset})>((ref, args) async {
      final baseUrl = ref.watch(apiBaseUrlProvider);
      final response = await ref
          .watch(apiClientProvider)
          .get<Map<String, dynamic>>(
            '$_admin/items',
            queryParameters: {
              if (args.query.isNotEmpty) 'query': args.query,
              'offset': args.offset,
              'limit': adminPageSize,
            },
          );
      return PageOf.fromJson(
        response.data!,
        (json) => AdminItem.fromJson(json, baseUrl),
      );
    });

final adminItemProvider = FutureProvider.autoDispose
    .family<AdminItemDetail, int>((ref, id) async {
      final baseUrl = ref.watch(apiBaseUrlProvider);
      final response = await ref
          .watch(apiClientProvider)
          .get<Map<String, dynamic>>('$_admin/items/$id');
      return AdminItemDetail.fromJson(response.data!, baseUrl);
    });

final itemMetadataProvider = FutureProvider.autoDispose
    .family<List<MetadataField>, int>((ref, id) async {
      final response = await ref
          .watch(apiClientProvider)
          .get<List<dynamic>>('$_admin/items/$id/metadata');
      return [
        for (final field in response.data!)
          MetadataField.fromJson(field as Map<String, dynamic>),
      ];
    });

Future<void> updateItemMetadata(
  Dio dio,
  int itemId,
  String field, {
  required String? value,
  required bool locked,
}) async {
  await dio.patch<void>(
    '$_admin/items/$itemId/metadata/$field',
    data: {'value': value, 'locked': locked},
  );
}

/// 解除锁定，恢复自动来源：下一次刷新或重新匹配会覆盖这一项。
Future<void> unlockItemMetadata(Dio dio, int itemId, String field) async {
  await dio.delete<void>('$_admin/items/$itemId/metadata/$field');
}

/// 用资料库选择的元数据插件搜索。kind 是资料库类型 movie / series。
Future<List<MetadataCandidate>> searchMetadata(
  Dio dio,
  String kind,
  String query, {
  int? libraryId,
}) async {
  final response = await dio.get<List<dynamic>>(
    '$_admin/metadata/search',
    queryParameters: {'kind': kind, 'query': query, 'library_id': ?libraryId},
  );
  return [
    for (final item in response.data!)
      MetadataCandidate.fromJson(item as Map<String, dynamic>),
  ];
}

/// 手动指定作品对应的元数据条目，返回条目 ID。不传 providerId 时用资料库选择的插件。
Future<int> assignWork(
  Dio dio,
  int workId,
  String remoteId, {
  String? providerId,
}) async {
  final response = await dio.post<Map<String, dynamic>>(
    '$_admin/works/$workId/assign',
    data: {'provider_id': ?providerId, 'remote_id': remoteId},
  );
  return response.data!['id'] as int;
}

Future<BatchAssignmentReport> assignWorksBatch(
  Dio dio,
  List<int> workIds,
  String remoteId, {
  String? providerId,
}) async {
  final response = await dio.post<Map<String, dynamic>>(
    '$_admin/works/batch-assign',
    data: {
      'work_ids': workIds,
      'provider_id': ?providerId,
      'remote_id': remoteId,
    },
  );
  return BatchAssignmentReport.fromJson(response.data!);
}

/// 采用 AI 的匹配建议（等同于手动指定），返回条目 ID。
Future<int> adoptSuggestion(Dio dio, int workId) async {
  final response = await dio.post<Map<String, dynamic>>(
    '$_admin/works/$workId/suggestion/adopt',
  );
  return response.data!['id'] as int;
}

Future<void> ignoreSuggestion(Dio dio, int workId) =>
    dio.post<void>('$_admin/works/$workId/suggestion/ignore');

/// 丢弃现有建议，稍后由后台任务重新分析。
Future<void> reanalyzeSuggestion(Dio dio, int workId) =>
    dio.post<void>('$_admin/works/$workId/suggestion/reanalyze');

/// 撤销 AI 自动匹配：作品回到待处理，且不再自动匹配同一个候选。
Future<void> revertSuggestion(Dio dio, int workId) =>
    dio.post<void>('$_admin/works/$workId/suggestion/revert');

/// 批量采用 AI 建议，每个作品用自己的建议。
Future<BatchAssignmentReport> adoptSuggestionsBatch(
  Dio dio,
  List<int> workIds,
) async {
  final response = await dio.post<Map<String, dynamic>>(
    '$_admin/works/batch-adopt-suggestions',
    data: {'work_ids': workIds},
  );
  return BatchAssignmentReport.fromJson(response.data!);
}

Future<BatchAssignmentReport> rematchWorksBatch(
  Dio dio,
  List<int> workIds,
) async {
  final response = await dio.post<Map<String, dynamic>>(
    '$_admin/works/batch-rematch',
    data: {'work_ids': workIds},
  );
  return BatchAssignmentReport.fromJson(response.data!);
}

/// 把视频挂到电影或剧集的某一集，返回条目 ID。剧集必须给出季号和集号。
Future<int> assignFile(
  Dio dio,
  int mediaFileId,
  String remoteId, {
  String? providerId,
  int? season,
  int? episode,
}) async {
  final response = await dio.post<Map<String, dynamic>>(
    '$_admin/files/$mediaFileId/assign',
    data: {
      'provider_id': ?providerId,
      'remote_id': remoteId,
      'season': ?season,
      'episode': ?episode,
    },
  );
  return response.data!['id'] as int;
}

Future<BatchAssignmentReport> assignFilesBatch(
  Dio dio,
  List<int> mediaFileIds,
  String remoteId, {
  String? providerId,
  int? season,
  int? episode,
}) async {
  final response = await dio.post<Map<String, dynamic>>(
    '$_admin/files/batch-assign',
    data: {
      'media_file_ids': mediaFileIds,
      'provider_id': ?providerId,
      'remote_id': remoteId,
      'season': ?season,
      'episode': ?episode,
    },
  );
  return BatchAssignmentReport.fromJson(response.data!);
}

Future<BatchAssignmentReport> ignoreFilesBatch(
  Dio dio,
  List<int> mediaFileIds,
) async {
  final response = await dio.post<Map<String, dynamic>>(
    '$_admin/files/batch-ignore',
    data: {'media_file_ids': mediaFileIds},
  );
  return BatchAssignmentReport.fromJson(response.data!);
}

Future<BatchAssignmentReport> restoreFilesBatch(
  Dio dio,
  List<int> mediaFileIds,
) async {
  final response = await dio.post<Map<String, dynamic>>(
    '$_admin/files/batch-restore',
    data: {'media_file_ids': mediaFileIds},
  );
  return BatchAssignmentReport.fromJson(response.data!);
}

Future<void> restoreFile(Dio dio, int mediaFileId) =>
    dio.post<void>('$_admin/files/$mediaFileId/restore');

Future<void> refreshItem(Dio dio, int id) =>
    dio.post<void>('$_admin/items/$id/refresh');

Future<void> patchSeasonMetadata(
  Dio dio,
  int id,
  Map<String, dynamic> values,
) => dio.patch<void>('$_admin/seasons/$id/metadata', data: {'values': values});

Future<void> patchEpisodeMetadata(
  Dio dio,
  int id,
  Map<String, dynamic> values,
) => dio.patch<void>('$_admin/episodes/$id/metadata', data: {'values': values});

Future<void> refreshSeason(Dio dio, int id) =>
    dio.post<void>('$_admin/seasons/$id/refresh');

Future<void> refreshEpisode(Dio dio, int id) =>
    dio.post<void>('$_admin/episodes/$id/refresh');

Future<void> deleteSeason(Dio dio, int id) =>
    dio.delete<void>('$_admin/seasons/$id', queryParameters: {'confirm': true});

/// 删除单集，返回后台删除任务的编号（服务端先清理来源上的文件再从资料库移除）。
Future<int?> deleteEpisode(Dio dio, int id) async {
  final response = await dio.delete<void>(
    '$_admin/episodes/$id',
    queryParameters: {'confirm': true},
  );
  final value = response.headers.value('X-Task-Id');
  return value == null ? null : int.tryParse(value);
}

Future<Map<String, dynamic>> itemInfo(Dio dio, int id) async {
  final response = await dio.get<Map<String, dynamic>>(
    '$_admin/items/$id/info',
  );
  return response.data!;
}

Future<void> unmatchItem(Dio dio, int id) =>
    dio.post<void>('$_admin/items/$id/unmatch');

Future<Map<String, dynamic>> batchItems(
  Dio dio,
  List<int> ids,
  String action, {
  Map<String, dynamic> payload = const {},
  Map<String, dynamic>? filter,
}) async {
  final response = await dio.post<Map<String, dynamic>>(
    '$_admin/items/batch',
    data: {
      'ids': ids,
      ...?filter == null ? null : {'filter': filter},
      'action': action,
      'payload': payload,
    },
  );
  return response.data!;
}

/// 不传 remoteId 时清掉匹配结果重新自动匹配。返回新的条目 ID；没有匹配上时为 null。
Future<int?> rematchItem(
  Dio dio,
  int id, {
  String? providerId,
  String? remoteId,
}) async {
  final response = await dio.post<Map<String, dynamic>>(
    '$_admin/items/$id/rematch',
    data: {'provider_id': ?providerId, 'remote_id': ?remoteId},
  );
  return response.data!['item_id'] as int?;
}

/// 到来源重新读取条目下文件的信息，返回更新和失效的文件数。
Future<({int updated, int missing})> reenrichItem(Dio dio, int id) async {
  final response = await dio.post<Map<String, dynamic>>(
    '$_admin/items/$id/reenrich',
  );
  final data = response.data!;
  return (updated: data['updated'] as int, missing: data['missing'] as int);
}

/// 在后台定向同步条目所在的目录：找出新增、改名和删除的文件，进度在同步页查看。
Future<void> syncItemDirs(Dio dio, int id) =>
    dio.post<void>('$_admin/items/$id/sync');

Future<int?> deleteItem(Dio dio, int id, {String? idempotencyKey}) async {
  final response = await dio.delete<void>(
    '$_admin/items/$id',
    queryParameters: {'confirm': true},
    options: Options(
      headers: {
        'Idempotency-Key':
            idempotencyKey ??
            'delete-$id-${DateTime.now().microsecondsSinceEpoch}',
      },
    ),
  );
  final value = response.headers.value('X-Task-Id');
  return value == null ? null : int.tryParse(value);
}

/// 删除待处理的作品和视频，连同来源上的文件；返回后台任务编号。
Future<int?> deletePending(
  Dio dio, {
  List<int> workIds = const [],
  List<int> mediaFileIds = const [],
}) async {
  final response = await dio.post<void>(
    '$_admin/pending/delete',
    queryParameters: {'confirm': true},
    data: {'work_ids': workIds, 'media_file_ids': mediaFileIds},
    options: Options(
      headers: {
        'Idempotency-Key':
            'delete-pending-${DateTime.now().microsecondsSinceEpoch}',
      },
    ),
  );
  final value = response.headers.value('X-Task-Id');
  return value == null ? null : int.tryParse(value);
}

Future<void> detachVersion(Dio dio, int versionId) =>
    dio.post<void>('$_admin/versions/$versionId/detach');

/// 条目某类图片（海报、背景等）的候选地址。
Future<List<String>> artworkCandidates(Dio dio, int id, String kind) async {
  final response = await dio.get<Map<String, dynamic>>(
    '$_admin/items/$id/artwork/$kind',
  );
  return [
    for (final value
        in response.data?['candidates'] as List<dynamic>? ?? const [])
      value as String,
  ];
}

Future<void> selectArtwork(
  Dio dio,
  int id,
  String kind, {
  required String source,
  required String value,
}) => dio.put<void>(
  '$_admin/items/$id/artwork/$kind',
  data: {'source': source, 'value': value},
);

/// 资料库里的合集列表，只取加入合集时需要展示的原始字段。
Future<List<Map<String, dynamic>>> libraryCollectionChoices(
  Dio dio,
  int libraryId,
) async {
  final response = await dio.get<List<dynamic>>(
    '/api/libraries/$libraryId/collections',
  );
  return [
    for (final raw in response.data ?? const [])
      if (raw case final Map<String, dynamic> collection) collection,
  ];
}

Future<void> addItemToCollection(Dio dio, int collectionId, int itemId) =>
    dio.post<void>(
      '$_admin/collections/$collectionId/items',
      data: {'item_id': itemId},
    );
