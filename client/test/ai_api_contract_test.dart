import 'dart:collection';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/features/admin/ai_api.dart';

// AI 助手页的契约断言：读取 docs/api/openapi.json，校验客户端解析的响应模型
// 只依赖快照里声明、并且一定会返回的字段，发出的请求体只带快照里声明的字段。
// 写法同 openapi_contract_test.dart。

final Map<String, dynamic> _spec = jsonDecode(
  File('../docs/api/openapi.json').readAsStringSync(),
) as Map<String, dynamic>;

Map<String, dynamic> _schema(String name) =>
    (_spec['components']['schemas'] as Map<String, dynamic>)[name]
        as Map<String, dynamic>;

Map<String, dynamic> _properties(String name) =>
    _schema(name)['properties'] as Map<String, dynamic>;

Set<String> _required(String name) =>
    ((_schema(name)['required'] as List<dynamic>?) ?? const [])
        .cast<String>()
        .toSet();

bool _nullable(Map<String, dynamic> property) =>
    (property['anyOf'] as List<dynamic>? ?? const []).any(
      (o) => (o as Map<String, dynamic>)['type'] == 'null',
    );

/// 记录被读取的键，用来知道客户端解析某个模型实际依赖哪些字段。
class _Recording extends MapBase<String, dynamic> {
  _Recording(this._inner);

  final Map<String, dynamic> _inner;
  final Set<String> reads = {};

  @override
  dynamic operator [](Object? key) {
    reads.add('$key');
    return _inner[key];
  }

  @override
  void operator []=(String key, dynamic value) => _inner[key] = value;

  @override
  void clear() => _inner.clear();

  @override
  Iterable<String> get keys => _inner.keys;

  @override
  dynamic remove(Object? key) => _inner.remove(key);
}

Object? _sample(String name, Map<String, dynamic> property) {
  if (_nullable(property)) return null;
  if (property['enum'] case final List<dynamic> values) return values.first;
  return switch (property['type']) {
    'integer' => 1,
    'number' => 1.0,
    'boolean' => false,
    'string' => name.endsWith('_at') ? '2026-10-03T10:00:00Z' : 'x',
    'array' => <Object?>[],
    _ => <String, dynamic>{},
  };
}

/// 只含快照里 required 字段的最小响应；可空字段给 null。
Map<String, dynamic> _minimalBody(String schemaName) => {
  for (final name in _required(schemaName))
    name: _sample(name, _properties(schemaName)[name] as Map<String, dynamic>),
};

Set<String> _parse(
  String schemaName,
  Object? Function(Map<String, dynamic> json) fromJson,
) {
  final body = _Recording(_minimalBody(schemaName));
  fromJson(body);
  return body.reads;
}

void _expectDeclaredAndRequired(String schemaName, Set<String> reads) {
  expect(reads, isNotEmpty);
  expect(
    reads.difference(_properties(schemaName).keys.toSet()),
    isEmpty,
    reason: '客户端读取了快照 $schemaName 里没有声明的字段',
  );
  expect(
    reads.difference(_required(schemaName)),
    isEmpty,
    reason: '客户端依赖了快照里可缺省的 $schemaName 字段',
  );
}

/// 记录请求体的假适配器。
class _Capture implements HttpClientAdapter {
  Object? body;

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    if (requestStream != null) {
      body = jsonDecode(
        utf8.decode(await requestStream.expand((c) => c).toList()),
      );
    }
    return ResponseBody.fromString('', 204);
  }

  @override
  void close({bool force = false}) {}
}

Dio _dio(_Capture capture) =>
    Dio(BaseOptions(baseUrl: 'http://test'))..httpClientAdapter = capture;

void main() {
  group('AI response models only depend on declared required fields', () {
    final cases =
        <(String, Object? Function(Map<String, dynamic>), Set<String>)>[
          (
            'PresetOut',
            AiPreset.fromJson,
            {'id', 'name', 'kind', 'base_url', 'chat_model', 'embedding_model'},
          ),
          ('LastTestOut', AiLastTest.fromJson, {'ok', 'checked_at', 'message'}),
          (
            'AiProviderOut',
            AiProvider.fromJson,
            {
              'id',
              'name',
              'kind',
              'base_url',
              'key_set',
              'last_test',
              'used_by',
            },
          ),
          (
            'ProviderTestOut',
            AiTestResult.fromJson,
            {'ok', 'checked_at', 'error_kind', 'message', 'duration_ms'},
          ),
          ('RoleBindingOut', AiRoleBinding.fromJson, {'provider_id', 'model'}),
          ('RolesOut', AiRoles.fromJson, {'chat', 'task', 'embedding'}),
          (
            'AiSettingsOut',
            AiSettings.fromJson,
            {
              'reversible_write',
              'suggest_matches',
              'auto_match',
              'auto_match_threshold',
            },
          ),
          (
            'SemanticStatusOut',
            AiSemanticStatus.fromJson,
            {'state', 'model', 'total', 'indexed', 'error'},
          ),
          (
            'UsageRowOut',
            AiUsageRow.fromJson,
            {
              'month',
              'role',
              'calls',
              'failures',
              'input_tokens',
              'output_tokens',
            },
          ),
        ];
    for (final (schema, fromJson, fields) in cases) {
      test(schema, () {
        final reads = _parse(schema, fromJson);
        _expectDeclaredAndRequired(schema, reads);
        expect(reads, fields);
      });
    }
  });

  group('nullability and enums match the snapshot', () {
    test('nullable fields the client treats as optional', () {
      bool nullable(String schema, String field) =>
          _nullable(_properties(schema)[field] as Map<String, dynamic>);
      const provider = 'AiProviderOut';
      expect(nullable(provider, 'last_test'), isTrue);
      expect(nullable('LastTestOut', 'message'), isTrue);
      expect(nullable('ProviderTestOut', 'error_kind'), isTrue);
      expect(nullable('ProviderTestOut', 'message'), isTrue);
      expect(nullable('SemanticStatusOut', 'model'), isTrue);
      expect(nullable('SemanticStatusOut', 'error'), isTrue);
      for (final role in aiRoles) {
        expect(nullable('RolesOut', role), isTrue, reason: role);
      }
      // 这些字段客户端没有兜底，快照必须保证非空
      expect(nullable(provider, 'key_set'), isFalse);
      expect(nullable('AiSettingsOut', 'auto_match_threshold'), isFalse);
      expect(nullable('UsageRowOut', 'calls'), isFalse);
    });

    test('roles, kinds and index states are the ones the page handles', () {
      Set<String> values(String schema, String field) =>
          ((_properties(schema)[field] as Map<String, dynamic>)['enum']
                  as List<dynamic>)
              .cast<String>()
              .toSet();
      expect(values('UsageRowOut', 'role'), aiRoles.toSet());
      expect(values('PresetOut', 'kind'), {'openai', 'anthropic'});
      expect(values('SemanticStatusOut', 'state'), {
        'off',
        'unavailable',
        'building',
        'ready',
        'error',
      });
      final used =
          ((_properties('AiProviderOut')['used_by']
                      as Map<String, dynamic>)['items']
                  as Map<String, dynamic>)['enum']
              as List<dynamic>;
      expect(used.toSet(), aiRoles.toSet());
    });
  });

  group('request bodies only use declared fields', () {
    test('ProviderRequest: required fields and a nullable api_key', () {
      expect(_required('ProviderRequest'), {'name', 'kind', 'base_url'});
      expect(
        _nullable(
          _properties('ProviderRequest')['api_key'] as Map<String, dynamic>,
        ),
        isTrue,
        reason: '清除 Key 要传 api_key: null',
      );
    });

    test('create and update providers', () async {
      final capture = _Capture();
      final declared = _properties('ProviderRequest').keys.toSet();
      await createAiProvider(
        _dio(capture),
        name: 'n',
        kind: 'openai',
        baseUrl: 'http://x',
        apiKey: 'k',
      );
      expect((capture.body as Map).keys.toSet(), declared);
      await updateAiProvider(
        _dio(capture),
        1,
        name: 'n',
        kind: 'openai',
        baseUrl: 'http://x',
        clearKey: true,
      );
      expect(capture.body, containsPair('api_key', null));
      await updateAiProvider(
        _dio(capture),
        1,
        name: 'n',
        kind: 'openai',
        baseUrl: 'http://x',
      );
      expect((capture.body as Map).keys.toSet().difference(declared), isEmpty);
      expect(capture.body, isNot(contains('api_key')), reason: '不改 Key 就不带');
    });

    test('RolesRequest: every role is optional and nullable', () async {
      expect(_required('RolesRequest'), isEmpty);
      for (final role in aiRoles) {
        expect(
          _nullable(_properties('RolesRequest')[role] as Map<String, dynamic>),
          isTrue,
        );
      }
      expect(_required('RoleBindingBody'), {'provider_id', 'model'});
      final capture = _Capture();
      await saveAiRoles(_dio(capture), {
        'chat': const AiRoleBinding(providerId: 1, model: 'm'),
        'task': null,
      });
      expect(capture.body, {
        'chat': {'provider_id': 1, 'model': 'm'},
        'task': null,
      });
      expect(
        (capture.body as Map).keys.toSet().difference(
          _properties('RolesRequest').keys.toSet(),
        ),
        isEmpty,
      );
      expect(
        ((capture.body as Map)['chat'] as Map).keys.toSet(),
        _properties('RoleBindingBody').keys.toSet(),
      );
    });

    test('AiSettingsRequest: nothing required, unset fields are omitted', () {
      expect(_required('AiSettingsRequest'), isEmpty);
      expect(
        _properties('AiSettingsRequest').keys,
        containsAll(['suggest_matches', 'auto_match', 'auto_match_threshold']),
      );
    });

    test('settings body only carries what changed', () async {
      final capture = _Capture();
      final dio = Dio(BaseOptions(baseUrl: 'http://test'))
        ..httpClientAdapter = capture;
      try {
        await saveAiSettings(dio, autoMatch: true);
      } catch (_) {
        // 假适配器返回空响应，解析失败不影响请求体的断言
      }
      expect(capture.body, {'auto_match': true});
    });
  });

  test('usage defaults to the documented months parameter', () {
    final usage =
        (_spec['paths'] as Map<String, dynamic>)['/api/admin/ai/usage']
            as Map<String, dynamic>;
    final parameters =
        (usage['get'] as Map<String, dynamic>)['parameters'] as List<dynamic>;
    final months = parameters.cast<Map<String, dynamic>>().firstWhere(
      (p) => p['name'] == 'months',
    );
    expect(months['schema']['default'], aiUsageMonths);
    expect(months['schema']['maximum'], greaterThanOrEqualTo(aiUsageMonths));
  });
}
