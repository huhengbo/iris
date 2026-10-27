import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/provider_cache.dart';
import '../../core/providers.dart';

class HealthCheck {
  const HealthCheck({
    required this.key,
    required this.title,
    required this.state,
    required this.message,
    this.actionPath,
  });

  factory HealthCheck.fromJson(Map<String, dynamic> json) => HealthCheck(
    key: json['key'] as String,
    title: json['title'] as String,
    state: json['state'] as String,
    message: json['message'] as String,
    actionPath: json['action_path'] as String?,
  );

  final String key;
  final String title;
  final String state;
  final String message;
  final String? actionPath;
}

class HealthReport {
  const HealthReport({
    required this.checkedAt,
    required this.schemaVersion,
    required this.checks,
  });

  factory HealthReport.fromJson(Map<String, dynamic> json) => HealthReport(
    checkedAt: DateTime.parse(json['checked_at'] as String).toLocal(),
    schemaVersion: json['schema_version'] as int,
    checks: [
      for (final item in json['checks'] as List<dynamic>)
        HealthCheck.fromJson(item as Map<String, dynamic>),
    ],
  );

  final DateTime checkedAt;
  final int schemaVersion;
  final List<HealthCheck> checks;
}

final healthReportProvider = FutureProvider.autoDispose<HealthReport>((
  ref,
) async {
  keepProviderAlive(ref);
  final response = await ref
      .watch(apiClientProvider)
      .get<Map<String, dynamic>>('/api/admin/health');
  return HealthReport.fromJson(response.data!);
});
