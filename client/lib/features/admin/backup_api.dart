import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';

const backupsPath = '/api/admin/backups';

class BackupRecord {
  const BackupRecord({
    required this.name,
    required this.size,
    required this.createdAt,
    required this.schemaVersion,
  });

  factory BackupRecord.fromJson(Map<String, dynamic> json) => BackupRecord(
    name: json['name'] as String,
    size: json['size'] as int,
    createdAt: DateTime.parse(json['created_at'] as String).toLocal(),
    schemaVersion: json['schema_version'] as int,
  );

  final String name;
  final int size;
  final DateTime createdAt;
  final int schemaVersion;
}

class RestoreReport {
  const RestoreReport({required this.restored, required this.safetyBackup});

  factory RestoreReport.fromJson(Map<String, dynamic> json) => RestoreReport(
    restored: BackupRecord.fromJson(json['restored'] as Map<String, dynamic>),
    safetyBackup: BackupRecord.fromJson(
      json['safety_backup'] as Map<String, dynamic>,
    ),
  );

  final BackupRecord restored;
  final BackupRecord safetyBackup;
}

final backupsProvider = FutureProvider.autoDispose<List<BackupRecord>>((
  ref,
) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<List<dynamic>>(backupsPath);
  return [
    for (final item in response.data!)
      BackupRecord.fromJson(item as Map<String, dynamic>),
  ];
});

Future<BackupRecord> createBackup(Dio dio) async {
  final response = await dio.post<Map<String, dynamic>>(backupsPath);
  return BackupRecord.fromJson(response.data!);
}

Future<RestoreReport> restoreBackup(Dio dio, String name) async {
  final response = await dio.post<Map<String, dynamic>>(
    '$backupsPath/$name/restore',
    queryParameters: {'confirm': true},
  );
  return RestoreReport.fromJson(response.data!);
}

/// 不含密钥和凭据的配置（服务器、插件生效代理、资料库阈值），用来迁移或留档。
Future<Map<String, dynamic>> exportConfig(Dio dio) async {
  final response = await dio.get<Map<String, dynamic>>(
    '$backupsPath/config/export',
  );
  return response.data!;
}
