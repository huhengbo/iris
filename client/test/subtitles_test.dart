import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/core/api/api_error.dart';
import 'package:iris/core/providers.dart';
import 'package:iris/features/admin/item_actions.dart';
import 'package:iris/features/admin/users_api.dart';
import 'package:iris/features/admin/users_page.dart';
import 'package:iris/features/auth/auth_controller.dart';
import 'package:iris/features/detail/stream_rows.dart';
import 'package:iris/features/library/browse_api.dart';
import 'package:iris/features/play/play_api.dart';
import 'package:iris/features/subtitles/edit_subtitles_dialog.dart';
import 'package:iris/features/subtitles/subtitle_search_sheet.dart';
import 'package:iris/features/subtitles/subtitles_api.dart';

import 'support/fake_api.dart';

class _User extends AuthController {
  _User({this.role = 'user', this.allow = true});

  final String role;
  final bool allow;

  @override
  Future<AppUser?> build() async => AppUser(
    id: 1,
    username: 'u',
    role: role,
    disabled: false,
    allowSubtitleDownload: allow,
  );
}

Map<String, dynamic> _providers({
  bool allowed = true,
  bool ready = true,
  String? reason = '还没有填写 API Token',
}) => {
  'allowed': allowed,
  'providers': [
    {
      'id': 'builtin.assrt',
      'name': '射手网 assrt',
      'ready': ready,
      'reason': ready ? null : reason,
    },
  ],
};

Map<String, dynamic> _candidate({
  String remoteId = '123/Movie.zh.srt',
  String label = 'Movie.2024.2160p.zh.srt',
  bool downloaded = false,
}) => {
  'provider_id': 'builtin.assrt',
  'provider_name': '射手网 assrt',
  'remote_id': remoteId,
  'label': label,
  'release': 'Movie.2024.2160p.BluRay',
  'language': 'zh-Hans',
  'format': 'srt',
  'forced': false,
  'hearing_impaired': false,
  'score': 88.5,
  'downloads': 120,
  'uploaded_at': '2026-10-01T10:00:00Z',
  'downloaded': downloaded,
};

Map<String, dynamic> _search({
  List<Map<String, dynamic>> results = const [],
  List<Map<String, dynamic>>? providers,
}) => {
  'version_id': 7,
  'language': 'zh-Hans',
  'providers':
      providers ??
      [
        {
          'id': 'builtin.assrt',
          'name': '射手网 assrt',
          'status': 'ok',
          'message': null,
        },
      ],
  'results': results,
};

Map<String, dynamic> _downloadResult({bool created = true}) => {
  'subtitle': {
    'id': 99,
    'label': 'Movie.zh.srt',
    'language': 'zh-Hans',
    'forced': false,
    'source': 'online',
  },
  'created': created,
  'play': {'path': '/api/subtitles/99/file', 'url': null},
};

Map<String, dynamic> _movieDetail() => {
  'id': 1,
  'library_id': 1,
  'kind': 'movie',
  'title': 'Movie',
  'original_title': null,
  'year': 2024,
  'rating': 8.0,
  'genres': <String>[],
  'runtime': 120,
  'poster': <Object>[],
  'backdrop': <Object>[],
  'watched': false,
  'favorite': false,
  'overview': null,
  'content_rating': null,
  'watchlisted': false,
  'directors': <Object>[],
  'writers': <Object>[],
  'cast': <Object>[],
  'studios': <Object>[],
  'countries': <Object>[],
  'ratings': <Object>[],
  'versions': [
    {
      'id': 1,
      'label': '4K · 60 GB',
      'size': null,
      'subtitles': <Object>[],
      'media': null,
    },
  ],
  'seasons': <Object>[],
  'next_episode': null,
  'progress': null,
};

Map<String, dynamic> _adminPayload({
  List<Map<String, dynamic>>? subtitles,
  List<Map<String, dynamic>>? embedded = const [
    {
      'language': 'en',
      'title': 'English',
      'codec': 'subrip',
      'forced': false,
      'default': false,
    },
  ],
}) => {
  'version': {
    'id': 1,
    'label': '4K · 60 GB',
    'filename': 'Movie.2024.2160p.mkv',
  },
  'subtitles':
      subtitles ??
      [
        {
          'id': 11,
          'source': 'external',
          'label': 'Movie.zh.srt',
          'language': 'zh-Hans',
          'forced': false,
          'hearing_impaired': false,
          'default': true,
          'format': 'srt',
          'external': {
            'rel_path': 'Movie.zh.srt',
            'deletable': true,
            'delete_blocked_reason': null,
          },
          'online': null,
        },
        {
          'id': 12,
          'source': 'online',
          'label': 'Movie.2024.2160p.zh.srt',
          'language': 'zh-Hans',
          'forced': false,
          'hearing_impaired': false,
          'default': false,
          'format': 'srt',
          'external': null,
          'online': {
            'provider_id': 'builtin.assrt',
            'provider_name': '射手网 assrt',
            'release': 'Movie.2024.2160p.BluRay',
            'size': 20480,
            'created_at': '2026-10-01T10:00:00Z',
            'created_by': {'id': 2, 'username': 'alice'},
          },
        },
      ],
  'embedded': embedded,
};

/// 下载请求卡在 Completer 上，用来验证下载中再次点击不会重复提交。
class _GatedDownloadApi extends FakeAdminApi {
  final gate = Completer<void>();

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    if (options.method == 'POST' &&
        options.path == '/api/versions/7/subtitles') {
      await gate.future;
    }
    return super.fetch(options, requestStream, cancelFuture);
  }
}

class _SearchHarness extends StatelessWidget {
  const _SearchHarness({required this.result});

  final ValueNotifier<SubtitleDownloadResult?> result;

  @override
  Widget build(BuildContext context) => Scaffold(
    body: Center(
      child: FilledButton(
        onPressed: () async {
          result.value = await showSubtitleSearchSheet(
            context,
            versionId: 7,
            title: 'Movie',
          );
        },
        child: const Text('打开搜索'),
      ),
    ),
  );
}

Future<(FakeAdminApi, ValueNotifier<SubtitleDownloadResult?>)> _pumpSearch(
  WidgetTester tester, {
  required void Function(FakeAdminApi api) setup,
  String role = 'user',
  bool allow = true,
}) async {
  final result = ValueNotifier<SubtitleDownloadResult?>(null);
  addTearDown(result.dispose);
  final api = await pumpAdmin(
    tester,
    _SearchHarness(result: result),
    (api) {
      api.on('GET /api/me/preferences', {'subtitle_language': 'zh-CN'});
      setup(api);
    },
    overrides: [
      authControllerProvider.overrideWith(
        () => _User(role: role, allow: allow),
      ),
    ],
  );
  return (api, result);
}

Future<void> _openSearch(WidgetTester tester) async {
  await tester.tap(find.text('打开搜索'));
  await tester.pumpAndSettle();
}

void main() {
  group('models and API contract', () {
    test('admin is always allowed to download subtitles', () {
      final denied = AppUser.fromJson({
        'id': 2,
        'username': 'viewer',
        'role': 'user',
        'disabled': false,
        'allow_subtitle_download': false,
      });
      expect(denied.canDownloadSubtitles, isFalse);

      final admin = AppUser.fromJson({
        'id': 1,
        'username': 'root',
        'role': 'admin',
        'disabled': false,
        'allow_subtitle_download': false,
      });
      expect(admin.canDownloadSubtitles, isTrue);
    });

    test('online subtitles are recognised', () {
      final external = MediaSubtitle.fromJson({
        'id': 1,
        'label': 'Movie.zh.srt',
        'language': 'zh-Hans',
        'forced': false,
        'source': 'external',
      });
      expect(external.source, 'external');
      expect(external.isOnline, isFalse);

      final online = MediaSubtitle.fromJson({
        'id': 2,
        'label': 'Movie.zh.srt',
        'language': 'zh-Hans',
        'forced': false,
        'source': 'online',
      });
      expect(online.isOnline, isTrue);

      final play = PlaySubtitle.fromJson({
        'label': 'Movie.zh.srt',
        'language': 'zh-Hans',
        'url': null,
        'path': '/api/subtitles/2/file',
        'source': 'external',
      });
      expect(play.source, 'external');
      final onlinePlay = PlaySubtitle.fromJson({
        'label': 'Movie.zh.srt',
        'language': 'zh-Hans',
        'url': null,
        'path': '/api/subtitles/2/file',
        'source': 'online',
      });
      expect(onlinePlay.source, 'online');
    });

    test(
      'providers, search, download and admin list use the documented shape',
      () async {
        final api = FakeAdminApi();
        final dio = Dio(BaseOptions(baseUrl: 'http://test'))
          ..httpClientAdapter = api;
        final client = SubtitlesApi(dio);

        api.on('GET /api/subtitles/providers', _providers());
        final providers = await client.providers();
        expect(providers.allowed, isTrue);
        expect(providers.hasReady, isTrue);

        // 带版本时按该版本所在资料库的下载器过滤
        api.on(
          'GET /api/subtitles/providers?version_id=7',
          _providers(ready: false),
        );
        final scoped = await client.providers(versionId: 7);
        expect(api.requests.last, 'GET /api/subtitles/providers?version_id=7');
        expect(scoped.hasReady, isFalse);

        api.on(
          'GET /api/versions/7/subtitles/search?language=zh-Hans',
          _search(results: [_candidate()]),
        );
        final search = await client.search(7, language: 'zh-Hans');
        expect(
          api.requests.last,
          'GET /api/versions/7/subtitles/search?language=zh-Hans',
        );
        expect(search.results.single.downloaded, isFalse);
        expect(search.results.single.language, 'zh-Hans');
        expect(search.failures, isEmpty);

        api.on(
          'POST /api/versions/7/subtitles',
          _downloadResult(),
          status: 201,
        );
        final download = await client.download(
          7,
          SubtitleDownloadRequest.fromCandidate(
            search.results.single,
            language: 'zh-Hans',
          ),
        );
        expect(api.bodies['POST /api/versions/7/subtitles'], {
          'provider_id': 'builtin.assrt',
          'remote_id': '123/Movie.zh.srt',
          'label': 'Movie.2024.2160p.zh.srt',
          'language': 'zh-Hans',
          'release': 'Movie.2024.2160p.BluRay',
          'format': 'srt',
          'forced': false,
          'hearing_impaired': false,
        });
        expect(download.created, isTrue);
        expect(download.subtitle.id, 99);
        expect(download.subtitle.source, 'online');
        expect(download.playPath, '/api/subtitles/99/file');

        api.on('GET /api/admin/versions/7/subtitles', _adminPayload());
        final admin = await client.adminList(7);
        expect(admin.version.filename, 'Movie.2024.2160p.mkv');
        expect(admin.external.single.id, 11);
        expect(admin.online.single.id, 12);
        expect(admin.online.single.online?.providerName, '射手网 assrt');
        expect(admin.embedded?.single.codec, 'subrip');

        api.on(
          'DELETE /api/admin/subtitles/11?confirm=true',
          null,
          status: 204,
        );
        await client.delete(11);
        expect(
          api.requests.last,
          'DELETE /api/admin/subtitles/11?confirm=true',
        );
      },
    );

    test('language codes from user preferences are normalised', () {
      expect(normalizeSubtitleLanguage('zh-CN'), 'zh-Hans');
      expect(normalizeSubtitleLanguage('zh-TW'), 'zh-Hant');
      expect(normalizeSubtitleLanguage('zh-Hans'), 'zh-Hans');
      expect(normalizeSubtitleLanguage('en'), 'en');
      expect(normalizeSubtitleLanguage(null), isNull);
    });

    test(
      'subtitle errors explain permission, limits and provider failures',
      () {
        expect(
          subtitleErrorMessage(
            const ApiError(
              code: 'subtitle_download_not_allowed',
              message: 'x',
              statusCode: 403,
            ),
          ),
          '管理员没有允许你下载在线字幕',
        );
        expect(
          subtitleErrorMessage(
            const ApiError(
              code: 'subtitle_provider_unavailable',
              message: 'x',
              statusCode: 409,
            ),
          ),
          contains('暂时不可用'),
        );
        expect(
          subtitleErrorMessage(
            const ApiError(
              code: 'subtitle_limit_reached',
              message: 'x',
              statusCode: 409,
            ),
          ),
          contains('上限'),
        );
        expect(
          subtitleErrorMessage(
            const ApiError(
              code: 'subtitle_rate_limited',
              message: 'x',
              statusCode: 429,
            ),
          ),
          contains('太频繁'),
        );
      },
    );
  });

  group('subtitle search panel', () {
    testWidgets('uses the zh-CN preference as zh-Hans and lists results', (
      tester,
    ) async {
      final (api, result) = await _pumpSearch(
        tester,
        setup: (api) {
          api.on('GET /api/subtitles/providers?version_id=7', _providers());
          api.on(
            'GET /api/versions/7/subtitles/search?language=zh-Hans',
            _search(results: [_candidate()]),
          );
          api.on(
            'POST /api/versions/7/subtitles',
            _downloadResult(),
            status: 201,
          );
        },
      );
      await _openSearch(tester);
      expect(
        api.requests,
        contains('GET /api/versions/7/subtitles/search?language=zh-Hans'),
      );
      expect(find.text('Movie.2024.2160p.zh.srt'), findsOneWidget);
      expect(find.textContaining('射手网 assrt'), findsWidgets);
      expect(find.textContaining('评分 88.5'), findsWidgets);

      await tester.tap(find.text('Movie.2024.2160p.zh.srt'));
      await tester.pumpAndSettle();
      expect(result.value, isNotNull);
      expect(result.value!.created, isTrue);
      expect(result.value!.subtitle.source, 'online');
      expect(find.text('搜索字幕'), findsNothing);
    });

    testWidgets('empty results show an empty state and a retry action', (
      tester,
    ) async {
      await _pumpSearch(
        tester,
        setup: (api) {
          api.on('GET /api/subtitles/providers?version_id=7', _providers());
          api.on(
            'GET /api/versions/7/subtitles/search?language=zh-Hans',
            _search(),
          );
        },
      );
      await _openSearch(tester);
      expect(find.text('没有找到字幕'), findsOneWidget);
      expect(find.text('重新搜索'), findsOneWidget);
    });

    testWidgets('all downloaders failing lists each provider message', (
      tester,
    ) async {
      await _pumpSearch(
        tester,
        setup: (api) {
          api.on('GET /api/subtitles/providers?version_id=7', _providers());
          api.on(
            'GET /api/versions/7/subtitles/search?language=zh-Hans',
            _search(
              providers: [
                {
                  'id': 'builtin.assrt',
                  'name': '射手网 assrt',
                  'status': 'failed',
                  'message': '站点没有响应',
                },
                {
                  'id': 'other',
                  'name': '另一个下载器',
                  'status': 'unavailable',
                  'message': '没有配置',
                },
              ],
            ),
          );
        },
      );
      await _openSearch(tester);
      expect(find.text('所有字幕下载器都没有返回结果'), findsOneWidget);
      expect(find.textContaining('站点没有响应'), findsOneWidget);
      expect(find.textContaining('没有配置'), findsOneWidget);
    });

    testWidgets('partial failure is shown above successful results', (
      tester,
    ) async {
      await _pumpSearch(
        tester,
        setup: (api) {
          api.on('GET /api/subtitles/providers?version_id=7', _providers());
          api.on(
            'GET /api/versions/7/subtitles/search?language=zh-Hans',
            _search(
              results: [_candidate()],
              providers: [
                {
                  'id': 'builtin.assrt',
                  'name': '射手网 assrt',
                  'status': 'ok',
                  'message': null,
                },
                {
                  'id': 'other',
                  'name': '另一个下载器',
                  'status': 'failed',
                  'message': '超时',
                },
              ],
            ),
          );
        },
      );
      await _openSearch(tester);
      expect(find.text('部分下载器没有返回结果'), findsOneWidget);
      expect(find.textContaining('超时'), findsOneWidget);
      expect(find.text('Movie.2024.2160p.zh.srt'), findsOneWidget);
    });

    testWidgets('no ready downloader points admins at the assrt plugin', (
      tester,
    ) async {
      await _pumpSearch(
        tester,
        role: 'admin',
        setup: (api) {
          api.on(
            'GET /api/subtitles/providers?version_id=7',
            _providers(ready: false),
          );
        },
      );
      await _openSearch(tester);
      expect(find.text('没有可用的字幕下载器'), findsOneWidget);
      expect(find.textContaining('插件 › 射手网 assrt'), findsOneWidget);
      expect(find.textContaining('还没有填写 API Token'), findsOneWidget);
      expect(
        find.text('GET /api/versions/7/subtitles/search?language=zh-Hans'),
        findsNothing,
      );
    });

    testWidgets('permission denied from providers hides search', (
      tester,
    ) async {
      await _pumpSearch(
        tester,
        setup: (api) {
          api.on(
            'GET /api/subtitles/providers?version_id=7',
            _providers(allowed: false, ready: false),
          );
        },
      );
      await _openSearch(tester);
      expect(find.text('不能下载在线字幕'), findsOneWidget);
      expect(find.text('搜索'), findsNothing);
    });

    testWidgets('403 on search switches to the permission state', (
      tester,
    ) async {
      await _pumpSearch(
        tester,
        setup: (api) {
          api.on('GET /api/subtitles/providers?version_id=7', _providers());
          api.on('GET /api/versions/7/subtitles/search?language=zh-Hans', {
            'error': {
              'code': 'subtitle_download_not_allowed',
              'message': '不允许',
            },
          }, status: 403);
        },
      );
      await _openSearch(tester);
      expect(find.text('不能下载在线字幕'), findsOneWidget);
    });

    testWidgets('429 on download stays open and explains the retry', (
      tester,
    ) async {
      final (_, result) = await _pumpSearch(
        tester,
        setup: (api) {
          api.on('GET /api/subtitles/providers?version_id=7', _providers());
          api.on(
            'GET /api/versions/7/subtitles/search?language=zh-Hans',
            _search(results: [_candidate()]),
          );
          api.on(
            'POST /api/versions/7/subtitles',
            {
              'error': {'code': 'subtitle_rate_limited', 'message': '请求太频繁'},
            },
            status: 429,
            headers: {'retry-after': '30'},
          );
        },
      );
      await _openSearch(tester);
      await tester.tap(find.text('Movie.2024.2160p.zh.srt'));
      await tester.pumpAndSettle();
      expect(result.value, isNull);
      expect(find.text('搜索或下载太频繁，请稍后再试'), findsOneWidget);
      expect(find.text('Movie.2024.2160p.zh.srt'), findsOneWidget);
    });

    testWidgets('403 on download keeps the panel and explains permission', (
      tester,
    ) async {
      final (_, result) = await _pumpSearch(
        tester,
        setup: (api) {
          api.on('GET /api/subtitles/providers?version_id=7', _providers());
          api.on(
            'GET /api/versions/7/subtitles/search?language=zh-Hans',
            _search(results: [_candidate()]),
          );
          api.on('POST /api/versions/7/subtitles', {
            'error': {
              'code': 'subtitle_download_not_allowed',
              'message': '不允许',
            },
          }, status: 403);
        },
      );
      await _openSearch(tester);
      await tester.tap(find.text('Movie.2024.2160p.zh.srt'));
      await tester.pumpAndSettle();
      expect(result.value, isNull);
      expect(find.text('管理员没有允许你下载在线字幕'), findsOneWidget);
      expect(find.text('Movie.2024.2160p.zh.srt'), findsOneWidget);
    });

    testWidgets('the downloading candidate is disabled until it finishes', (
      tester,
    ) async {
      final api = _GatedDownloadApi();
      api.on('GET /api/me/preferences', {'subtitle_language': 'zh-CN'});
      api.on('GET /api/subtitles/providers?version_id=7', _providers());
      api.on(
        'GET /api/versions/7/subtitles/search?language=zh-Hans',
        _search(results: [_candidate()]),
      );
      api.on('POST /api/versions/7/subtitles', _downloadResult(), status: 201);
      final dio = Dio(BaseOptions(baseUrl: 'http://test'))
        ..httpClientAdapter = api;
      final result = ValueNotifier<SubtitleDownloadResult?>(null);
      addTearDown(result.dispose);
      tester.view.physicalSize = const Size(390 * 3, 1600 * 3);
      tester.view.devicePixelRatio = 3;
      addTearDown(tester.view.reset);
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            apiClientProvider.overrideWithValue(dio),
            apiBaseUrlProvider.overrideWithValue('http://test'),
            authControllerProvider.overrideWith(() => _User()),
          ],
          child: MaterialApp(
            theme: buildTheme(),
            home: _SearchHarness(result: result),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await _openSearch(tester);

      await tester.tap(find.text('Movie.2024.2160p.zh.srt'));
      await tester.pump();
      expect(find.byType(CircularProgressIndicator), findsWidgets);
      // 下载中条目禁用，再点一次不会发第二个请求
      await tester.tap(find.text('Movie.2024.2160p.zh.srt'));
      await tester.pump();
      api.gate.complete();
      await tester.pumpAndSettle();
      final posts = api.requests
          .where((r) => r == 'POST /api/versions/7/subtitles')
          .length;
      expect(posts, 1);
      expect(result.value, isNotNull);
    });

    testWidgets('keyboard can reach and activate a result', (tester) async {
      final (_, result) = await _pumpSearch(
        tester,
        setup: (api) {
          api.on('GET /api/subtitles/providers?version_id=7', _providers());
          api.on(
            'GET /api/versions/7/subtitles/search?language=zh-Hans',
            _search(results: [_candidate()]),
          );
          api.on(
            'POST /api/versions/7/subtitles',
            _downloadResult(),
            status: 201,
          );
        },
      );
      await _openSearch(tester);

      bool onResult() {
        final context = FocusManager.instance.primaryFocus?.context;
        if (context == null) return false;
        final tile = context.findAncestorWidgetOfExactType<ListTile>();
        return tile?.title is Text &&
            (tile!.title! as Text).data == 'Movie.2024.2160p.zh.srt';
      }

      for (var i = 0; i < 6 && !onResult(); i++) {
        await tester.sendKeyEvent(LogicalKeyboardKey.tab);
        await tester.pump();
      }
      expect(onResult(), isTrue, reason: 'D-pad / Tab 应该能走到结果条目');
      await tester.sendKeyEvent(LogicalKeyboardKey.enter);
      await tester.pumpAndSettle();
      expect(result.value, isNotNull);
    });
  });

  group('detail subtitle row', () {
    Future<FakeAdminApi> pumpRow(
      WidgetTester tester, {
      required List<MediaSubtitle> subtitles,
      bool allow = true,
      bool ready = true,
      bool allowed = true,
      int? subtitleId,
      List<MediaVersion>? versions,
    }) async {
      final api = await pumpAdmin(
        tester,
        Scaffold(
          body: MediaStreams(
            versions:
                versions ??
                [
                  MediaVersion(
                    id: 1,
                    label: '4K · 60 GB',
                    subtitles: subtitles,
                  ),
                ],
            versionId: 1,
            subtitleId: subtitleId,
            onVersion: (_) {},
            onSubtitle: (_) {},
            onSubtitleAdded: (_) async {},
            autoVersion: false,
          ),
        ),
        (api) {
          api.on('GET /api/me/preferences', {'subtitle_language': 'zh-CN'});
          api.on(
            'GET /api/subtitles/providers?version_id=1',
            _providers(ready: ready, allowed: allowed),
          );
        },
        overrides: [
          authControllerProvider.overrideWith(() => _User(allow: allow)),
        ],
      );
      // 认证和下载器状态都是异步的，多等一帧再断言
      await tester.pumpAndSettle();
      return api;
    }

    testWidgets('shows the row and 搜索字幕 even without external subtitles', (
      tester,
    ) async {
      await pumpRow(tester, subtitles: const []);
      expect(find.text('字幕'), findsOneWidget);
      expect(find.text('自动（中文）'), findsOneWidget);
      await tester.tap(find.text('字幕'));
      await tester.pumpAndSettle();
      expect(find.text('搜索字幕'), findsOneWidget);
    });

    testWidgets('online subtitles are marked as 在线', (tester) async {
      await pumpRow(
        tester,
        subtitles: const [
          MediaSubtitle(
            id: 3,
            label: 'Movie.zh.srt',
            language: 'zh-Hans',
            forced: false,
            source: 'online',
          ),
        ],
        subtitleId: 3,
      );
      expect(find.text('中文 · 在线'), findsOneWidget);
    });

    testWidgets('a denied user without subtitles sees no subtitle row', (
      tester,
    ) async {
      await pumpRow(tester, subtitles: const [], allow: false);
      expect(find.text('字幕'), findsNothing);
    });

    testWidgets(
      'server-side denial hides the row even if the user flag is on',
      (tester) async {
        await pumpRow(tester, subtitles: const [], allowed: false);
        expect(find.text('字幕'), findsNothing);
      },
    );

    testWidgets('no ready downloader leaves only the embedded row', (
      tester,
    ) async {
      await pumpRow(tester, subtitles: const [], ready: false);
      expect(find.text('字幕'), findsNothing);
    });
  });

  group('admin edit subtitles', () {
    Future<FakeAdminApi> pumpDialog(
      WidgetTester tester, {
      List<MediaVersion>? versions,
      void Function(FakeAdminApi api)? setup,
    }) => pumpAdmin(
      tester,
      Scaffold(
        body: Builder(
          builder: (context) => Center(
            child: FilledButton(
              onPressed: () => showEditSubtitlesDialog(
                context,
                title: 'Movie',
                versions:
                    versions ??
                    const [
                      MediaVersion(id: 1, label: '4K · 60 GB', subtitles: []),
                      MediaVersion(id: 2, label: '1080p · 8 GB', subtitles: []),
                    ],
              ),
              child: const Text('打开编辑'),
            ),
          ),
        ),
      ),
      (api) {
        api.on('GET /api/me/preferences', {'subtitle_language': 'zh-CN'});
        api.on('GET /api/subtitles/providers?version_id=1', _providers());
        api.on('GET /api/admin/versions/1/subtitles', _adminPayload());
        api.on('GET /api/admin/versions/2/subtitles', _adminPayload());
        setup?.call(api);
      },
      overrides: [
        authControllerProvider.overrideWith(() => _User(role: 'admin')),
      ],
    );

    testWidgets('the movie ⋯ menu opens the admin subtitle editor', (
      tester,
    ) async {
      await pumpAdmin(
        tester,
        Scaffold(
          body: Consumer(
            builder: (context, ref, _) => Center(
              child: FilledButton(
                onPressed: () => showItemActions(
                  context,
                  ref,
                  const MediaCard(
                    id: 1,
                    libraryId: 1,
                    kind: MediaKind.movie,
                    title: 'Movie',
                    watched: false,
                  ),
                  inDetail: true,
                ),
                child: const Text('打开菜单'),
              ),
            ),
          ),
        ),
        (api) {
          api.on('GET /api/me/preferences', {'subtitle_language': 'zh-CN'});
          api.on('GET /api/items/1', _movieDetail());
          api.on('GET /api/subtitles/providers?version_id=1', _providers());
          api.on('GET /api/admin/versions/1/subtitles', _adminPayload());
        },
        overrides: [
          authControllerProvider.overrideWith(() => _User(role: 'admin')),
        ],
      );
      await tester.tap(find.text('打开菜单'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('编辑字幕'));
      await tester.pumpAndSettle();
      expect(find.text('外挂字幕'), findsOneWidget);
      expect(find.text('在线字幕'), findsOneWidget);
    });

    testWidgets('lists external, online and embedded subtitles by section', (
      tester,
    ) async {
      await pumpDialog(tester);
      await tester.tap(find.text('打开编辑'));
      await tester.pumpAndSettle();
      expect(find.text('外挂字幕'), findsOneWidget);
      expect(find.text('在线字幕'), findsOneWidget);
      expect(find.text('内封字幕'), findsOneWidget);
      expect(find.text('Movie.zh.srt'), findsOneWidget);
      expect(find.textContaining('射手网 assrt'), findsWidgets);
      expect(find.textContaining('English'), findsWidgets);
      expect(tester.takeException(), isNull);
    });

    testWidgets('switching version loads its own subtitle list', (
      tester,
    ) async {
      final api = await pumpDialog(tester);
      await tester.tap(find.text('打开编辑'));
      await tester.pumpAndSettle();
      await tester.tap(find.byType(DropdownButtonFormField<int>));
      await tester.pumpAndSettle();
      await tester.tap(find.text('1080p · 8 GB').last);
      await tester.pumpAndSettle();
      expect(api.requests, contains('GET /api/admin/versions/2/subtitles'));
    });

    testWidgets('deleting an external subtitle asks and confirms', (
      tester,
    ) async {
      final api = await pumpDialog(
        tester,
        setup: (api) {
          api.on(
            'DELETE /api/admin/subtitles/11?confirm=true',
            null,
            status: 204,
          );
        },
      );
      await tester.tap(find.text('打开编辑'));
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('删除').first);
      await tester.pumpAndSettle();
      expect(find.text('会从来源上删除这个字幕文件，操作不可恢复。'), findsOneWidget);
      await tester.tap(find.widgetWithText(FilledButton, '删除'));
      await tester.pumpAndSettle();
      expect(
        api.requests,
        contains('DELETE /api/admin/subtitles/11?confirm=true'),
      );
    });

    testWidgets(
      'online subtitle deletion explains that source files are untouched',
      (tester) async {
        await pumpDialog(
          tester,
          setup: (api) {
            api.on(
              'DELETE /api/admin/subtitles/12?confirm=true',
              null,
              status: 204,
            );
          },
        );
        await tester.tap(find.text('打开编辑'));
        await tester.pumpAndSettle();
        await tester.tap(find.byTooltip('删除').last);
        await tester.pumpAndSettle();
        expect(find.textContaining('不影响来源上的文件'), findsOneWidget);
      },
    );

    testWidgets('external subtitle without delete permission is disabled', (
      tester,
    ) async {
      await pumpDialog(
        tester,
        setup: (api) {
          api.on(
            'GET /api/admin/versions/1/subtitles',
            _adminPayload(
              subtitles: [
                {
                  'id': 13,
                  'source': 'external',
                  'label': 'local.zh.srt',
                  'language': 'zh-Hans',
                  'forced': false,
                  'hearing_impaired': false,
                  'default': false,
                  'format': 'srt',
                  'external': {
                    'rel_path': 'local.zh.srt',
                    'deletable': false,
                    'delete_blocked_reason': '本地来源不支持删除',
                  },
                  'online': null,
                },
              ],
            ),
          );
        },
      );
      await tester.tap(find.text('打开编辑'));
      await tester.pumpAndSettle();
      expect(find.textContaining('本地来源不支持删除'), findsOneWidget);
      final button = tester.widget<IconButton>(
        find.ancestor(
          of: find.byIcon(Icons.delete_outline),
          matching: find.byType(IconButton),
        ),
      );
      expect(button.onPressed, isNull);
    });

    testWidgets(
      'missing media info and missing downloader explain next steps',
      (tester) async {
        await pumpDialog(
          tester,
          setup: (api) {
            api.on(
              'GET /api/admin/versions/1/subtitles',
              _adminPayload(embedded: null),
            );
            api.on(
              'GET /api/subtitles/providers?version_id=1',
              _providers(ready: false),
            );
          },
        );
        await tester.tap(find.text('打开编辑'));
        await tester.pumpAndSettle();
        expect(find.textContaining('播过一次后显示'), findsOneWidget);
        expect(find.textContaining('插件 › 射手网 assrt'), findsOneWidget);
      },
    );

    testWidgets('searching from the dialog downloads and reloads the list', (
      tester,
    ) async {
      final api = await pumpDialog(
        tester,
        setup: (api) {
          api.on(
            'GET /api/versions/1/subtitles/search?language=zh-Hans',
            _search(results: [_candidate()]),
          );
          api.on(
            'POST /api/versions/1/subtitles',
            _downloadResult(),
            status: 201,
          );
        },
      );
      await tester.tap(find.text('打开编辑'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('搜索字幕'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Movie.2024.2160p.zh.srt').last);
      await tester.pumpAndSettle();
      expect(api.bodies['POST /api/versions/1/subtitles'], isNotNull);
      final loads = api.requests
          .where((r) => r == 'GET /api/admin/versions/1/subtitles')
          .length;
      expect(loads, 2);
    });
  });

  group('user management', () {
    Future<FakeAdminApi> pumpUsers(WidgetTester tester, {bool allow = false}) =>
        pumpAdmin(
          tester,
          const UsersPage(),
          (api) {
            api.on('GET /api/admin/users', [
              {
                'id': 2,
                'username': 'alice',
                'role': 'user',
                'disabled': false,
                'created_at': '2026-10-01T10:00:00Z',
                'all_libraries': false,
                'last_seen_at': null,
                'allow_subtitle_download': allow,
              },
            ]);
            api.on('GET /api/admin/libraries', []);
            api.on('POST /api/admin/users', null, status: 204);
            api.on('PATCH /api/admin/users/2', null, status: 204);
          },
          overrides: [
            authControllerProvider.overrideWith(() => _User(role: 'admin')),
          ],
        );

    testWidgets('a denied user is tagged and the menu turns it back on', (
      tester,
    ) async {
      final api = await pumpUsers(tester);
      expect(find.textContaining('禁止下载字幕'), findsOneWidget);
      await tester.tap(find.byTooltip('管理 alice'));
      await tester.pumpAndSettle();
      expect(find.text('允许下载在线字幕'), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('user-subtitle-download')));
      await tester.pumpAndSettle();
      expect(api.bodies['PATCH /api/admin/users/2'], {
        'allow_subtitle_download': true,
      });
    });

    testWidgets('create dialog sends the switch, default on', (tester) async {
      final api = await pumpUsers(tester);
      await tester.tap(find.text('新建用户'));
      await tester.pumpAndSettle();
      expect(find.text('允许下载在线字幕'), findsOneWidget);
      await tester.enterText(find.byType(TextFormField).at(0), 'bob');
      await tester.enterText(find.byType(TextFormField).at(1), 'password123');
      await tester.tap(find.text('创建'));
      await tester.pumpAndSettle();
      final body = api.bodies['POST /api/admin/users'] as Map<String, dynamic>;
      expect(body['allow_subtitle_download'], isTrue);
      expect(body['username'], 'bob');
    });

    testWidgets('create dialog can turn subtitle download off', (tester) async {
      final api = await pumpUsers(tester);
      await tester.tap(find.text('新建用户'));
      await tester.pumpAndSettle();
      await tester.tap(find.byType(SwitchListTile));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextFormField).at(0), 'bob');
      await tester.enterText(find.byType(TextFormField).at(1), 'password123');
      await tester.tap(find.text('创建'));
      await tester.pumpAndSettle();
      final body = api.bodies['POST /api/admin/users'] as Map<String, dynamic>;
      expect(body['allow_subtitle_download'], isFalse);
    });

    testWidgets('create dialog hides the switch for admins', (tester) async {
      await pumpUsers(tester);
      await tester.tap(find.text('新建用户'));
      await tester.pumpAndSettle();
      expect(find.text('允许下载在线字幕'), findsOneWidget);
      await tester.tap(find.text('管理员'));
      await tester.pumpAndSettle();
      expect(find.text('允许下载在线字幕'), findsNothing);
    });

    test(
      'createUser and setUserSubtitleDownload use PATCH/POST bodies',
      () async {
        final api = FakeAdminApi();
        final dio = Dio(BaseOptions(baseUrl: 'http://test'))
          ..httpClientAdapter = api;
        api.on('POST /api/admin/users', null, status: 204);
        api.on('PATCH /api/admin/users/2', null, status: 204);
        await createUser(
          dio,
          username: 'bob',
          password: 'password123',
          role: 'user',
          allowSubtitleDownload: false,
        );
        expect(
          (api.bodies['POST /api/admin/users']!
              as Map)['allow_subtitle_download'],
          isFalse,
        );
        await setUserSubtitleDownload(dio, 2, true);
        expect(api.bodies['PATCH /api/admin/users/2'], {
          'allow_subtitle_download': true,
        });
      },
    );
  });
}
