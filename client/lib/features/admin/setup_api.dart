import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';

class SetupStep {
  const SetupStep({
    required this.key,
    required this.title,
    required this.state,
    required this.message,
    this.actionPath,
  });

  factory SetupStep.fromJson(Map<String, dynamic> json) => SetupStep(
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

  bool get complete => state == 'complete';

  /// 做过但出了问题；message 即失败原因。
  bool get failed => state == 'failed';
}

class SetupStatus {
  const SetupStatus({
    required this.complete,
    required this.currentKey,
    required this.steps,
  });

  factory SetupStatus.fromJson(Map<String, dynamic> json) => SetupStatus(
    complete: json['complete'] as bool,
    currentKey: json['current_key'] as String?,
    steps: [
      for (final item in json['steps'] as List<dynamic>)
        SetupStep.fromJson(item as Map<String, dynamic>),
    ],
  );

  final bool complete;
  final String? currentKey;
  final List<SetupStep> steps;
}

final setupStatusProvider = FutureProvider.autoDispose<SetupStatus>((
  ref,
) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<Map<String, dynamic>>('/api/admin/setup');
  return SetupStatus.fromJson(response.data!);
});
