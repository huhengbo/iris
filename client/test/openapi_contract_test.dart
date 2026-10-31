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
}
