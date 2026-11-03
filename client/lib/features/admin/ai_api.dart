import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';

// 管理后台「AI 助手」页的接口：模型提供方、模型用途、匹配建议、语义索引和用量。
// 字段与 docs/api/openapi.json 对齐，test/openapi_contract_test.dart 校验。

const aiBasePath = '/api/admin/ai';

/// 用量汇总默认查看的月数
const aiUsageMonths = 6;

/// 三种模型用途，顺序即页面显示顺序。
const aiRoles = ['chat', 'task', 'embedding'];

String aiRoleLabel(String role) => switch (role) {
  'chat' => '对话',
  'task' => '后台任务',
  'embedding' => '向量',
  _ => role,
};

/// 每种用途一句说明，显示在用途行下面。
String aiRoleHint(String role) => switch (role) {
  'chat' => 'AI 找片和对话',
  'task' => '为待处理作品生成匹配建议',
  'embedding' => '语义索引，可不配',
  _ => '',
};

String aiKindLabel(String kind) => switch (kind) {
  'openai' => 'OpenAI 兼容',
  'anthropic' => 'Anthropic',
  _ => kind,
};

/// 测试连接失败的原因分类（`error_kind`）对应的人话。
String aiErrorKindLabel(String? kind) => switch (kind) {
  'auth' => 'Key 无效或没有权限',
  'rate_limited' => '被对方限流',
  'timeout' => '连接超时',
  'unreachable' => '连不上这个地址',
  'bad_request' => '请求被拒绝，模型名或地址可能不对',
  'server' => '对方服务出错',
  'unsupported' => '对方不支持这个操作',
  'not_configured' => '还没有配置',
  _ => '原因未知',
};

DateTime _time(Object? value) => DateTime.parse(value as String).toLocal();

/// 新建提供方时可选的预设（地址、类型和预填的模型名）。
class AiPreset {
  const AiPreset({
    required this.id,
    required this.name,
    required this.kind,
    required this.baseUrl,
    required this.chatModel,
    required this.embeddingModel,
  });

  factory AiPreset.fromJson(Map<String, dynamic> json) => AiPreset(
    id: json['id'] as String,
    name: json['name'] as String,
    kind: json['kind'] as String,
    baseUrl: json['base_url'] as String,
    chatModel: json['chat_model'] as String,
    embeddingModel: json['embedding_model'] as String,
  );

  final String id;
  final String name;
  final String kind;
  final String baseUrl;

  /// 没有预填时为空字符串
  final String chatModel;
  final String embeddingModel;

  /// 用途对应的预填模型名：对话和后台任务用对话模型，向量用向量模型。
  String modelFor(String role) =>
      role == 'embedding' ? embeddingModel : chatModel;
}

/// 提供方最近一次测试连接的结果。
class AiLastTest {
  const AiLastTest({
    required this.ok,
    required this.checkedAt,
    required this.message,
  });

  factory AiLastTest.fromJson(Map<String, dynamic> json) => AiLastTest(
    ok: json['ok'] as bool,
    checkedAt: _time(json['checked_at']),
    message: json['message'] as String?,
  );

  final bool ok;
  final DateTime checkedAt;
  final String? message;
}

class AiProvider {
  const AiProvider({
    required this.id,
    required this.name,
    required this.kind,
    required this.baseUrl,
    required this.keySet,
    required this.lastTest,
    required this.usedBy,
  });

  factory AiProvider.fromJson(Map<String, dynamic> json) => AiProvider(
    id: json['id'] as int,
    name: json['name'] as String,
    kind: json['kind'] as String,
    baseUrl: json['base_url'] as String,
    keySet: json['key_set'] as bool,
    lastTest: json['last_test'] == null
        ? null
        : AiLastTest.fromJson(json['last_test'] as Map<String, dynamic>),
    usedBy: [
      for (final role in json['used_by'] as List<dynamic>) role as String,
    ],
  );

  final int id;
  final String name;

  /// openai 或 anthropic
  final String kind;
  final String baseUrl;

  /// Key 只返回是否已设置，不回显
  final bool keySet;
  final AiLastTest? lastTest;

  /// 正在用这个提供方的用途（chat、task、embedding）
  final List<String> usedBy;
}

/// 一次测试（提供方或用途）的结果。
class AiTestResult {
  const AiTestResult({
    required this.ok,
    required this.checkedAt,
    required this.errorKind,
    required this.message,
    required this.durationMs,
  });

  factory AiTestResult.fromJson(Map<String, dynamic> json) => AiTestResult(
    ok: json['ok'] as bool,
    checkedAt: _time(json['checked_at']),
    errorKind: json['error_kind'] as String?,
    message: json['message'] as String?,
    durationMs: json['duration_ms'] as int,
  );

  final bool ok;
  final DateTime checkedAt;
  final String? errorKind;
  final String? message;
  final int durationMs;

  /// 失败原因：分类的人话，服务端有详情时附在后面
  String get reason {
    final detail = message;
    final kind = aiErrorKindLabel(errorKind);
    return detail == null || detail.isEmpty ? kind : '$kind（$detail）';
  }
}

/// 一个用途选的「提供方 + 模型名」。
class AiRoleBinding {
  const AiRoleBinding({required this.providerId, required this.model});

  factory AiRoleBinding.fromJson(Map<String, dynamic> json) => AiRoleBinding(
    providerId: json['provider_id'] as int,
    model: json['model'] as String,
  );

  final int providerId;
  final String model;

  Map<String, dynamic> toJson() => {'provider_id': providerId, 'model': model};

  @override
  bool operator ==(Object other) =>
      other is AiRoleBinding &&
      other.providerId == providerId &&
      other.model == model;

  @override
  int get hashCode => Object.hash(providerId, model);
}

class AiRoles {
  const AiRoles({this.chat, this.task, this.embedding});

  factory AiRoles.fromJson(Map<String, dynamic> json) {
    AiRoleBinding? binding(String key) => json[key] == null
        ? null
        : AiRoleBinding.fromJson(json[key] as Map<String, dynamic>);
    return AiRoles(
      chat: binding('chat'),
      task: binding('task'),
      embedding: binding('embedding'),
    );
  }

  final AiRoleBinding? chat;
  final AiRoleBinding? task;
  final AiRoleBinding? embedding;

  AiRoleBinding? of(String role) => switch (role) {
    'chat' => chat,
    'task' => task,
    'embedding' => embedding,
    _ => null,
  };
}

/// 匹配建议的开关与阈值。
class AiSettings {
  const AiSettings({
    required this.reversibleWrite,
    required this.suggestMatches,
    required this.autoMatch,
    required this.autoMatchThreshold,
  });

  factory AiSettings.fromJson(Map<String, dynamic> json) => AiSettings(
    reversibleWrite: json['reversible_write'] as String,
    suggestMatches: json['suggest_matches'] as bool,
    autoMatch: json['auto_match'] as bool,
    autoMatchThreshold: (json['auto_match_threshold'] as num).toDouble(),
  );

  /// 本期没有工具用到，页面不展示
  final String reversibleWrite;
  final bool suggestMatches;
  final bool autoMatch;
  final double autoMatchThreshold;
}

/// 语义索引状态：off、unavailable、building、ready、error。
class AiSemanticStatus {
  const AiSemanticStatus({
    required this.state,
    required this.model,
    required this.total,
    required this.indexed,
    required this.error,
  });

  factory AiSemanticStatus.fromJson(Map<String, dynamic> json) =>
      AiSemanticStatus(
        state: json['state'] as String,
        model: json['model'] as String?,
        total: json['total'] as int,
        indexed: json['indexed'] as int,
        error: json['error'] as String?,
      );

  final String state;
  final String? model;
  final int total;
  final int indexed;
  final String? error;

  bool get building => state == 'building';

  /// 能不能重建：没有向量模型或 sqlite-vec 不可用时没有意义
  bool get canRebuild =>
      state == 'building' || state == 'ready' || state == 'error';
}

/// 某月某用途的用量汇总。
class AiUsageRow {
  const AiUsageRow({
    required this.month,
    required this.role,
    required this.calls,
    required this.failures,
    required this.inputTokens,
    required this.outputTokens,
  });

  factory AiUsageRow.fromJson(Map<String, dynamic> json) => AiUsageRow(
    month: json['month'] as String,
    role: json['role'] as String,
    calls: json['calls'] as int,
    failures: json['failures'] as int,
    inputTokens: json['input_tokens'] as int,
    outputTokens: json['output_tokens'] as int,
  );

  /// 形如 2026-11
  final String month;
  final String role;
  final int calls;
  final int failures;
  final int inputTokens;
  final int outputTokens;
}

// ───────────────────────── 读取 ─────────────────────────

final aiPresetsProvider = FutureProvider.autoDispose<List<AiPreset>>((
  ref,
) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<Map<String, dynamic>>('$aiBasePath/presets');
  return [
    for (final row in response.data!['presets'] as List<dynamic>)
      AiPreset.fromJson(row as Map<String, dynamic>),
  ];
});

final aiProvidersProvider = FutureProvider.autoDispose<List<AiProvider>>((
  ref,
) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<Map<String, dynamic>>('$aiBasePath/providers');
  return [
    for (final row in response.data!['providers'] as List<dynamic>)
      AiProvider.fromJson(row as Map<String, dynamic>),
  ];
});

final aiRolesProvider = FutureProvider.autoDispose<AiRoles>((ref) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<Map<String, dynamic>>('$aiBasePath/roles');
  return AiRoles.fromJson(response.data!);
});

final aiSettingsProvider = FutureProvider.autoDispose<AiSettings>((ref) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<Map<String, dynamic>>('$aiBasePath/settings');
  return AiSettings.fromJson(response.data!);
});

final aiSemanticProvider = FutureProvider.autoDispose<AiSemanticStatus>((
  ref,
) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<Map<String, dynamic>>('$aiBasePath/semantic');
  return AiSemanticStatus.fromJson(response.data!);
});

final aiUsageProvider = FutureProvider.autoDispose<List<AiUsageRow>>((
  ref,
) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<Map<String, dynamic>>(
        '$aiBasePath/usage',
        queryParameters: {'months': aiUsageMonths},
      );
  return [
    for (final row in response.data!['rows'] as List<dynamic>)
      AiUsageRow.fromJson(row as Map<String, dynamic>),
  ];
});

// ───────────────────────── 写入 ─────────────────────────

/// 新增提供方。[apiKey] 为空表示没有 Key（本地 Ollama）。
Future<void> createAiProvider(
  Dio dio, {
  required String name,
  required String kind,
  required String baseUrl,
  String? apiKey,
}) => dio.post<void>(
  '$aiBasePath/providers',
  data: {
    'name': name,
    'kind': kind,
    'base_url': baseUrl,
    if (apiKey != null && apiKey.isNotEmpty) 'api_key': apiKey,
  },
);

/// 修改提供方。[apiKey] 为 null 且 [clearKey] 为 false 时请求体不带 api_key，
/// 服务端保持原 Key；[clearKey] 为 true 时传 `api_key: null` 清除。
Future<void> updateAiProvider(
  Dio dio,
  int id, {
  required String name,
  required String kind,
  required String baseUrl,
  String? apiKey,
  bool clearKey = false,
}) => dio.put<void>(
  '$aiBasePath/providers/$id',
  data: {
    'name': name,
    'kind': kind,
    'base_url': baseUrl,
    if (clearKey)
      'api_key': null
    else if (apiKey != null && apiKey.isNotEmpty)
      'api_key': apiKey,
  },
);

Future<void> deleteAiProvider(Dio dio, int id) =>
    dio.delete<void>('$aiBasePath/providers/$id');

Future<AiTestResult> testAiProvider(Dio dio, int id) async {
  final response = await dio.post<Map<String, dynamic>>(
    '$aiBasePath/providers/$id/test',
  );
  return AiTestResult.fromJson(response.data!);
}

Future<AiTestResult> testAiRole(Dio dio, String role) async {
  final response = await dio.post<Map<String, dynamic>>(
    '$aiBasePath/roles/$role/test',
  );
  return AiTestResult.fromJson(response.data!);
}

/// 保存用途。只传 [changes] 里的用途：值为 null 表示清除，没列出的保持原值。
Future<void> saveAiRoles(Dio dio, Map<String, AiRoleBinding?> changes) =>
    dio.put<void>(
      '$aiBasePath/roles',
      data: {
        for (final MapEntry(:key, :value) in changes.entries)
          key: value?.toJson(),
      },
    );

/// 保存匹配建议设置，没传的项服务端保持原值。
Future<AiSettings> saveAiSettings(
  Dio dio, {
  bool? suggestMatches,
  bool? autoMatch,
  double? autoMatchThreshold,
}) async {
  final response = await dio.put<Map<String, dynamic>>(
    '$aiBasePath/settings',
    data: {
      'suggest_matches': ?suggestMatches,
      'auto_match': ?autoMatch,
      'auto_match_threshold': ?autoMatchThreshold,
    },
  );
  return AiSettings.fromJson(response.data!);
}

Future<void> rebuildAiSemantic(Dio dio) =>
    dio.post<void>('$aiBasePath/semantic/rebuild');
