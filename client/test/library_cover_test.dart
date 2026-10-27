import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image_picker/image_picker.dart';
import 'package:iris/features/admin/libraries_api.dart';
import 'package:iris/features/admin/libraries_page.dart';
import 'package:iris/features/library/browse_api.dart';

import 'support/fake_api.dart';

const _base = '/api/admin/libraries';

Map<String, dynamic> _adminLibrary({
  List<Map<String, dynamic>> cover = const [],
  bool custom = false,
}) => {
  'id': 1,
  'name': '电影',
  'kind': 'movie',
  'sort_order': 1,
  'folders': [
    {
      'id': 1,
      'path': '/影视/电影',
      'source_id': 1,
      'source_name': '115 网盘',
      'plugin_id': 'source.115',
      'locator': '11',
    },
  ],
  'video_count': 0,
  'missing_count': 0,
  'metadata_provider': 'builtin.tmdb',
  'created_at': '2026-10-01T00:00:00Z',
  'cover': cover,
  'cover_custom': custom,
};

const _coverJson = [
  {'width': 780, 'url': '/api/images/a/780.jpg'},
  {'width': 1280, 'url': '/api/images/a/1280.jpg'},
];

void main() {
  group('cover parsing', () {
    test('LibrarySummary resolves cover urls against the server', () {
      final library = LibrarySummary.fromJson({
        'id': 1,
        'name': '电影',
        'kind': 'movie',
        'item_count': 3,
        'cover': _coverJson,
      }, 'http://srv:8000');
      expect(library.cover.sizes.map((s) => s.width), [780, 1280]);
      expect(library.cover.pick(1000), 'http://srv:8000/api/images/a/1280.jpg');
    });

    test('LibrarySummary rejects a response without cover', () {
      expect(
        () => LibrarySummary.fromJson({
          'id': 1,
          'name': '电影',
          'kind': 'movie',
          'item_count': 3,
        }),
        throwsA(isA<TypeError>()),
      );
    });

    test('AdminLibrary reads cover and cover_custom strictly', () {
      final full = AdminLibrary.fromJson(
        _adminLibrary(cover: [..._coverJson], custom: true),
        'http://srv',
      );
      expect(full.cover.sizes, hasLength(2));
      expect(full.coverCustom, isTrue);
      final plain = AdminLibrary.fromJson(_adminLibrary());
      expect(plain.cover.isEmpty, isTrue);
      expect(plain.coverCustom, isFalse);
      final missing = _adminLibrary()..remove('cover_custom');
      expect(() => AdminLibrary.fromJson(missing), throwsA(isA<TypeError>()));
    });
  });

  group('cover section in the library dialog', () {
    Future<FakeAdminApi> open(
      WidgetTester tester, {
      required bool custom,
      XFile? picked,
    }) async {
      final api = await pumpAdmin(
        tester,
        const LibrariesPage(),
        (api) {
          api.on('GET $_base', [_adminLibrary(cover: [], custom: custom)]);
          api.on('PUT $_base/1/cover', {'cover': _coverJson, 'custom': true});
          api.on('DELETE $_base/1/cover', {
            'cover': <dynamic>[],
            'custom': false,
          });
        },
        overrides: [
          libraryCoverPickerProvider.overrideWithValue(() async => picked),
        ],
      );
      await tester.tap(find.text('电影').first);
      await tester.pumpAndSettle();
      // 封面在「通用」页最下面，先滚到能看到上传按钮的位置
      await tester.ensureVisible(find.text('上传封面'));
      await tester.pumpAndSettle();
      return api;
    }

    testWidgets('automatic cover: reset is disabled, upload sends multipart', (
      tester,
    ) async {
      final api = await open(
        tester,
        custom: false,
        picked: XFile.fromData(
          Uint8List.fromList([1, 2, 3]),
          name: 'c.jpg',
          mimeType: 'image/jpeg',
        ),
      );
      expect(find.text('封面'), findsOneWidget);
      expect(find.text('自动'), findsOneWidget);
      final reset = find.widgetWithText(TextButton, '恢复自动');
      expect(tester.widget<TextButton>(reset).onPressed, isNull);

      await tester.tap(find.text('上传封面'));
      await tester.pumpAndSettle();
      expect(api.requests, contains('PUT $_base/1/cover'));
      expect(api.requests.where((r) => r == 'GET $_base').length, 2);
      expect(api.bodies['PUT $_base/1/cover'], contains('name="file"'));
      expect(find.text('自定义'), findsOneWidget);
      expect(tester.widget<TextButton>(reset).onPressed, isNotNull);
    });

    testWidgets('custom cover can be reset to automatic', (tester) async {
      final api = await open(tester, custom: true);
      expect(find.text('自定义'), findsOneWidget);
      await tester.tap(find.text('恢复自动'));
      await tester.pumpAndSettle();
      expect(api.requests, contains('DELETE $_base/1/cover'));
      expect(find.text('自动'), findsOneWidget);
    });

    testWidgets('cancelled picker sends nothing', (tester) async {
      final api = await open(tester, custom: false);
      await tester.tap(find.text('上传封面'));
      await tester.pumpAndSettle();
      expect(api.requests, isNot(contains('PUT $_base/1/cover')));
    });

    testWidgets('upload failure is shown and keeps the state', (tester) async {
      final api = await open(
        tester,
        custom: false,
        picked: XFile.fromData(Uint8List.fromList([1]), name: 'c.jpg'),
      );
      api.on('PUT $_base/1/cover', {
        'error': {'code': 'invalid_artwork', 'message': '图片无法识别'},
      }, status: 400);
      await tester.tap(find.text('上传封面'));
      await tester.pumpAndSettle();
      expect(find.text('图片无法识别'), findsOneWidget);
      expect(find.text('自动'), findsOneWidget);
    });
  });
}
