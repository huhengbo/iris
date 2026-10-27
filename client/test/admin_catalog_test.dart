import 'package:iris/core/api/api_error.dart';
import 'package:iris/core/server.dart';
import 'package:iris/features/admin/catalog_api.dart';
import 'package:iris/features/admin/item_admin_page.dart';
import 'package:iris/features/admin/pending_page.dart';
import 'package:iris/features/admin/settings_page.dart';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/fake_api.dart';

const _admin = '/api/admin';
const _page = 'offset=0&limit=50';

Map<String, dynamic> _summary({
  String? problem,
  String? action,
  int waiting = 0,
  int unmatched = 0,
  int unplaced = 0,
  int ignored = 0,
}) => {
  'metadata_problem': problem,
  'metadata_action': action,
  'waiting': waiting,
  'unmatched': unmatched,
  'unplaced': unplaced,
  'ignored': ignored,
};

Map<String, dynamic> _pageOf(List<Map<String, dynamic>> items) => {
  'items': items,
  'total': items.length,
};

Map<String, dynamic> _filesPage(
  List<Map<String, dynamic>> items, {
  List<String> reasons = const [],
}) => {..._pageOf(items), 'reasons': reasons};

Map<String, dynamic> _batchReport({int succeeded = 1, int failed = 0}) => {
  'task_id': 1,
  'requested': succeeded + failed,
  'succeeded': succeeded,
  'failed': failed,
  'unprocessed': 0,
  'results': [
    for (var i = 0; i < succeeded; i++) {'id': 7, 'item_id': 5, 'error': null},
    for (var i = 0; i < failed; i++)
      {
        'id': 999,
        'item_id': null,
        'error': {'code': 'not_found', 'message': '作品不存在'},
      },
  ],
};

Map<String, dynamic> _work() => {
  'id': 7,
  'library_id': 1,
  'library_name': '电影',
  'kind': 'movie',
  'path': '/影视/电影/奇怪的片名 (2021)',
  'title': '奇怪的片名',
  'year': 2021,
  'state': 'unmatched',
  'manual': false,
  'reason': 'TMDB 上有 2 个可能的结果，无法确定',
  'candidates': [
    {
      'provider_id': 'builtin.tmdb',
      'remote_id': '1001',
      'title': '奇怪的片名',
      'original_title': 'Strange Title',
      'year': 2021,
      'url': 'https://www.themoviedb.org/movie/1001',
      'score': 100,
      'reasons': ['片名完全一致', '年份一致', '搜索结果唯一'],
    },
  ],
  'file_count': 1,
  'item': null,
};

Map<String, dynamic> _file({String kind = 'series'}) => {
  'id': 31,
  'library_id': 2,
  'library_name': '剧集',
  'kind': kind,
  'path': '/影视/剧集/三体/花絮合集.mkv',
  'size': 1610612736,
  'reason': '无法从文件名识别季号和集号',
  'item': {
    'id': 5,
    'provider_id': 'builtin.tmdb',
    'remote_id': '108545',
    'title': '三体',
  },
};

Map<String, dynamic> _image(String name) => {
  'width': 342,
  'url': '/api/images/$name',
};

Map<String, dynamic> _item({List<Map<String, dynamic>>? versions}) => {
  'id': 5,
  'library_id': 1,
  'library_name': '电影',
  'kind': 'movie',
  'provider_id': 'builtin.tmdb',
  'remote_id': '535167',
  'title': '流浪地球',
  'original_title': '流浪地球',
  'year': 2019,
  'poster': [_image('a_342.jpg')],
  'version_count': 2,
  'added_at': '2026-10-01T00:00:00Z',
  'provider_name': 'TMDB 元数据',
  'external_url': 'https://www.themoviedb.org/movie/535167',
  'overview': '太阳即将毁灭。',
  'release_date': '2019-02-05',
  'rating': 7.3,
  'genres': ['科幻', '冒险'],
  'runtime': 125,
  'backdrop': <Object>[],
  'thumb': <Object>[],
  'logo': <Object>[],
  'scraped_at': '2026-10-01T02:00:00Z',
  'works': [
    {'id': 3, 'path': '/影视/电影/流浪地球 (2019)', 'manual': true},
  ],
  'versions':
      versions ??
      [
        {
          'id': 11,
          'media_file_id': 21,
          'path': '/影视/电影/流浪地球 (2019)/流浪地球.2160p.mkv',
          'size': 64424509440,
          'state': 'active',
          'label': '4K · 60 GB',
          'manual': false,
          'season': null,
          'episode': null,
        },
        {
          'id': 12,
          'media_file_id': 22,
          'path': '/影视/电影/流浪地球 (2019)/流浪地球.1080p.mkv',
          'size': 8589934592,
          'state': 'missing',
          'label': '1080p · 8 GB',
          'manual': false,
          'season': null,
          'episode': null,
        },
      ],
};

List<Map<String, dynamic>> _metadata() => [
  {
    'field': 'title',
    'value': '流浪地球',
    'source': 'provider',
    'locked': false,
    'updated_at': '2026-10-01T02:00:00Z',
  },
  {
    'field': 'overview',
    'value': '太阳即将毁灭。',
    'source': 'provider',
    'locked': false,
    'updated_at': '2026-10-01T02:00:00Z',
  },
];

Map<String, dynamic> _settings({String? pluginsProxy, String? publicUrl}) => {
  'server': {'id': 'abc123', 'name': '家里的影院', 'public_url': publicUrl},
  'plugins': {'proxy': pluginsProxy},
  'library': {'extras_min_mb': 200, 'missing_grace_days': 7},
};

void main() {
  group('data access', () {
    test('artwork picks the smallest size covering the display', () {
      final art = Artwork.fromJson([
        {'width': 342, 'url': '/api/images/a_342.jpg'},
        {'width': 780, 'url': '/api/images/a_780.jpg'},
      ], 'http://192.168.1.2:8080');

      expect(art.pick(300), 'http://192.168.1.2:8080/api/images/a_342.jpg');
      expect(art.pick(500), 'http://192.168.1.2:8080/api/images/a_780.jpg');
      expect(art.pick(2000), 'http://192.168.1.2:8080/api/images/a_780.jpg');
      expect(Artwork.none.pick(100), isNull);
    });

    test('server info rejects other products and newer api versions', () async {
      final api = FakeAdminApi();
      final dio = Dio(BaseOptions(baseUrl: 'http://test'))
        ..httpClientAdapter = api;
      Map<String, dynamic> info({String product = 'iris', int v = 1}) => {
        'product': product,
        'server_id': 'abc',
        'server_name': '家里',
        'version': '0.1.0',
        'api_version': v,
      };

      api.on('GET $systemInfoPath', info());
      expect((await fetchServerInfo(dio)).id, 'abc');

      api.on('GET $systemInfoPath', info(product: 'emby'));
      await expectLater(
        fetchServerInfo(dio),
        throwsA(isA<ApiError>().having((e) => e.code, 'code', 'not_iris')),
      );

      api.on('GET $systemInfoPath', info(v: 2));
      await expectLater(
        fetchServerInfo(dio),
        throwsA(
          isA<ApiError>().having((e) => e.code, 'code', 'client_outdated'),
        ),
      );
    });

    test('sizes are formatted in binary units', () {
      expect(formatSize(null), '大小未知');
      expect(formatSize(512), '512 B');
      expect(formatSize(1610612736), '1.5 GB');
      expect(formatSize(64424509440), '60.0 GB');
    });
  });

  group('pending page', () {
    void lists(FakeAdminApi api, {Map<String, dynamic>? summary}) {
      api.on('GET $_admin/pending', summary ?? _summary(unmatched: 1));
      api.on('GET $_admin/works?state=unmatched&$_page', _pageOf([_work()]));
      api.on('GET $_admin/files?status=unplaced&$_page', _filesPage([_file()]));
      api.on('GET $_admin/files?status=ignored&$_page', _filesPage([]));
    }

    testWidgets('explains why the metadata plugin is unavailable', (
      tester,
    ) async {
      await pumpAdmin(
        tester,
        const PendingPage(),
        (api) => lists(
          api,
          summary: _summary(
            problem: '还没有配置 TMDB Key，在插件 › TMDB 元数据里填写',
            action: '/settings/manage/plugins/builtin.tmdb',
            waiting: 12,
          ),
        ),
      );

      expect(find.text('元数据插件暂时不能用'), findsOneWidget);
      expect(find.textContaining('还没有配置 TMDB Key'), findsOneWidget);
      expect(find.textContaining('12 个作品在等待'), findsOneWidget);
      expect(find.text('去处理'), findsOneWidget);
    });

    testWidgets('assign an unmatched work to a candidate', (tester) async {
      final api = await pumpAdmin(tester, const PendingPage(), (api) {
        lists(api);
        api.on('POST $_admin/works/7/assign', _item());
      });

      expect(find.text('未匹配 1'), findsOneWidget);
      expect(find.text('奇怪的片名（2021）'), findsOneWidget);
      expect(find.text('TMDB 上有 2 个可能的结果，无法确定'), findsOneWidget);

      await tester.tap(find.bySemanticsLabel('指定 奇怪的片名（2021）'));
      await tester.pumpAndSettle();
      expect(find.text('候选'), findsOneWidget);
      expect(find.text('匹配度 100'), findsOneWidget);
      expect(find.textContaining('片名完全一致，年份一致，搜索结果唯一'), findsOneWidget);
      await tester.tap(find.text('奇怪的片名（2021）').last);
      await tester.pumpAndSettle();

      expect(api.bodies['POST $_admin/works/7/assign'], {
        'provider_id': 'builtin.tmdb',
        'remote_id': '1001',
      });
      expect(find.text('已指定为「奇怪的片名（2021）」'), findsOneWidget);
      // 指定后刷新列表和计数
      expect(
        api.requests.where((r) => r == 'GET $_admin/pending').length,
        greaterThan(1),
      );
    });

    testWidgets('selects works and reports a batch assignment', (tester) async {
      final api = await pumpAdmin(tester, const PendingPage(), (api) {
        lists(api);
        api.on('POST $_admin/works/batch-assign', _batchReport());
      });

      await tester.tap(find.byType(Checkbox).first);
      await tester.pumpAndSettle();
      expect(find.text('已选 1 项'), findsOneWidget);

      await tester.tap(find.text('批量指定'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('奇怪的片名（2021）').last);
      await tester.pumpAndSettle();

      expect(api.bodies['POST $_admin/works/batch-assign'], {
        'work_ids': [7],
        'provider_id': 'builtin.tmdb',
        'remote_id': '1001',
      });
      expect(find.text('批量指定完成：成功 1 项'), findsOneWidget);
    });

    testWidgets('can retry automatic matching for selected works', (
      tester,
    ) async {
      final api = await pumpAdmin(tester, const PendingPage(), (api) {
        lists(api);
        api.on('POST $_admin/works/batch-rematch', _batchReport());
      });

      await tester.tap(find.byType(Checkbox).first);
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('批量重新匹配'));
      await tester.pumpAndSettle();

      expect(api.bodies['POST $_admin/works/batch-rematch'], {
        'work_ids': [7],
      });
      expect(find.text('批量重新匹配完成：成功 1 项'), findsOneWidget);
    });

    testWidgets('search metadata and assign a file to an episode', (
      tester,
    ) async {
      final api = await pumpAdmin(tester, const PendingPage(), (api) {
        lists(api, summary: _summary(unplaced: 1));
        api.on(
          'GET $_admin/metadata/search?kind=series&query=三体&library_id=2',
          [
            {
              'provider_id': 'builtin.tmdb',
              'remote_id': '108545',
              'title': '三体',
              'original_title': null,
              'year': 2023,
              'overview': '物理学家汪淼被卷入一起神秘事件。',
              'url': 'https://www.themoviedb.org/tv/108545',
            },
          ],
        );
        api.on('POST $_admin/files/31/assign', _item());
      });

      await tester.tap(find.text('未归入 1'));
      await tester.pumpAndSettle();
      expect(find.text('花絮合集.mkv'), findsOneWidget);
      expect(find.textContaining('1.5 GB · 所在作品：三体'), findsOneWidget);

      await tester.tap(find.bySemanticsLabel('指定 花絮合集.mkv'));
      await tester.pumpAndSettle();
      await tester.testTextInput.receiveAction(TextInputAction.search);
      await tester.pumpAndSettle();
      await tester.tap(find.text('三体（2023）'));
      await tester.pumpAndSettle();

      // 集号必填
      await tester.tap(find.widgetWithText(FilledButton, '指定'));
      await tester.pumpAndSettle();
      expect(find.textContaining('集号从 1 开始'), findsOneWidget);

      await tester.enterText(find.widgetWithText(TextField, '季号'), '0');
      await tester.enterText(find.widgetWithText(TextField, '集号'), '3');
      await tester.tap(find.widgetWithText(FilledButton, '指定'));
      await tester.pumpAndSettle();

      expect(api.bodies['POST $_admin/files/31/assign'], {
        'provider_id': 'builtin.tmdb',
        'remote_id': '108545',
        'season': 0,
        'episode': 3,
      });
    });

    testWidgets('restores ignored files and can batch ignore unplaced files', (
      tester,
    ) async {
      final api = await pumpAdmin(tester, const PendingPage(), (api) {
        lists(api, summary: _summary(unplaced: 1, ignored: 1));
        api.on(
          'GET $_admin/files?status=ignored&$_page',
          _filesPage([_file()]),
        );
        api.on('POST $_admin/files/31/restore', null, status: 204);
        api.on('POST $_admin/files/batch-ignore', _batchReport());
      });

      await tester.tap(find.text('已忽略 1'));
      await tester.pumpAndSettle();
      await tester.tap(find.bySemanticsLabel('恢复 花絮合集.mkv'));
      await tester.pumpAndSettle();
      expect(api.requests, contains('POST $_admin/files/31/restore'));

      await tester.tap(find.text('未归入 1'));
      await tester.pumpAndSettle();
      await tester.tap(find.byType(Checkbox).first);
      await tester.pumpAndSettle();
      await tester.tap(find.text('批量忽略'));
      await tester.pumpAndSettle();

      expect(api.bodies['POST $_admin/files/batch-ignore'], {
        'media_file_ids': [31],
      });
    });

    testWidgets('filters files by reason', (tester) async {
      final api = await pumpAdmin(tester, const PendingPage(), (api) {
        lists(api, summary: _summary(unplaced: 1));
        api.on(
          'GET $_admin/files?status=unplaced&$_page',
          _filesPage([_file()], reasons: ['已从条目中拆出，等待重新指定', '无法从文件名识别季号和集号']),
        );
        api.on(
          'GET $_admin/files?status=unplaced&reason=已从条目中拆出，等待重新指定&$_page',
          _filesPage([], reasons: ['已从条目中拆出，等待重新指定', '无法从文件名识别季号和集号']),
        );
      });

      await tester.tap(find.text('未归入 1'));
      await tester.pumpAndSettle();
      expect(find.text('全部'), findsOneWidget);
      await tester.tap(find.widgetWithText(ChoiceChip, '已从条目中拆出，等待重新指定'));
      await tester.pumpAndSettle();

      expect(
        api.requests,
        contains(
          'GET $_admin/files?status=unplaced&reason=已从条目中拆出，等待重新指定&$_page',
        ),
      );
      expect(find.text('没有符合该原因的视频'), findsOneWidget);

      await tester.tap(find.widgetWithText(ChoiceChip, '全部'));
      await tester.pumpAndSettle();
      expect(find.text('花絮合集.mkv'), findsOneWidget);
    });

    testWidgets('shows the server message when records are already in a task', (
      tester,
    ) async {
      await pumpAdmin(tester, const PendingPage(), (api) {
        lists(api, summary: _summary(unplaced: 1));
        api.on('POST $_admin/files/batch-ignore', {
          'error': {
            'code': 'batch_in_progress',
            'message': '其中部分记录正在被任务 #3 处理，请等它结束后再提交',
          },
        }, status: 409);
      });

      await tester.tap(find.text('未归入 1'));
      await tester.pumpAndSettle();
      await tester.tap(find.byType(Checkbox).first);
      await tester.pumpAndSettle();
      await tester.tap(find.text('批量忽略'));
      await tester.pumpAndSettle();

      expect(find.textContaining('正在被任务 #3 处理'), findsOneWidget);
      // 失败后按钮恢复可用，选择保留
      expect(find.text('已选 1 项'), findsOneWidget);
    });

    testWidgets('assign by an id typed into the search', (tester) async {
      final api = await pumpAdmin(tester, const PendingPage(), (api) {
        lists(api);
        api.on('POST $_admin/works/7/assign', _item());
      });

      await tester.tap(find.bySemanticsLabel('指定 奇怪的片名（2021）'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), '424242');
      await tester.pumpAndSettle();
      await tester.tap(find.text('使用条目 ID 424242'));
      await tester.pumpAndSettle();

      expect(api.bodies['POST $_admin/works/7/assign'], {
        'remote_id': '424242',
      });
    });

    testWidgets('shows the server message when assigning fails', (
      tester,
    ) async {
      await pumpAdmin(tester, const PendingPage(), (api) {
        lists(api);
        api.on('POST $_admin/works/7/assign', {
          'error': {'code': 'metadata_not_found', 'message': '没有这个条目'},
        }, status: 404);
      });

      await tester.tap(find.bySemanticsLabel('指定 奇怪的片名（2021）'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('奇怪的片名（2021）').last);
      await tester.pumpAndSettle();

      expect(find.text('没有这个条目'), findsOneWidget);
    });

    Map<String, dynamic> task(String status, {String? error}) => {
      'id': 9,
      'kind': 'delete_pending',
      'phase': 'deleting_cloud',
      'status': status,
      'progress_done': 1,
      'progress_total': 1,
      'cancel_requested': false,
      'error': error,
      'started_at': '2026-10-03T08:00:00Z',
      'finished_at': '2026-10-03T08:00:01Z',
    };

    testWidgets('deletes a work together with its files on a source', (
      tester,
    ) async {
      final api = await pumpAdmin(tester, const PendingPage(), (api) {
        lists(api);
        api.on(
          'POST $_admin/pending/delete?confirm=true',
          null,
          status: 202,
          headers: {'X-Task-Id': '9'},
        );
        api.on('GET /api/admin/tasks/9', task('succeeded'));
      });

      await tester.tap(find.byTooltip('删除').first);
      await tester.pumpAndSettle();
      expect(find.text('删除「奇怪的片名（2021）」？'), findsOneWidget);
      expect(find.textContaining('/影视/电影/奇怪的片名 (2021)'), findsWidgets);
      await tester.tap(find.text('取消'));
      await tester.pumpAndSettle();
      expect(
        api.bodies,
        isNot(contains('POST $_admin/pending/delete?confirm=true')),
      );

      await tester.tap(find.byTooltip('删除').first);
      await tester.pumpAndSettle();
      await tester.tap(find.text('删除'));
      await tester.pumpAndSettle();

      expect(api.bodies['POST $_admin/pending/delete?confirm=true'], {
        'work_ids': [7],
        'media_file_ids': <int>[],
      });
      expect(api.requests, contains('GET /api/admin/tasks/9'));
      expect(find.text('已删除，来源上的文件已清理'), findsOneWidget);
      // 删除结束后刷新列表和计数
      expect(
        api.requests.where((r) => r == 'GET $_admin/pending').length,
        greaterThan(1),
      );
    });

    testWidgets('batch deletes files and reports a failed task', (
      tester,
    ) async {
      final api = await pumpAdmin(tester, const PendingPage(), (api) {
        lists(api, summary: _summary(unplaced: 1));
        api.on(
          'POST $_admin/pending/delete?confirm=true',
          null,
          status: 202,
          headers: {'X-Task-Id': '9'},
        );
        api.on('GET /api/admin/tasks/9', task('failed', error: '删除请求失败'));
      });

      await tester.tap(find.text('未归入 1'));
      await tester.pumpAndSettle();
      await tester.tap(find.byType(Checkbox).first);
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('批量删除'));
      await tester.pumpAndSettle();
      expect(find.text('删除 1 个视频？'), findsOneWidget);
      await tester.tap(find.text('删除'));
      await tester.pumpAndSettle();

      expect(api.bodies['POST $_admin/pending/delete?confirm=true'], {
        'work_ids': <int>[],
        'media_file_ids': [31],
      });
      expect(find.text('删除没有完成：删除请求失败。可以在后台任务里重试'), findsOneWidget);
      expect(find.text('已选 1 项'), findsNothing);
    });

    testWidgets('empty lists explain what would appear', (tester) async {
      await pumpAdmin(tester, const PendingPage(), (api) {
        api.on('GET $_admin/pending', _summary());
        api.on('GET $_admin/works?state=unmatched&$_page', _pageOf([]));
      });
      expect(find.text('没有未匹配的作品'), findsOneWidget);
    });
  });

  group('item admin page', () {
    testWidgets('shows the match, works and versions', (tester) async {
      await pumpAdmin(
        tester,
        const ItemAdminPage(id: 5),
        (api) => api.on('GET $_admin/items/5', _item()),
      );

      expect(find.text('流浪地球'), findsWidgets);
      expect(find.text('电影 · 电影 · 2019 · 125 分钟 · 7.3'), findsOneWidget);
      expect(
        find.text('https://www.themoviedb.org/movie/535167'),
        findsOneWidget,
      );
      expect(find.text('手动指定'), findsOneWidget);
      expect(find.text('版本 2'), findsOneWidget);
      expect(find.text('4K · 60 GB'), findsOneWidget);
      expect(find.text('来源上已找不到'), findsOneWidget);
    });

    testWidgets('chooses a provider artwork candidate and locks it', (
      tester,
    ) async {
      final api = await pumpAdmin(tester, const ItemAdminPage(id: 5), (api) {
        api
          ..on('GET $_admin/items/5', _item())
          ..on('GET $_admin/items/5/artwork/poster', {
            'kind': 'poster',
            'current': '/old.jpg',
            'candidates': ['/new.jpg'],
          })
          ..on('PUT $_admin/items/5/artwork/poster', {
            'kind': 'poster',
            'current': '/new.jpg',
            'candidates': ['/new.jpg'],
          });
      });

      await tester.tap(find.widgetWithText(OutlinedButton, '海报'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('/new.jpg'));
      await tester.pumpAndSettle();

      expect(api.bodies['PUT $_admin/items/5/artwork/poster'], {
        'source': 'provider',
        'value': '/new.jpg',
      });
      expect(find.text('海报已更新并锁定'), findsOneWidget);
    });

    testWidgets('shows metadata source and can lock a manually edited field', (
      tester,
    ) async {
      final api = await pumpAdmin(tester, const ItemAdminPage(id: 5), (api) {
        api.on('GET $_admin/items/5', _item());
        api.on('GET $_admin/items/5/metadata', _metadata());
        api.on('PATCH $_admin/items/5/metadata/title', null, status: 204);
      });

      expect(find.textContaining('TMDB'), findsWidgets);
      await tester.tap(find.byTooltip('编辑标题'));
      await tester.pumpAndSettle();
      await tester.enterText(find.widgetWithText(TextField, '标题'), '我的片名');
      await tester.tap(find.text('锁定此字段'));
      await tester.tap(find.widgetWithText(FilledButton, '保存'));
      await tester.pumpAndSettle();

      expect(api.bodies['PATCH $_admin/items/5/metadata/title'], {
        'value': '我的片名',
        'locked': true,
      });
      expect(find.text('标题已保存并锁定'), findsOneWidget);
    });

    testWidgets('unlocks a locked field to restore automatic updates', (
      tester,
    ) async {
      final api = await pumpAdmin(tester, const ItemAdminPage(id: 5), (api) {
        api.on('GET $_admin/items/5', _item());
        api.on('GET $_admin/items/5/metadata', [
          {..._metadata().first, 'source': 'manual', 'locked': true},
        ]);
        api.on('DELETE $_admin/items/5/metadata/title', null, status: 204);
      });

      await tester.tap(find.byTooltip('解锁标题，恢复自动更新'));
      await tester.pumpAndSettle();

      expect(api.requests, contains('DELETE $_admin/items/5/metadata/title'));
      expect(find.text('标题已解锁，下次刷新会自动更新'), findsOneWidget);
    });

    testWidgets('re-reading files reports what changed', (tester) async {
      final api = await pumpAdmin(tester, const ItemAdminPage(id: 5), (api) {
        api.on('GET $_admin/items/5', _item());
        api.on('POST $_admin/items/5/reenrich', {'updated': 1, 'missing': 1});
      });

      await tester.tap(find.text('重新读取文件'));
      await tester.pumpAndSettle();

      expect(find.text('更新了 1 个文件，1 个文件在来源上已找不到'), findsOneWidget);
      expect(api.requests.where((r) => r == 'GET $_admin/items/5').length, 2);
    });

    testWidgets('syncing the item directories starts a background sync', (
      tester,
    ) async {
      final api = await pumpAdmin(tester, const ItemAdminPage(id: 5), (api) {
        api.on('GET $_admin/items/5', _item());
        api.on('POST $_admin/items/5/sync', {'id': 9}, status: 202);
      });

      await tester.tap(find.text('同步此目录'));
      await tester.pumpAndSettle();

      expect(api.requests, contains('POST $_admin/items/5/sync'));
      expect(find.textContaining('可在「同步」页查看进度'), findsOneWidget);
      // 同步在后台进行，不重新加载条目
      expect(api.requests.where((r) => r == 'GET $_admin/items/5').length, 1);
    });

    testWidgets('automatic rematch without a result leaves the page', (
      tester,
    ) async {
      final api = await pumpAdmin(tester, const ItemAdminPage(id: 5), (api) {
        api.on('GET $_admin/items/5', _item());
        api.on('POST $_admin/items/5/rematch', {'item_id': null});
      });

      await tester.tap(find.text('修正匹配'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('重新自动匹配'));
      await tester.pumpAndSettle();
      expect(find.text('重新自动匹配？'), findsOneWidget);
      await tester.tap(find.widgetWithText(FilledButton, '重新匹配'));
      await tester.pumpAndSettle();

      expect(api.bodies['POST $_admin/items/5/rematch'], <String, dynamic>{});
      expect(find.text('没有自动匹配上，作品已进入待处理'), findsOneWidget);
    });

    testWidgets('detach a version after confirming', (tester) async {
      final api = await pumpAdmin(tester, const ItemAdminPage(id: 5), (api) {
        api.on('GET $_admin/items/5', _item());
        api.on('POST $_admin/versions/12/detach', null, status: 204);
      });

      await tester.tap(find.byTooltip('流浪地球.1080p.mkv 的操作'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('拆出'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(FilledButton, '拆出'));
      await tester.pumpAndSettle();

      expect(api.requests, contains('POST $_admin/versions/12/detach'));
      expect(find.text('已拆出，可在待处理中重新指定'), findsOneWidget);
    });
  });

  group('settings page', () {
    testWidgets('saves the global plugin proxy, keeping a masked value', (
      tester,
    ) async {
      final api = await pumpAdmin(tester, const PluginProxyPage(), (api) {
        api.on(
          'GET $_admin/settings',
          _settings(pluginsProxy: 'http://user:***@global:7890'),
        );
        api.on(
          'PUT $_admin/settings/plugins',
          _settings(pluginsProxy: 'http://user:***@global:7890'),
        );
      });

      expect(find.text('插件网络代理'), findsOneWidget);
      expect(find.text('http://user:***@global:7890'), findsOneWidget);

      final field = find.widgetWithText(TextField, '全局代理地址（可选）');
      await tester.ensureVisible(field);
      final save = find.descendant(
        of: find.ancestor(of: field, matching: find.byType(Column)).first,
        matching: find.widgetWithText(FilledButton, '保存'),
      );

      // 打码地址原样传回，服务端视为没有修改密码
      await tester.tap(save);
      await tester.pumpAndSettle();
      expect(api.bodies['PUT $_admin/settings/plugins'], {
        'proxy': 'http://user:***@global:7890',
      });

      await tester.enterText(field, 'http://127.0.0.1:7890');
      await tester.tap(save);
      await tester.pumpAndSettle();
      expect(api.bodies['PUT $_admin/settings/plugins'], {
        'proxy': 'http://127.0.0.1:7890',
      });
      expect(find.text('已保存，插件代理已更新'), findsOneWidget);
    });

    testWidgets('clears the global plugin proxy', (tester) async {
      final api = await pumpAdmin(tester, const PluginProxyPage(), (api) {
        api.on(
          'GET $_admin/settings',
          _settings(pluginsProxy: 'http://user:***@global:7890'),
        );
        api.on('PUT $_admin/settings/plugins', _settings());
      });

      final clear = find.text('清除代理');
      await tester.ensureVisible(clear);
      await tester.tap(clear);
      await tester.pumpAndSettle();

      expect(api.bodies['PUT $_admin/settings/plugins'], {'proxy': ''});
      expect(find.text('已清除插件代理'), findsOneWidget);
    });

    testWidgets('each server setting lives on its own page', (tester) async {
      await pumpAdmin(tester, const PluginProxyPage(), (api) {
        api.on('GET $_admin/settings', _settings());
      });
      expect(find.text('插件网络代理'), findsOneWidget);
      expect(find.text('资料库默认值'), findsNothing);
      expect(find.text('对外访问地址'), findsNothing);

      await pumpAdmin(tester, const LibraryDefaultsPage(), (api) {
        api.on('GET $_admin/settings', _settings());
      });
      expect(find.text('资料库默认值'), findsOneWidget);
      expect(find.text('插件网络代理'), findsNothing);
      expect(find.text('服务器名称'), findsNothing);
    });

    testWidgets('public URL is saved together with the server name', (
      tester,
    ) async {
      final api = await pumpAdmin(tester, const RemoteAccessPage(), (api) {
        api.on('GET $_admin/settings', _settings());
        api.on('PUT $_admin/settings/server', {
          'error': {
            'code': 'invalid_public_url',
            'message': '对外访问地址要以 http:// 或 https:// 开头',
          },
        }, status: 400);
      });

      final field = find.widgetWithText(TextField, '对外访问地址');
      await tester.ensureVisible(field);
      await tester.enterText(field, 'movie.example.com');
      final save = find.widgetWithText(FilledButton, '保存').last;
      await tester.tap(save);
      await tester.pumpAndSettle();
      expect(api.bodies['PUT $_admin/settings/server'], {
        'name': '家里的影院',
        'public_url': 'movie.example.com',
      });
      expect(find.text('对外访问地址要以 http:// 或 https:// 开头'), findsOneWidget);

      api.on(
        'PUT $_admin/settings/server',
        _settings(publicUrl: 'https://movie.example.com'),
      );
      await tester.enterText(field, ' https://movie.example.com/ ');
      await tester.tap(save);
      await tester.pumpAndSettle();
      expect(api.bodies['PUT $_admin/settings/server'], {
        'name': '家里的影院',
        'public_url': 'https://movie.example.com/',
      });
      expect(find.text('已保存'), findsOneWidget);
    });

    testWidgets('library settings are range checked', (tester) async {
      final api = await pumpAdmin(tester, const LibraryDefaultsPage(), (api) {
        api.on('GET $_admin/settings', _settings());
        api.on('PUT $_admin/settings/library', _settings());
      });

      final field = find.widgetWithText(TextField, '最小体积');
      await tester.enterText(field, '99999');
      await tester.ensureVisible(field);
      final save = find.descendant(
        of: find.ancestor(of: field, matching: find.byType(Column)).first,
        matching: find.widgetWithText(FilledButton, '保存'),
      );
      await tester.tap(save);
      await tester.pumpAndSettle();
      expect(find.textContaining('0 到 10240'), findsOneWidget);

      await tester.enterText(field, '300');
      final grace = find.widgetWithText(TextField, '失效文件保留');
      await tester.enterText(grace, '0');
      await tester.tap(save);
      await tester.pumpAndSettle();
      expect(find.textContaining('1 到 365'), findsOneWidget);
      expect(api.bodies['PUT $_admin/settings/library'], isNull);

      await tester.enterText(grace, '14');
      await tester.tap(save);
      await tester.pumpAndSettle();
      expect(api.bodies['PUT $_admin/settings/library'], {
        'extras_min_mb': 300,
        'missing_grace_days': 14,
      });
      expect(find.text('已保存，下次同步时生效'), findsOneWidget);
    });
  });
}
