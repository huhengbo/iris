import 'dart:async';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/core/providers.dart';
import 'package:iris/features/admin/libraries_page.dart';

import 'support/fake_api.dart';

const _base = '/api/admin/libraries/1';

Map<String, dynamic> _downloader(
  String id,
  String name, {
  bool enabled = false,
  bool pluginEnabled = true,
  String? reason,
  bool canMatch = true,
}) => {
  'id': id,
  'name': name,
  'enabled': enabled,
  'plugin_enabled': pluginEnabled,
  'unavailable_reason': reason,
  'can_match': canMatch,
};

Map<String, dynamic> _downloaders({
  List<Map<String, dynamic>>? metadata,
  List<Map<String, dynamic>>? images,
  List<Map<String, dynamic>>? subtitles,
  List<String> languages = const [],
  bool skipIfAudio = false,
}) => {
  'metadata':
      metadata ??
      [
        _downloader('builtin.tmdb', 'TMDB 元数据', enabled: true),
        _downloader('builtin.douban', '豆瓣', canMatch: false),
      ],
  'images':
      images ??
      [
        _downloader('builtin.tmdb', 'TMDB 图片', enabled: true),
        _downloader('builtin.fanart', 'Fanart.tv', enabled: true),
      ],
  'subtitles':
      subtitles ??
      [
        _downloader('builtin.assrt', '射手网', enabled: true),
        _downloader(
          'org.example.off',
          '停用的字幕站',
          pluginEnabled: false,
          reason: '未配置 API Key',
        ),
      ],
  'subtitle_languages': languages,
  'subtitle_skip_if_audio': skipIfAudio,
};

/// 可以让 PUT 停在响应前，用来观察保存期间控件是否禁用。
class _GatedApi extends FakeAdminApi {
  Completer<void>? putGate;

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    if (options.method == 'PUT' && putGate != null) await putGate!.future;
    return super.fetch(options, requestStream, cancelFuture);
  }
}

const _libraryJson = {
  'id': 1,
  'name': '电影',
  'kind': 'movie',
  'sort_order': 1,
  'folders': <Object>[],
  'video_count': 3,
  'missing_count': 0,
  'metadata_provider': 'builtin.tmdb',
  'cover': <Object>[],
  'cover_custom': false,
  'created_at': '2026-10-01T00:00:00Z',
};

/// 「编辑资料库 › 高级」里下载器分区的接口；PUT 把请求体合并进当前状态。
_GatedApi _api(Map<String, dynamic> initial) {
  var state = initial;
  final api = _GatedApi();
  api
    ..on('GET /api/admin/libraries', [_libraryJson])
    ..on('GET $_base/settings', {'metadata_language': 'zh-CN'});
  api.routes['GET $_base/downloaders'] = (_) => (200, state);
  api.routes['PUT $_base/downloaders'] = (body) {
    final patch = body as Map<String, dynamic>;
    state = {
      ...state,
      for (final entry in patch.entries)
        entry.key: switch (entry.key) {
          'metadata' || 'images' || 'subtitles' => _merge(
            state[entry.key] as List<dynamic>,
            entry.value as List<dynamic>,
          ),
          _ => entry.value,
        },
    };
    return (200, state);
  };
  return api;
}

Future<void> _pump(WidgetTester tester, _GatedApi api) async {
  tester.view.physicalSize = const Size(390 * 3, 2400 * 3);
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  final dio = Dio(BaseOptions(baseUrl: 'http://test'))..httpClientAdapter = api;
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        apiClientProvider.overrideWithValue(dio),
        apiBaseUrlProvider.overrideWithValue('http://test'),
      ],
      child: MaterialApp(theme: buildTheme(), home: const LibrariesPage()),
    ),
  );
  await tester.pumpAndSettle();
  // 下载器在「编辑资料库 › 高级」里
  await tester.tap(find.text('电影').first);
  await tester.pumpAndSettle();
  await tester.tap(find.text('高级'));
  await tester.pumpAndSettle();
}

CheckboxListTile _tile(WidgetTester tester, String title) =>
    tester.widget(find.widgetWithText(CheckboxListTile, title));

/// 下载器列表嵌在「高级」页的 ListView 里，自己的 ReorderableListView 不能滚，
/// 要滚外层的列表；ensureVisible 会找错滚动容器。
Future<void> _show(WidgetTester tester, Finder target) async {
  final scrollable = find
      .descendant(
        of: find.byKey(const ValueKey('library-advanced-tab')),
        matching: find.byType(Scrollable),
      )
      .first;
  await tester.scrollUntilVisible(target, 200, scrollable: scrollable);
  await tester.pumpAndSettle();
}

/// 服务端按 id 保留插件名称等信息，只更新勾选和顺序。
List<Map<String, dynamic>> _merge(List<dynamic> current, List<dynamic> patch) {
  final byId = {
    for (final row in current) (row as Map<String, dynamic>)['id']: row,
  };
  return [
    for (final row in patch)
      {...?byId[(row as Map<String, dynamic>)['id']], ...row},
  ];
}

void main() {
  testWidgets('lists all downloader roles and saves only the changed list', (
    tester,
  ) async {
    final api = _api(_downloaders());
    await _pump(tester, api);

    expect(find.text('元数据下载器'), findsOneWidget);
    expect(find.text('图片获取器'), findsOneWidget);
    expect(find.text('字幕下载器'), findsOneWidget);
    expect(find.text('字幕'), findsOneWidget);
    // 每行都有拖动柄，遥控器也能用上移/下移调整顺序
    expect(find.byIcon(Icons.drag_handle), findsNWidgets(6));
    // 停用的插件给出原因
    expect(find.textContaining('插件已停用'), findsOneWidget);

    await _show(tester, find.text('豆瓣'));
    await tester.tap(find.text('豆瓣'));
    await tester.pumpAndSettle();

    expect(api.bodies['PUT $_base/downloaders'], {
      'metadata': [
        {'id': 'builtin.tmdb', 'enabled': true},
        {'id': 'builtin.douban', 'enabled': true},
      ],
    });
    expect(_tile(tester, '豆瓣').value, isTrue);
  });

  testWidgets('moves a downloader with the up and down buttons', (
    tester,
  ) async {
    final api = _api(_downloaders());
    await _pump(tester, api);

    await _show(tester, find.byTooltip('下移 TMDB 图片'));
    await tester.tap(find.byTooltip('下移 TMDB 图片'));
    await tester.pumpAndSettle();

    expect(api.bodies['PUT $_base/downloaders'], {
      'images': [
        {'id': 'builtin.fanart', 'enabled': true},
        {'id': 'builtin.tmdb', 'enabled': true},
      ],
    });
  });

  testWidgets('warns when a fill-only plugin is first in the metadata list', (
    tester,
  ) async {
    final api = _api(
      _downloaders(
        metadata: [
          _downloader('builtin.douban', '豆瓣', enabled: true, canMatch: false),
          _downloader('builtin.tmdb', 'TMDB 元数据'),
        ],
      ),
    );
    await _pump(tester, api);

    expect(find.text('只能补缺，不能放在第一位'), findsOneWidget);
  });

  testWidgets('a rejected save shows the server error and rolls back', (
    tester,
  ) async {
    final api = _api(_downloaders());
    api.on('PUT $_base/downloaders', {
      'error': {'code': 'primary_cannot_match', 'message': '第一个元数据下载器必须支持匹配'},
    }, status: 400);
    await _pump(tester, api);
    expect(_tile(tester, '豆瓣').value, isFalse);

    await _show(tester, find.text('豆瓣'));
    await tester.tap(find.text('豆瓣'));
    await tester.pumpAndSettle();

    expect(find.text('第一个元数据下载器必须支持匹配'), findsOneWidget);
    expect(_tile(tester, '豆瓣').value, isFalse);
  });

  testWidgets('disables the whole list while a change is saving', (
    tester,
  ) async {
    final api = _api(_downloaders());
    api.putGate = Completer<void>();
    await _pump(tester, api);

    await _show(tester, find.text('豆瓣'));
    await tester.tap(find.text('豆瓣'));
    await tester.pump();

    expect(_tile(tester, 'TMDB 元数据').onChanged, isNull);
    expect(_tile(tester, '豆瓣').onChanged, isNull);
    // 其他列表不受影响
    expect(_tile(tester, 'Fanart.tv').onChanged, isNotNull);
    final row = find.ancestor(
      of: find.text('豆瓣'),
      matching: find.byType(CheckboxListTile),
    );
    final down = tester.widget<IconButton>(
      find.descendant(of: row, matching: find.byType(IconButton)).last,
    );
    expect(down.onPressed, isNull);

    api.putGate!.complete();
    await tester.pumpAndSettle();
    expect(api.bodies['PUT $_base/downloaders'], {
      'metadata': [
        {'id': 'builtin.tmdb', 'enabled': true},
        {'id': 'builtin.douban', 'enabled': true},
      ],
    });
  });

  testWidgets(
    'picks subtitle languages and whether to skip the audio language',
    (tester) async {
      final api = _api(_downloaders());
      await _pump(tester, api);
      expect(find.text('由计划任务『下载字幕』执行'), findsOneWidget);

      await _show(tester, find.text('英语'));
      await tester.tap(find.text('英语'));
      await tester.pumpAndSettle();
      expect(api.bodies['PUT $_base/downloaders'], {
        'subtitle_languages': ['en'],
      });

      await _show(tester, find.text('音轨是该语言时跳过'));
      await tester.tap(find.text('音轨是该语言时跳过'));
      await tester.pumpAndSettle();
      expect(api.bodies['PUT $_base/downloaders'], {
        'subtitle_skip_if_audio': true,
      });
    },
  );
}
