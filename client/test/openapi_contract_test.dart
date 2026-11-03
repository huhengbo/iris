import 'dart:collection';
import 'dart:convert';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/core/api/api_error.dart';
import 'package:iris/features/admin/catalog_api.dart';
import 'package:iris/features/admin/libraries_api.dart';
import 'package:iris/features/admin/plugin_catalog_api.dart';
import 'package:iris/features/admin/plugins_api.dart';
import 'package:iris/features/ai/ai_chat_controller.dart';
import 'package:iris/features/ai/ai_chat_models.dart';
import 'package:iris/features/admin/tasks_api.dart';
import 'package:iris/features/auth/auth_controller.dart';
import 'package:iris/features/library/browse_api.dart';
import 'package:iris/features/me/devices_api.dart';
import 'package:iris/features/me/webdav_api.dart';

/// 读取 docs/api/openapi.json（后端契约快照），校验客户端对它的假设。
final Map<String, dynamic> _spec = jsonDecode(
  File('../docs/api/openapi.json').readAsStringSync(),
) as Map<String, dynamic>;

Map<String, dynamic> _schema(String name) =>
    (_spec['components']['schemas'] as Map<String, dynamic>)[name]
        as Map<String, dynamic>;

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

bool _nullable(Map<String, dynamic> property) =>
    (property['anyOf'] as List<dynamic>? ?? const []).any(
      (o) => (o as Map<String, dynamic>)['type'] == 'null',
    );

/// 只含快照里 required 字段的最小响应；可空字段给 null。
Map<String, dynamic> _minimalBody(String schemaName) {
  final schema = _schema(schemaName);
  final properties = schema['properties'] as Map<String, dynamic>;
  return {
    for (final name in schema['required'] as List<dynamic>)
      '$name': _sample('$name', properties['$name'] as Map<String, dynamic>),
  };
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
    'object' => <String, dynamic>{},
    _ => switch (property[r'$ref']) {
      final String ref => _minimalBody(ref.split('/').last),
      _ => <Object?>[], // 其余引用都是列表
    },
  };
}

/// 解析 [schemaName] 的最小响应，返回客户端读取过的键。
Set<String> _parse(
  String schemaName,
  Object? Function(Map<String, dynamic> json) fromJson,
) {
  final body = _Recording(_minimalBody(schemaName));
  fromJson(body);
  return body.reads;
}

void _expectClientFieldsDeclared(
  String schemaName,
  Set<String> reads, {
  Set<String> nullSafeOptional = const {},
}) {
  final schema = _schema(schemaName);
  final declared = (schema['properties'] as Map<String, dynamic>).keys.toSet();
  final required = (schema['required'] as List<dynamic>).cast<String>().toSet();
  expect(reads, isNotEmpty);
  expect(
    reads.difference(declared),
    isEmpty,
    reason: '客户端读取了快照 $schemaName 里没有声明的字段',
  );
  // 客户端没有兜底的字段，必须是服务端承诺一定返回的
  expect(
    reads.difference(required).difference(nullSafeOptional),
    isEmpty,
    reason: '客户端依赖了快照里可缺省的 $schemaName 字段',
  );
}

DioException _http(int status, Object? body, {Map<String, String>? headers}) {
  final options = RequestOptions(path: '/api/x');
  return DioException(
    requestOptions: options,
    response: Response(
      requestOptions: options,
      statusCode: status,
      data: body,
      headers: Headers.fromMap({
        for (final e in (headers ?? const <String, String>{}).entries)
          e.key: [e.value],
      }),
    ),
  );
}

void main() {
  group('client models only depend on fields the snapshot declares', () {
    test('PluginOut -> AdminPlugin', () {
      final reads = _parse('PluginOut', AdminPlugin.fromJson);
      _expectClientFieldsDeclared('PluginOut', reads);
      expect(
        reads,
        containsAll(['id', 'state', 'source', 'available_version']),
      );
    });

    test('RepositoryOut -> PluginRepository', () {
      final reads = _parse('RepositoryOut', PluginRepository.fromJson);
      // schema 只在索引已解析时才有值，客户端按可空读取
      _expectClientFieldsDeclared(
        'RepositoryOut',
        reads,
        nullSafeOptional: {'schema'},
      );
      expect(reads, containsAll(['id', 'url', 'official', 'status']));
    });

    test('CatalogPluginOut -> CatalogPlugin', () {
      final reads = _parse('CatalogPluginOut', CatalogPlugin.fromJson);
      _expectClientFieldsDeclared('CatalogPluginOut', reads);
      expect(reads, containsAll(['id', 'version', 'status', 'capabilities']));
    });

    test('CatalogOut -> PluginCatalog', () {
      final reads = _parse('CatalogOut', PluginCatalog.fromJson);
      _expectClientFieldsDeclared('CatalogOut', reads);
    });

    test('UploadOut -> PluginUpload', () {
      final reads = _parse('UploadOut', PluginUpload.fromJson);
      _expectClientFieldsDeclared('UploadOut', reads);
      expect(reads, containsAll(['upload_id', 'sha256', 'action']));
    });

    test('UploadManifestOut -> PluginUploadManifest', () {
      final reads = _parse('UploadManifestOut', PluginUploadManifest.fromJson);
      _expectClientFieldsDeclared('UploadManifestOut', reads);
    });

    test('TaskOut -> AdminTask', () {
      final reads = _parse('TaskOut', AdminTask.fromJson);
      _expectClientFieldsDeclared('TaskOut', reads);
      expect(
        reads,
        containsAll(['id', 'kind', 'phase', 'status', 'cancel_requested']),
      );
    });

    test('WebDavOut -> WebDavInfo', () {
      final reads = _parse('WebDavOut', WebDavInfo.fromJson);
      _expectClientFieldsDeclared('WebDavOut', reads);
      expect(reads, {'enabled', 'username', 'url'});
    });

    test('LibraryOut -> LibrarySummary', () {
      final reads = _parse('LibraryOut', LibrarySummary.fromJson);
      _expectClientFieldsDeclared('LibraryOut', reads);
      expect(reads, {'id', 'name', 'kind', 'item_count', 'cover'});
    });

    test('UserOut -> AppUser', () {
      final reads = _parse('UserOut', AppUser.fromJson);
      _expectClientFieldsDeclared('UserOut', reads);
      expect(reads, containsAll(['allow_subtitle_download']));
    });

    test('DeviceSessionOut -> DeviceSession', () {
      final reads = _parse('DeviceSessionOut', DeviceSession.fromJson);
      _expectClientFieldsDeclared('DeviceSessionOut', reads);
      expect(reads, contains('kind'));
    });

    test('AdminLibraryOut -> AdminLibrary', () {
      final reads = _parse(
        'AdminLibraryOut',
        (json) => AdminLibrary.fromJson(json, 'http://test'),
      );
      _expectClientFieldsDeclared('AdminLibraryOut', reads);
      expect(
        reads,
        containsAll(['metadata_provider', 'cover', 'cover_custom']),
      );
    });

    test('BatchAssignmentOut -> BatchAssignmentReport', () {
      final reads = _parse(
        'BatchAssignmentOut',
        BatchAssignmentReport.fromJson,
      );
      _expectClientFieldsDeclared('BatchAssignmentOut', reads);
      expect(reads, contains('task_id'));
    });

    test('LibraryDetailOut -> LibraryDetail', () {
      final reads = _parse(
        'LibraryDetailOut',
        (json) => LibraryDetail.fromJson(json, 'http://test'),
      );
      _expectClientFieldsDeclared('LibraryDetailOut', reads);
      expect(reads, contains('categories'));
    });

    test('ItemDetailOut -> MediaDetail', () {
      final reads = _parse(
        'ItemDetailOut',
        (json) => MediaDetail.fromJson(json, 'http://test'),
      );
      _expectClientFieldsDeclared('ItemDetailOut', reads);
      expect(
        reads,
        containsAll([
          'studios',
          'countries',
          'ratings',
          'watchlisted',
          'favorite',
        ]),
      );
    });

    test('SeasonDetailOut -> SeasonDetail', () {
      final reads = _parse(
        'SeasonDetailOut',
        (json) => SeasonDetail.fromJson(json, 'http://test'),
      );
      _expectClientFieldsDeclared('SeasonDetailOut', reads);
      expect(reads, contains('item_id'));
    });

    test('SubtitleOut -> MediaSubtitle', () {
      final reads = _parse('SubtitleOut', MediaSubtitle.fromJson);
      _expectClientFieldsDeclared('SubtitleOut', reads);
      expect(reads, contains('source'));
    });

    test('HomeOut -> HomeFeed', () {
      final reads = _parse(
        'HomeOut',
        (json) => HomeFeed.fromJson(json, 'http://test'),
      );
      _expectClientFieldsDeclared('HomeOut', reads);
      expect(
        reads,
        containsAll([
          'libraries',
          'recent',
          'favorites',
          'watchlist',
          'shelves',
        ]),
      );
    });
  });

  group('ApiError and the error shapes in the snapshot', () {
    test('the snapshot declares the runtime error envelope', () {
      final envelope = _schema('ErrorResponse');
      expect((envelope['properties'] as Map).keys, ['error']);
      final body = _schema('ErrorBody');
      expect(body['required'], containsAll(['code', 'message']));
      expect(
        (body['properties'] as Map).keys,
        containsAll(['code', 'message']),
      );
    });

    test('validation errors in the declared envelope are parsed', () {
      final error = ApiError.from(
        _http(422, {
          'error': {
            'code': 'validation_error',
            'message': '请求参数不合法',
            'details': [
              {
                'loc': ['body', 'username'],
                'type': 'missing',
              },
            ],
          },
        }),
      );
      expect(error.code, 'validation_error');
      expect(error.statusCode, 422);
      expect(error.kind, ApiErrorKind.validation);
    });

    test(
      'a body that is not the envelope (e.g. from a proxy) does not crash',
      () {
        final error = ApiError.from(_http(422, {'detail': 'oops'}));
        expect(error.code, 'http_422');
        expect(error.kind, ApiErrorKind.validation);
        expect(error.message, isNotEmpty);
      },
    );

    test('runtime {error:{code,message}} body is parsed', () {
      final error = ApiError.from(
        _http(404, {
          'error': {'code': 'item_not_found', 'message': '没有这部影片'},
        }),
      );
      expect(error.code, 'item_not_found');
      expect(error.message, '没有这部影片');
      expect(error.statusCode, 404);
      expect(error.kind, ApiErrorKind.notFound);
    });

    test('details and retry-after do not break parsing', () {
      final error = ApiError.from(
        _http(
          429,
          {
            'error': {
              'code': 'rate_limited',
              'message': '请求太频繁',
              'details': {'scope': 'login'},
            },
          },
          headers: {'retry-after': '30'},
        ),
      );
      expect(error.code, 'rate_limited');
      expect(error.retryAfter, 30);
      expect(error.kind, ApiErrorKind.rateLimited);
    });

    test('error without code or message falls back to safe defaults', () {
      final error = ApiError.from(_http(500, {'error': <String, dynamic>{}}));
      expect(error.code, 'unknown');
      expect(error.message, '请求失败');
      expect(error.kind, ApiErrorKind.server);
    });

    test('non-JSON error body uses the HTTP status', () {
      final error = ApiError.from(_http(502, '<html>bad gateway</html>'));
      expect(error.code, 'http_502');
      expect(error.statusCode, 502);
      expect(error.kind, ApiErrorKind.server);
    });
  });

  group('AI chat models (ADR 0030, M58)', () {
    test('ChatOut -> AiChatSummary', () {
      final reads = _parse('ChatOut', AiChatSummary.fromJson);
      _expectClientFieldsDeclared('ChatOut', reads);
      expect(reads, {'id', 'title', 'created_at', 'updated_at'});
    });

    test('ChatDetailOut -> AiChatDetail', () {
      final reads = _parse(
        'ChatDetailOut',
        (json) => AiChatDetail.fromJson(json, 'http://test', AiKeys()),
      );
      _expectClientFieldsDeclared('ChatDetailOut', reads);
      expect(reads, {'id', 'title', 'created_at', 'updated_at', 'messages'});
    });

    test('ChatMessageOut -> AiMessage', () {
      final reads = _parse(
        'ChatMessageOut',
        (json) => AiMessage.fromJson(json, 'http://test', key: 0, question: ''),
      );
      _expectClientFieldsDeclared('ChatMessageOut', reads);
      expect(reads, {
        'id',
        'role',
        'content',
        'steps',
        'items',
        'suggestions',
        'thinking_seconds',
        'status',
        'error',
      });
    });

    test(
      'ChatMessageOut role and status values are the ones the client maps',
      () {
        final properties =
            _schema('ChatMessageOut')['properties'] as Map<String, dynamic>;
        expect(properties['role']['enum'], ['user', 'assistant']);
        expect(properties['status']['enum'], ['done', 'stopped', 'error']);
        // thinking_seconds 和 error 在没有时为 null，而不是缺省
        expect(_nullable(properties['thinking_seconds']), isTrue);
        expect(_nullable(properties['error']), isTrue);
        final required = _schema('ChatMessageOut')['required'] as List<dynamic>;
        expect(required, containsAll(['thinking_seconds', 'error']));
      },
    );

    test('ItemCardOut -> MediaCard (AI item cards reuse the poster card)', () {
      final reads = _parse(
        'ItemCardOut',
        (json) => MediaCard.fromJson(json, 'http://test'),
      );
      _expectClientFieldsDeclared('ItemCardOut', reads);
      expect(reads, containsAll(['id', 'library_id', 'kind', 'title']));
    });

    test('ChatStepOut -> AiStep', () {
      final reads = _parse('ChatStepOut', AiStep.fromJson);
      _expectClientFieldsDeclared('ChatStepOut', reads);
      expect(reads, {'name', 'label', 'summary', 'ok', 'item_ids'});
    });

    // SSE 各事件的 data：字段名、必填、可空都和快照一致
    final events = <String, (String, Object? Function(Map<String, dynamic>))>{
      'StartData': ('start', AiStarted.fromJson),
      'ThinkingData': ('thinking', AiThinking.fromJson),
      'ToolStartData': ('tool_start', AiToolStarted.fromJson),
      'ToolEndData': (
        'tool_end',
        (json) => AiToolEnded.fromJson(json, 'http://test'),
      ),
      'TextData': ('text', AiTextDelta.fromJson),
      'SuggestionsData': ('suggestions', AiSuggestions.fromJson),
      'DoneData': ('done', AiDone.fromJson),
      'ErrorData': ('error', AiFailed.fromJson),
    };
    for (final entry in events.entries) {
      final (event, parse) = entry.value;
      test('${entry.key} -> SSE "$event"', () {
        final reads = _parse(entry.key, parse);
        _expectClientFieldsDeclared(entry.key, reads);
        // 客户端读取的正好是快照声明的全部字段，新增字段会在这里提醒我们
        final declared =
            (_schema(entry.key)['properties'] as Map<String, dynamic>).keys;
        expect(reads, declared.toSet());
      });
    }

    test('thinking.seconds is nullable and required', () {
      final schema = _schema('ThinkingData');
      final properties = schema['properties'] as Map<String, dynamic>;
      expect(_nullable(properties['seconds']), isTrue);
      expect(schema['required'], containsAll(['delta', 'seconds']));
      // seconds 为 null 是思考增量，有值是思考结束
      expect(
        AiThinking.fromJson({'delta': 'x', 'seconds': null}).seconds,
        isNull,
      );
      expect(AiThinking.fromJson({'delta': '', 'seconds': 2}).seconds, 2.0);
    });

    test(
      'the event names in ChatStream are exactly the ones the client parses',
      () {
        final stream = _schema('ChatStream');
        final item =
            (stream['properties']['events']['items'] as Map<String, dynamic>);
        final mapping =
            (item['discriminator']['mapping'] as Map<String, dynamic>);
        expect(mapping.keys.toSet(), {for (final e in events.values) e.$1});
        for (final MapEntry(key: name, value: ref) in mapping.entries) {
          // #/components/schemas/StartEvent -> StartData
          final eventSchema = _schema('$ref'.split('/').last);
          expect(eventSchema['properties']['event']['const'], name);
          final dataRef = eventSchema['properties']['data'][r'$ref'] as String;
          expect(events.containsKey(dataRef.split('/').last), isTrue);
          expect(events[dataRef.split('/').last]!.$1, name);
          expect(
            AiEvent.parse(
              name,
              _minimalBody(dataRef.split('/').last),
              'http://test',
            ),
            isNotNull,
          );
        }
        // 不认识的事件被忽略，而不是报错
        expect(AiEvent.parse('future_event', const {}, 'http://test'), isNull);
      },
    );

    test('ToolEndData items are the same card shape as the poster wall', () {
      final items =
          (_schema('ToolEndData')['properties']['items']
                  as Map<String, dynamic>)['items']
              as Map<String, dynamic>;
      expect(items[r'$ref'], '#/components/schemas/ItemCardOut');
      final message =
          _schema('ChatMessageOut')['properties']['items']
              as Map<String, dynamic>;
      expect(
        (message['items'] as Map<String, dynamic>)[r'$ref'],
        '#/components/schemas/ItemCardOut',
      );
    });

    test('SendMessageRequest: content is required, 1..2000 chars', () {
      final schema = _schema('SendMessageRequest');
      expect(schema['required'], ['content']);
      final content = schema['properties']['content'] as Map<String, dynamic>;
      expect(content['minLength'], 1);
      expect(content['maxLength'], aiMaxQuestionLength);
    });

    test('paths and status codes the client relies on', () {
      final paths = _spec['paths'] as Map<String, dynamic>;
      expect(paths['/api/ai/chats']['get']['responses'], contains('200'));
      expect(paths['/api/ai/chats']['post']['responses'], contains('201'));
      expect(
        paths['/api/ai/chats/{chat_id}']['delete']['responses'],
        contains('204'),
      );
      expect(
        paths['/api/ai/chats/{chat_id}']['get']['responses'],
        contains('200'),
      );
      final send = paths['/api/ai/chats/{chat_id}/messages']['post'];
      expect(
        (send['responses']['200']['content'] as Map<String, dynamic>).keys,
        ['text/event-stream'],
      );
      expect(
        send['requestBody']['content']['application/json']['schema'][r'$ref'],
        '#/components/schemas/SendMessageRequest',
      );
    });
  });
}
