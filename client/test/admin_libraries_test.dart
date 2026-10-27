import 'package:iris/features/admin/libraries_page.dart';
import 'package:iris/features/admin/sync_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/fake_api.dart';

const _base = '/api/admin/libraries';
const _sync = '/api/admin/sync';
const _resources = '/api/admin/resources';

/// 编辑资料库的对话框分了页，操作目录前要先切到「添加文件夹」。
Future<void> openFoldersTab(WidgetTester tester) async {
  await tester.tap(find.text('添加文件夹'));
  await tester.pumpAndSettle();
}

Map<String, dynamic> _source(
  int id,
  String name, {
  String plugin = 'source.115',
  bool enabled = true,
}) => {
  'id': id,
  'plugin_id': plugin,
  'plugin_name': plugin == 'source.115' ? '115 网盘' : '本地目录',
  'plugin_enabled': true,
  'name': name,
  'enabled': enabled,
  'config': <String, dynamic>{},
  'status': 'ok',
  'last_error': null,
  'checked_at': null,
  'folder_count': 0,
  'supports_delete': plugin == 'source.115',
};

Map<String, dynamic> _library(
  int id,
  String name, {
  String kind = 'movie',
  List<(String, String)> folders = const [('11', '/影视/电影')],
  int videos = 0,
  int missing = 0,
}) => {
  'id': id,
  'name': name,
  'kind': kind,
  'sort_order': id,
  'folders': [
    for (final (locator, path) in folders)
      {
        'id': id,
        'path': path,
        'source_id': 1,
        'source_name': '115 网盘',
        'plugin_id': 'source.115',
        'locator': locator,
      },
  ],
  'video_count': videos,
  'missing_count': missing,
  'metadata_provider': 'builtin.tmdb',
  'cover': <Object>[],
  'cover_custom': false,
  'created_at': '2026-10-01T00:00:00Z',
};

Map<String, dynamic> _metadataProvider(
  String id,
  String name, {
  bool builtIn = false,
  bool enabled = true,
  String? reason,
}) => {
  'id': id,
  'name': name,
  'built_in': builtIn,
  'enabled': enabled,
  'unavailable_reason': reason,
};

Map<String, dynamic> _job({
  int id = 1,
  String trigger = 'manual',
  String scope = 'all',
  String? target,
  String status = 'succeeded',
  String phase = 'done',
  int done = 0,
  int total = 0,
  Map<String, dynamic> stats = const {},
  String? error,
  String? waitingUntil,
  String? waitingReason,
}) => {
  'id': id,
  'trigger': trigger,
  'scope': scope,
  'library_id': null,
  'library_name': null,
  'target': target,
  'status': status,
  'phase': phase,
  'progress_done': done,
  'progress_total': total,
  'stats': {
    'added': 0,
    'removed': 0,
    'renamed': 0,
    'restored': 0,
    'failed': 0,
    'paused': 0,
    'purged': 0,
    'matched': 0,
    'unmatched': 0,
    'ignored': 0,
    'waiting': 0,
    'problems': <String>[],
    ...stats,
  },
  'error': error,
  'waiting_until': waitingUntil,
  'waiting_reason': waitingReason,
  'started_at': '2026-10-01T01:00:00Z',
  'finished_at': status == 'running' ? null : '2026-10-01T01:02:05Z',
};

Finder _selectButton(String name) =>
    find.bySemanticsLabel('选择 $name').hitTestable();

TextButton _selectWidget(WidgetTester tester, String name) => tester.widget(
  find.ancestor(
    of: find.bySemanticsLabel('选择 $name'),
    matching: find.byType(TextButton),
  ),
);

/// 展开同步页里默认收起的「自动同步」。
Future<void> expandAutoSync(WidgetTester tester) async {
  final header = find.text('自动同步');
  await tester.ensureVisible(header);
  await tester.tap(header);
  await tester.pumpAndSettle();
}

void main() {
  group('libraries page', () {
    testWidgets('empty state explains what a library is', (tester) async {
      await pumpAdmin(
        tester,
        const LibrariesPage(),
        (api) => api.on('GET $_base', []),
      );
      expect(find.text('还没有资料库'), findsOneWidget);
      expect(find.text('新建资料库'), findsOneWidget);
    });

    testWidgets('lists libraries with counts and folders', (tester) async {
      await pumpAdmin(
        tester,
        const LibrariesPage(),
        (api) => api.on('GET $_base', [
          _library(
            1,
            '电影',
            folders: [('11', '/影视/电影'), ('12', '/影视/纪录片')],
            videos: 120,
            missing: 2,
          ),
          _library(2, '剧集', kind: 'series', folders: [('21', '/影视/剧集')]),
        ]),
      );
      expect(find.textContaining('电影 · 120 个视频 · 2 个已失效'), findsOneWidget);
      expect(
        find.textContaining('115 网盘:/影视/电影、115 网盘:/影视/纪录片'),
        findsOneWidget,
      );
      expect(find.textContaining('剧集 · 0 个视频'), findsOneWidget);
    });

    testWidgets('unselectable folder cannot be chosen', (tester) async {
      await pumpAdmin(tester, const LibrariesPage(), (api) {
        api.on('GET $_base', <Map<String, dynamic>>[]);
        api.on('GET $_resources', [_source(1, '115 网盘')]);
        api.on('GET $_resources/1/browse', {
          'locator': '0',
          'path': '/',
          'selectable': false,
          'entries': [
            {'locator': '1', 'name': '影视'},
          ],
        });
      });

      await tester.tap(find.text('新建资料库'));
      await tester.pumpAndSettle();
      await openFoldersTab(tester);
      await tester.tap(find.text('添加目录'));
      await tester.pumpAndSettle();

      expect(find.text('选择当前目录（/）'), findsOneWidget);
      expect(find.text('不能选择这个目录'), findsOneWidget);
      final tile = find.ancestor(
        of: find.text('选择当前目录（/）'),
        matching: find.byType(ListTile),
      );
      expect(tester.widget<ListTile>(tile).enabled, isFalse);
    });

    testWidgets('create a library by browsing a source', (tester) async {
      var libraries = <Map<String, dynamic>>[
        _library(1, '动画', folders: [('13', '/影视/电影/动画')]),
      ];
      final api = await pumpAdmin(tester, const LibrariesPage(), (api) {
        api.routes['GET $_base'] = (_) => (200, libraries);
        api.on('GET $_resources', [
          _source(1, '115 网盘'),
          _source(2, '停用的盘', plugin: 'source.local', enabled: false),
        ]);
        api.on('GET $_resources/1/browse', {
          'locator': '0',
          'path': '/',
          'selectable': true,
          'entries': [
            {'locator': '1', 'name': '影视'},
          ],
        });
        api.on('GET $_resources/1/browse?locator=1', {
          'locator': '1',
          'path': '/影视',
          'selectable': true,
          'entries': [
            {'locator': '11', 'name': '电影'},
            {'locator': '14', 'name': '剧集'},
          ],
        });
        api.routes['POST $_base'] = (_) {
          libraries = [
            ...libraries,
            _library(2, '剧集', kind: 'series', folders: [('14', '/影视/剧集')]),
          ];
          return (201, libraries.last);
        };
      });

      await tester.tap(find.text('新建资料库'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), '剧集');
      await tester.tap(find.text('剧集').last);
      await openFoldersTab(tester);
      await tester.tap(find.text('添加目录'));
      await tester.pumpAndSettle();

      // 上级目录包含别的资料库的目录，不能选，但可以进入
      expect(find.text('与资料库「动画」的目录 /影视/电影/动画 重叠'), findsOneWidget);
      expect(_selectWidget(tester, '影视').onPressed, isNull);
      await tester.tap(find.text('影视'));
      await tester.pumpAndSettle();
      expect(find.text('根目录'), findsOneWidget);
      expect(_selectWidget(tester, '电影').onPressed, isNull);
      await tester.tap(_selectButton('剧集'));
      await tester.pumpAndSettle();

      expect(find.text('/影视/剧集'), findsOneWidget);
      await tester.tap(find.text('创建'));
      await tester.pumpAndSettle();

      expect(api.bodies['POST $_base'], {
        'name': '剧集',
        'kind': 'series',
        'roots': [
          {'source_id': 1, 'locator': '14'},
        ],
        'metadata_provider': 'builtin.tmdb',
      });
      expect(find.byType(AlertDialog), findsNothing);
      expect(find.textContaining('剧集 · 0 个视频'), findsOneWidget);
    });

    testWidgets('create a library from a local source', (tester) async {
      final api = await pumpAdmin(tester, const LibrariesPage(), (api) {
        api.on('GET $_base', [
          {
            ..._library(1, '电影'),
            'folders': [
              {
                'id': 5,
                'path': '/movies/old',
                'source_id': 3,
                'source_name': 'NAS',
                'plugin_id': 'source.local',
                'locator': 'movies/old',
              },
            ],
          },
        ]);
        api.on('GET $_resources', [
          _source(1, '115 网盘'),
          _source(3, 'NAS', plugin: 'source.local'),
        ]);
        api.on('GET $_resources/3/browse', {
          'locator': null,
          'path': '/',
          'selectable': true,
          'entries': [
            {'locator': 'movies', 'name': 'movies'},
            {'locator': 'tv', 'name': 'tv'},
          ],
        });
        api.on('GET $_resources/3/browse?locator=tv', {
          'locator': 'tv',
          'path': '/tv',
          'selectable': true,
          'entries': <Object>[],
        });
        api.on('POST $_base', _library(2, '剧集'), status: 201);
      });

      expect(find.textContaining('NAS:/movies/old'), findsOneWidget);
      await tester.tap(find.text('新建资料库'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), '剧集');
      await openFoldersTab(tester);
      await tester.tap(find.text('添加目录'));
      await tester.pumpAndSettle();

      // 有多个可用来源时先选来源
      expect(find.text('从哪个来源添加目录'), findsOneWidget);
      await tester.tap(find.text('NAS'));
      await tester.pumpAndSettle();

      expect(find.text('与资料库「电影」的目录 /movies/old 重叠'), findsOneWidget);
      expect(_selectWidget(tester, 'movies').onPressed, isNull);
      await tester.tap(find.text('tv'));
      await tester.pumpAndSettle();
      expect(find.text('这里没有子目录'), findsOneWidget);
      await tester.tap(find.text('选择当前目录（/tv）'));
      await tester.pumpAndSettle();

      expect(find.text('/tv'), findsOneWidget);
      expect(find.text('NAS'), findsOneWidget);
      await tester.tap(find.text('创建'));
      await tester.pumpAndSettle();
      expect(api.bodies['POST $_base'], {
        'name': '剧集',
        'kind': 'movie',
        'roots': [
          {'source_id': 3, 'locator': 'tv'},
        ],
        'metadata_provider': 'builtin.tmdb',
      });
    });

    testWidgets('adding a folder without usable sources explains why', (
      tester,
    ) async {
      final api = await pumpAdmin(tester, const LibrariesPage(), (api) {
        api.on('GET $_base', <Object>[]);
        api.on('GET $_resources', [_source(1, '115 网盘', enabled: false)]);
      });
      await tester.tap(find.text('新建资料库'));
      await tester.pumpAndSettle();
      await openFoldersTab(tester);
      await tester.tap(find.text('添加目录'));
      await tester.pumpAndSettle();
      expect(find.text('资源来源都已停用，先到「资源来源」里启用'), findsOneWidget);
      expect(api.requests, isNot(contains('GET $_resources/1/browse')));
    });

    testWidgets('dialog validates input and shows server errors inline', (
      tester,
    ) async {
      final api = await pumpAdmin(tester, const LibrariesPage(), (api) {
        api.on('GET $_base', [_library(1, '电影')]);
        api.on('PATCH $_base/1', {
          'error': {'code': 'library_name_taken', 'message': '已有同名资料库「剧集」'},
        }, status: 409);
      });

      await tester.tap(find.text('电影').first);
      await tester.pumpAndSettle();
      expect(find.text('编辑资料库'), findsOneWidget);
      expect(find.text('类型创建后不能修改'), findsOneWidget);
      expect(find.text('语言'), findsOneWidget);

      // 名称和目录在两个分页里，保存时回到第一个出错的分页
      await tester.enterText(find.byType(TextField), ' ');
      await openFoldersTab(tester);
      await tester.tap(find.byTooltip('移除 /影视/电影'));
      await tester.tap(find.text('保存'));
      await tester.pumpAndSettle();
      expect(find.text('请输入名称'), findsOneWidget);
      await tester.tap(find.text('添加文件夹'));
      await tester.pumpAndSettle();
      expect(find.text('至少添加一个目录'), findsOneWidget);
      expect(api.requests, isNot(contains('PATCH $_base/1')));

      await tester.tap(find.text('取消'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('电影').first);
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), '剧集');
      await tester.tap(find.text('保存'));
      await tester.pumpAndSettle();

      // 只提交改动的字段
      expect(api.bodies['PATCH $_base/1'], {'name': '剧集'});
      expect(find.text('已有同名资料库「剧集」'), findsOneWidget);
      expect(find.byType(AlertDialog), findsOneWidget);
    });

    testWidgets('only a new library picks the primary metadata plugin', (
      tester,
    ) async {
      final api = await pumpAdmin(tester, const LibrariesPage(), (api) {
        api
          ..on('GET $_base', [_library(1, '电影')])
          ..on('GET $_base/metadata-providers', [
            _metadataProvider('builtin.tmdb', 'TMDB 元数据', builtIn: true),
            _metadataProvider('org.example.douban', '豆瓣'),
          ])
          ..on('GET $_base/1/settings', {'metadata_language': 'zh-CN'})
          ..on('GET $_base/1/downloaders', {
            'metadata': [
              {
                'id': 'builtin.tmdb',
                'name': 'TMDB 元数据',
                'enabled': true,
                'plugin_enabled': true,
                'can_match': true,
              },
            ],
            'images': <Object>[],
            'subtitles': <Object>[],
            'subtitle_languages': <Object>[],
            'subtitle_skip_if_audio': false,
          });
      });

      await tester.tap(find.text('新建资料库'));
      await tester.pumpAndSettle();
      expect(find.text('元数据插件'), findsOneWidget);
      expect(find.text('TMDB 元数据（内置）'), findsOneWidget);
      await tester.tap(find.text('取消'));
      await tester.pumpAndSettle();

      // 编辑时下载器在「高级」里管理，通用页只显示元数据语言
      await tester.tap(find.text('电影').first);
      await tester.pumpAndSettle();
      expect(find.text('简体中文'), findsOneWidget);
      expect(find.text('元数据插件'), findsNothing);
      await tester.tap(find.text('高级'));
      await tester.pumpAndSettle();
      expect(find.text('元数据下载器'), findsOneWidget);
      expect(find.text('TMDB 元数据'), findsOneWidget);
      expect(api.requests, isNot(contains('PATCH $_base/1')));
    });

    testWidgets('the general tab saves the metadata language', (tester) async {
      final api = await pumpAdmin(tester, const LibrariesPage(), (api) {
        api
          ..on('GET $_base', [_library(1, '电影')])
          ..on('GET $_base/1/settings', {'metadata_language': 'zh-CN'})
          ..on('PUT $_base/1/settings/metadata_language', {
            'metadata_language': 'en-US',
          });
      });
      await tester.tap(find.text('电影').first);
      await tester.pumpAndSettle();
      expect(find.text('简体中文'), findsOneWidget);
      await tester.tap(find.text('简体中文'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('英语').last);
      await tester.pumpAndSettle();
      await tester.tap(find.text('保存'));
      await tester.pumpAndSettle();

      expect(api.bodies['PUT $_base/1/settings/metadata_language'], {
        'value': 'en-US',
      });
      // 名称、目录都没改，不发 PATCH
      expect(api.requests, isNot(contains('PATCH $_base/1')));
      expect(find.byType(AlertDialog), findsNothing);
    });

    testWidgets('the advanced tab saves behavior switches and lists settings', (
      tester,
    ) async {
      final api = await pumpAdmin(tester, const LibrariesPage(), (api) {
        api
          ..on('GET $_base', [_library(1, '电影')])
          ..on('GET $_base/1/settings', {
            'metadata_language': 'zh-CN',
            'prefer_nfo': 'false',
          })
          ..on('GET $_base/1/downloaders', {
            'metadata': [
              {
                'id': 'builtin.tmdb',
                'name': 'TMDB 元数据',
                'enabled': true,
                'plugin_enabled': true,
                'can_match': true,
              },
            ],
            'images': <Object>[],
            'subtitles': <Object>[],
            'subtitle_languages': ['zh-Hans'],
            'subtitle_skip_if_audio': false,
          })
          ..on('PUT $_base/1/settings/prefer_nfo', {'prefer_nfo': 'true'});
      });
      await tester.tap(find.text('电影').first);
      await tester.pumpAndSettle();
      await tester.tap(find.text('高级'));
      await tester.pumpAndSettle();
      expect(find.text('元数据下载器'), findsOneWidget);
      expect(find.text('图片获取器'), findsOneWidget);
      expect(find.text('字幕下载器'), findsOneWidget);
      expect(find.text('默认字幕语言'), findsOneWidget);
      expect(find.text('字幕下载语言'), findsOneWidget);

      await tester.tap(find.text('优先读取 NFO'));
      await tester.pumpAndSettle();
      expect(api.bodies['PUT $_base/1/settings/prefer_nfo'], {'value': 'true'});
      expect(find.text('已保存「优先读取 NFO」'), findsOneWidget);
    });

    testWidgets('delete asks for confirmation', (tester) async {
      final api = await pumpAdmin(tester, const LibrariesPage(), (api) {
        api.on('GET $_base', [_library(1, '电影')]);
        api.on('DELETE $_base/1', null, status: 204);
      });

      await tester.tap(find.byTooltip('管理 电影'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('删除'));
      await tester.pumpAndSettle();
      expect(find.text('删除资料库「电影」？'), findsOneWidget);
      await tester.tap(find.text('取消'));
      await tester.pumpAndSettle();
      expect(api.requests, isNot(contains('DELETE $_base/1')));

      await tester.tap(find.byTooltip('管理 电影'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('删除'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(FilledButton, '删除'));
      await tester.pumpAndSettle();
      expect(api.requests, contains('DELETE $_base/1'));
      expect(find.text('已删除资料库「电影」'), findsOneWidget);
    });

    testWidgets('move down from the menu saves the new order', (tester) async {
      final api = await pumpAdmin(tester, const LibrariesPage(), (api) {
        api.on('GET $_base', [_library(1, '电影'), _library(2, '剧集')]);
        api.on('PUT $_base/order', []);
      });

      await tester.tap(find.byTooltip('管理 电影'));
      await tester.pumpAndSettle();
      expect(find.text('上移'), findsNothing);
      await tester.tap(find.text('下移'));
      await tester.pumpAndSettle();

      expect(api.bodies['PUT $_base/order'], {
        'ids': [2, 1],
      });
    });

    testWidgets('sync a single library from the menu', (tester) async {
      final api = await pumpAdmin(tester, const LibrariesPage(), (api) {
        api.on('GET $_base', [_library(1, '电影')]);
        api.on('POST $_sync', _job(status: 'running'), status: 202);
      });

      await tester.tap(find.byTooltip('管理 电影'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('同步此资料库'));
      await tester.pumpAndSettle();

      expect(api.bodies['POST $_sync'], {'library_id': 1});
      expect(find.text('已开始同步「电影」'), findsOneWidget);
    });
  });

  group('sync page', () {
    void idle(FakeAdminApi api) {
      api.on('GET $_sync/current', {'job': null});
      api.on('GET $_sync/schedule', {
        'times': <String>[],
        'timezone': 'Asia/Shanghai',
        'next_run_at': null,
      });
      api.on('GET $_sync/jobs', []);
      api.on('GET $_sync/held-deletions', []);
    }

    testWidgets('idle page offers a manual sync and explains schedule', (
      tester,
    ) async {
      await pumpAdmin(tester, const SyncPage(), idle);
      expect(find.text('同步全部资料库'), findsOneWidget);
      // 自动同步默认收起，标题下概括当前设置
      expect(find.text('未设置定时同步'), findsOneWidget);
      expect(find.text('未设置，只在手动触发时同步。'), findsNothing);
      await expandAutoSync(tester);
      expect(find.text('未设置，只在手动触发时同步。'), findsOneWidget);
      expect(find.text('还没有同步记录'), findsOneWidget);
    });

    testWidgets('history shows stats, errors and problems', (tester) async {
      await pumpAdmin(tester, const SyncPage(), (api) {
        idle(api);
        api.on('GET $_sync/jobs', [
          _job(id: 2, status: 'failed', error: '来源登录已失效，请重新登录'),
          _job(
            stats: {
              'added': 3,
              'renamed': 1,
              'failed': 1,
              'paused': 1,
              'problems': ['「电影」的目录「/影视/电影」在来源中不存在，已跳过'],
            },
          ),
        ]);
      });

      expect(find.text('全部资料库 · 失败'), findsOneWidget);
      expect(find.textContaining('新增 3 · 改名 1 · 问题 1'), findsOneWidget);
      expect(find.textContaining('用时 2 分 5 秒'), findsNWidgets(2));
      await tester.tap(find.text('全部资料库 · 完成'));
      await tester.pumpAndSettle();
      expect(find.textContaining('在来源中不存在'), findsOneWidget);
      expect(find.text('因来源限流暂停 1 次'), findsOneWidget);
    });

    testWidgets('matching phase and match counts are labelled', (tester) async {
      await pumpAdmin(tester, const SyncPage(), (api) {
        idle(api);
        api.on('GET $_sync/current', {
          'job': _job(status: 'running', phase: 'matching', done: 3, total: 8),
        });
        api.on('GET $_sync/jobs', [
          _job(
            stats: {'matched': 5, 'unmatched': 1, 'ignored': 2, 'waiting': 4},
          ),
        ]);
      });

      expect(find.text('全部资料库 · 匹配元数据'), findsOneWidget);
      expect(find.text('3 / 8 个作品'), findsOneWidget);
      expect(
        find.textContaining('匹配 5 · 未匹配 1 · 忽略 2 · 等待匹配 4'),
        findsOneWidget,
      );
      // 卸载页面，停止轮询
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('a started sync shows progress and refreshes when done', (
      tester,
    ) async {
      final current = <Object?>[
        null,
        _job(status: 'running', phase: 'exporting', done: 1, total: 2),
        _job(
          status: 'running',
          phase: 'exporting',
          done: 1,
          total: 2,
          waitingUntil: DateTime.now()
              .add(const Duration(minutes: 30))
              .toUtc()
              .toIso8601String(),
          waitingReason: 'rate_limited',
        ),
        null,
      ];
      final api = await pumpAdmin(tester, const SyncPage(), (api) {
        idle(api);
        api.routes['GET $_sync/current'] = (_) => (
          200,
          {'job': current.length > 1 ? current.removeAt(0) : current[0]},
        );
        api.on('POST $_sync', _job(status: 'running'), status: 202);
      });

      await tester.tap(find.text('同步全部资料库'));
      await tester.pumpAndSettle();
      expect(api.bodies['POST $_sync'], {'library_id': null});
      expect(find.text('全部资料库 · 导出目录树'), findsOneWidget);
      expect(find.text('1 / 2 个目录'), findsOneWidget);
      expect(find.text('同步全部资料库'), findsNothing);

      await tester.pump(syncPollInterval);
      await tester.pumpAndSettle();
      expect(find.text('来源限制了请求，同步已暂停'), findsOneWidget);
      expect(find.textContaining('不需要重新登录'), findsOneWidget);

      final before = api.requests.where((r) => r == 'GET $_sync/jobs').length;
      await tester.pump(syncPollInterval);
      await tester.pumpAndSettle();
      expect(find.text('同步全部资料库'), findsOneWidget);
      expect(
        api.requests.where((r) => r == 'GET $_sync/jobs').length,
        before + 1,
      );

      // 任务结束后停止轮询
      final polls = api.requests.where((r) => r == 'GET $_sync/current').length;
      await tester.pump(syncPollInterval * 3);
      expect(
        api.requests.where((r) => r == 'GET $_sync/current').length,
        polls,
      );
    });

    testWidgets('a rejected start shows the server message', (tester) async {
      await pumpAdmin(tester, const SyncPage(), (api) {
        idle(api);
        api.on('POST $_sync', {
          'error': {'code': 'no_library', 'message': '还没有资料库，请先添加资料库'},
        }, status: 400);
      });

      await tester.tap(find.text('同步全部资料库'));
      await tester.pumpAndSettle();
      expect(find.text('还没有资料库，请先添加资料库'), findsOneWidget);
      expect(find.text('同步全部资料库'), findsOneWidget);
    });

    testWidgets('event jobs show their target and purged files', (
      tester,
    ) async {
      await pumpAdmin(tester, const SyncPage(), (api) {
        idle(api);
        api.on('GET $_sync/jobs', [
          _job(
            trigger: 'event',
            scope: 'dir',
            target: '来源变动（2 个目录）',
            stats: {'added': 1, 'purged': 3},
          ),
        ]);
      });

      expect(find.text('来源变动（2 个目录） · 完成'), findsOneWidget);
      expect(find.textContaining('来源变动 · '), findsOneWidget);
      expect(find.textContaining('新增 1 · 删除 3'), findsOneWidget);
    });

    testWidgets('held deletions are confirmed per library', (tester) async {
      var held = <Object>[
        {'library_id': 4, 'library_name': '电影', 'due': 6, 'total': 20},
      ];
      final api = await pumpAdmin(tester, const SyncPage(), (api) {
        idle(api);
        api.routes['GET $_sync/held-deletions'] = (_) => (200, held);
        api.on('POST $_sync/held-deletions/4/confirm', {'deleted': 6});
      });

      expect(find.text('「电影」有 6 个视频等待确认删除'), findsOneWidget);
      await tester.tap(find.text('确认删除'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('取消'));
      await tester.pumpAndSettle();
      expect(
        api.requests,
        isNot(contains('POST $_sync/held-deletions/4/confirm')),
      );

      await tester.tap(find.text('确认删除'));
      await tester.pumpAndSettle();
      held = [];
      await tester.tap(find.widgetWithText(FilledButton, '删除'));
      await tester.pumpAndSettle();
      expect(api.requests, contains('POST $_sync/held-deletions/4/confirm'));
      expect(find.text('已删除 6 个视频'), findsOneWidget);
      expect(find.textContaining('等待确认删除'), findsNothing);
    });

    testWidgets('removing a schedule time saves the rest', (tester) async {
      final api = await pumpAdmin(tester, const SyncPage(), (api) {
        idle(api);
        api.on('GET $_sync/schedule', {
          'times': ['03:30', '15:00'],
          'timezone': 'Asia/Shanghai',
          'next_run_at': '2026-10-01T07:00:00Z',
        });
        api.on('PUT $_sync/schedule', {
          'times': ['15:00'],
          'timezone': 'Asia/Shanghai',
          'next_run_at': '2026-10-01T07:00:00Z',
        });
      });

      expect(find.textContaining('每天 03:30、15:00 同步'), findsOneWidget);
      await expandAutoSync(tester);
      expect(
        find.textContaining('每天按 Asia/Shanghai 时区同步全部资料库'),
        findsOneWidget,
      );
      await tester.tap(find.byTooltip('删除 03:30'));
      await tester.pumpAndSettle();
      expect(api.bodies['PUT $_sync/schedule'], {
        'times': ['15:00'],
      });
    });

    testWidgets('the filling phase is described in entries', (tester) async {
      await pumpAdmin(tester, const SyncPage(), (api) {
        idle(api);
        api.on('GET $_sync/current', {
          'job': _job(status: 'running', phase: 'filling', done: 3, total: 8),
        });
      });
      expect(find.text('全部资料库 · 补充元数据和图片'), findsOneWidget);
      expect(find.text('3 / 8 个条目'), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
    });
  });
}
