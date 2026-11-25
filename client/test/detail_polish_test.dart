import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/app/tv.dart';
import 'package:iris/core/providers.dart';
import 'package:iris/features/auth/auth_controller.dart';
import 'package:iris/features/detail/detail_page.dart';
import 'package:iris/features/detail/episode_detail_page.dart';
import 'package:iris/features/detail/people_row.dart';
import 'package:iris/features/detail/rating_badges.dart';
import 'package:iris/features/detail/related_row.dart';
import 'package:iris/features/library/browse_api.dart';

import 'support/fake_api.dart';

/// 1x1 透明 PNG，用于测试 ClearLogo 加载成功分支。
final _pngBytes = base64Decode(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=',
);

class _FakeHttpClient implements HttpClient {
  _FakeHttpClient({required this.succeedUrl});

  final String succeedUrl;

  @override
  bool autoUncompress = true;

  @override
  Future<HttpClientRequest> getUrl(Uri url) async =>
      _FakeHttpClientRequest(ok: url.toString() == succeedUrl);

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _FakeHttpClientRequest implements HttpClientRequest {
  _FakeHttpClientRequest({required this.ok});

  final bool ok;

  @override
  final HttpHeaders headers = _FakeHttpHeaders();

  @override
  Future<HttpClientResponse> close() async => _FakeHttpClientResponse(ok: ok);

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _FakeHttpHeaders implements HttpHeaders {
  @override
  dynamic noSuchMethod(Invocation invocation) {}
}

class _FakeHttpClientResponse extends Stream<List<int>>
    implements HttpClientResponse {
  _FakeHttpClientResponse({required this.ok});

  final bool ok;

  @override
  int get statusCode => ok ? 200 : 404;

  @override
  int get contentLength => ok ? _pngBytes.length : 0;

  @override
  HttpClientResponseCompressionState get compressionState =>
      HttpClientResponseCompressionState.notCompressed;

  @override
  StreamSubscription<List<int>> listen(
    void Function(List<int> event)? onData, {
    Function? onError,
    void Function()? onDone,
    bool? cancelOnError,
  }) {
    final stream = ok
        ? Stream<List<int>>.value(_pngBytes)
        : const Stream<List<int>>.empty();
    return stream.listen(
      onData,
      onError: onError,
      onDone: onDone,
      cancelOnError: cancelOnError,
    );
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

class _TestUser extends AuthController {
  @override
  Future<AppUser?> build() async => const AppUser(
    id: 1,
    username: 'u',
    role: 'user',
    disabled: false,
    allowSubtitleDownload: true,
  );
}

Map<String, Object?> _movieJson({
  String title = '功夫',
  List<String> genres = const ['喜剧', '动作', '犯罪'],
  String? releaseDate = '2004-12-23',
  List<Map<String, Object?>> logo = const [],
  List<Map<String, Object?>> ratings = const [],
  List<Map<String, Object?>> versions = const [],
}) => {
  'id': 10,
  'library_id': 1,
  'kind': 'movie',
  'title': title,
  'original_title': null,
  'year': 2004,
  'rating': 8.2,
  'genres': genres,
  'runtime': 99,
  'poster': <Object>[],
  'backdrop': <Object>[],
  'watched': false,
  'favorite': false,
  'watchlisted': false,
  'release_date': releaseDate,
  'overview': '简介内容',
  'content_rating': 'PG-13',
  'logo': logo,
  'directors': <Object>[],
  'writers': <Object>[],
  'cast': <Object>[],
  'studios': ['星辉海外'],
  'countries': ['中国香港'],
  'ratings': ratings,
  'versions': versions,
  'seasons': <Object>[],
  'next_episode': null,
  'progress': null,
};

GoRouter _buildRouter(String location) => GoRouter(
  initialLocation: location,
  routes: [
    GoRoute(
      path: '/',
      builder: (_, _) => const Scaffold(body: Text('首页路由')),
    ),
    GoRoute(
      path: '/search',
      builder: (_, _) => const Scaffold(body: Text('搜索路由')),
    ),
    GoRoute(
      path: '/item/:id',
      builder: (_, state) =>
          DetailPage(id: int.parse(state.pathParameters['id']!)),
    ),
    GoRoute(
      path: '/item/:itemId/episode/:episodeId',
      builder: (_, state) => EpisodeDetailPage(
        itemId: int.parse(state.pathParameters['itemId']!),
        seasonId: int.parse(state.uri.queryParameters['season'] ?? '21'),
        episodeId: int.parse(state.pathParameters['episodeId']!),
      ),
    ),
  ],
);

Future<GoRouter> _pumpDetail(
  WidgetTester tester, {
  required Map<String, Object?> detail,
  List<Map<String, Object?>> related = const [],
  double width = 390,
  double height = 1600,
  bool tv = false,
}) async {
  final api = FakeAdminApi()
    ..on('GET /api/items/10', detail)
    ..on('GET /api/items/10/related?limit=$relatedLimit', related)
    ..on('GET /api/me/preferences', {'subtitle_language': 'zh-CN'});
  final dio = Dio(BaseOptions(baseUrl: 'http://test'))..httpClientAdapter = api;
  final router = _buildRouter('/item/10');
  addTearDown(router.dispose);
  tester.view.physicalSize = Size(width * 2, height * 2);
  tester.view.devicePixelRatio = 2;
  addTearDown(tester.view.reset);

  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        apiClientProvider.overrideWithValue(dio),
        apiBaseUrlProvider.overrideWithValue('http://test'),
        authControllerProvider.overrideWith(_TestUser.new),
        televisionProvider.overrideWithValue(tv),
      ],
      child: MaterialApp.router(theme: buildTheme(), routerConfig: router),
    ),
  );
  await tester.pumpAndSettle();
  return router;
}

Future<GoRouter> _pumpEpisode(
  WidgetTester tester, {
  double width = 390,
  bool tv = false,
}) async {
  final api = FakeAdminApi()
    ..on('GET /api/items/20', {
      ..._movieJson(title: '三体'),
      'id': 20,
      'kind': 'series',
      'runtime': null,
    })
    ..on('GET /api/seasons/21', {
      'id': 21,
      'item_id': 20,
      'number': 1,
      'episodes': [
        {
          'id': 301,
          'number': 1,
          'name': '科学边界',
          'overview': '单集简介',
          'air_date': '2023-01-15',
          'runtime': 45,
          'rating': 8.1,
          'still': <Object>[],
          'versions': [
            {
              'id': 3011,
              'label': '2160p · REMUX · 10 GB',
              'size': 10 * 1024 * 1024 * 1024,
              'subtitles': <Object>[],
              'media': {
                'duration': 2700,
                'width': 3840,
                'height': 2160,
                'video_codec': 'hevc',
                'hdr': 'dolby_vision',
                'audio': [
                  {
                    'language': 'chi',
                    'title': 'TrueHD Atmos',
                    'codec': 'truehd',
                    'channels': 8,
                    'default': true,
                  },
                ],
                'subtitles': <Object>[],
              },
            },
            {
              'id': 3012,
              'label': '1080p · WEB-DL · 2 GB',
              'size': 2 * 1024 * 1024 * 1024,
              'subtitles': <Object>[],
            },
          ],
          'watched': false,
          'progress': null,
          'favorite': false,
        },
      ],
    })
    ..on('GET /api/me/preferences', {'subtitle_language': 'zh-CN'});
  final dio = Dio(BaseOptions(baseUrl: 'http://test'))..httpClientAdapter = api;
  final router = _buildRouter('/item/20/episode/301?season=21');
  addTearDown(router.dispose);
  tester.view.physicalSize = Size(width * 2, 1600 * 2);
  tester.view.devicePixelRatio = 2;
  addTearDown(tester.view.reset);

  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        apiClientProvider.overrideWithValue(dio),
        apiBaseUrlProvider.overrideWithValue('http://test'),
        authControllerProvider.overrideWith(_TestUser.new),
        televisionProvider.overrideWithValue(tv),
      ],
      child: MaterialApp.router(theme: buildTheme(), routerConfig: router),
    ),
  );
  await tester.pumpAndSettle();
  return router;
}

void main() {
  group('PeopleRow deduplication', () {
    testWidgets('merges duplicate personId roles while preserving order', (
      tester,
    ) async {
      await tester.pumpWidget(
        MaterialApp(
          theme: buildTheme(),
          home: Scaffold(
            body: PeopleRow(
              padding: const EdgeInsets.symmetric(horizontal: 20),
              directors: const [
                Credit(personId: 1, name: '周星驰'),
                Credit(personId: 2, name: '陈凯歌'),
              ],
              writers: const [
                Credit(personId: 2, name: '陈凯歌'),
                Credit(personId: 3, name: '曾谨昌'),
              ],
              cast: const [
                Credit(personId: 1, name: '周星驰', character: 'Stephen Chow'),
                Credit(personId: 3, name: '曾谨昌', character: ''),
                Credit(personId: 4, name: '元华', character: '包租公'),
                Credit(personId: 5, name: '路人甲', character: '   '),
              ],
            ),
          ),
        ),
      );

      expect(find.text('周星驰'), findsOneWidget);
      expect(find.text('陈凯歌'), findsOneWidget);
      expect(find.text('曾谨昌'), findsOneWidget);
      expect(find.text('元华'), findsOneWidget);
      expect(find.text('路人甲'), findsOneWidget);

      expect(find.text('导演 · 饰 Stephen Chow'), findsOneWidget);
      expect(find.text('导演 / 编剧'), findsOneWidget);
      expect(find.text('编剧'), findsOneWidget);
      expect(find.text('包租公'), findsOneWidget);

      final xing = tester.getTopLeft(find.text('周星驰')).dx;
      final chen = tester.getTopLeft(find.text('陈凯歌')).dx;
      final zeng = tester.getTopLeft(find.text('曾谨昌')).dx;
      final yuan = tester.getTopLeft(find.text('元华')).dx;
      expect(xing, lessThan(chen));
      expect(chen, lessThan(zeng));
      expect(zeng, lessThan(yuan));
    });
  });

  group('ClearLogo title', () {
    testWidgets('renders ClearLogo image when loaded and keeps top bar text', (
      tester,
    ) async {
      final detail =
          _movieJson(
              logo: const [
                {'width': 400, 'url': '/api/art/logo-ok.png'},
              ],
              versions: [
                {
                  'id': 1,
                  'label': '4K · DV',
                  'size': 1024,
                  'subtitles': <Object>[],
                },
              ],
            )
            ..['directors'] = [
              {'person_id': 1, 'name': '周星驰', 'profile': <Object>[]},
            ];
      final semantics = tester.ensureSemantics();
      try {
        await HttpOverrides.runZoned(
          () async {
            await _pumpDetail(
              tester,
              detail: detail,
              related: [
                {
                  'id': 11,
                  'library_id': 1,
                  'kind': 'movie',
                  'title': '少林足球',
                  'original_title': null,
                  'year': 2001,
                  'rating': 7.8,
                  'genres': <String>['喜剧'],
                  'runtime': 112,
                  'poster': <Object>[],
                  'backdrop': <Object>[],
                  'watched': false,
                },
              ],
              width: 390,
              height: 500,
            );
            await tester.runAsync(
              () => Future<void>.delayed(const Duration(milliseconds: 50)),
            );
            await tester.pumpAndSettle();
            // ClearLogo 图片加载成功后渲染 RawImage 并替代正文的 Text('功夫')
            final logoImage = find.byType(Image);
            expect(logoImage, findsOneWidget);
            expect(find.byType(RawImage), findsOneWidget);
            final imageWidget = tester.widget<Image>(logoImage);
            expect(imageWidget.fit, BoxFit.contain);
            expect(imageWidget.alignment, Alignment.centerLeft);
            expect(find.bySemanticsLabel('功夫'), findsOneWidget);
            expect(find.text('功夫'), findsNothing);
            // 限高在手机 76-88 范围内
            expect(tester.getSize(logoImage).height, lessThanOrEqualTo(88));

            // 滚过头图后吸顶顶栏仍显示文字片名
            await tester.drag(
              find.byType(CustomScrollView),
              const Offset(0, -600),
            );
            await tester.pumpAndSettle();
            expect(
              find.descendant(
                of: find.byType(DetailTopBar),
                matching: find.text('功夫'),
              ),
              findsOneWidget,
            );
          },
          createHttpClient: (_) =>
              _FakeHttpClient(succeedUrl: 'http://test/api/art/logo-ok.png'),
        );
      } finally {
        semantics.dispose();
      }
    });

    testWidgets('falls back to headlineMedium text when ClearLogo fails', (
      tester,
    ) async {
      final detail = _movieJson(
        logo: const [
          {'width': 400, 'url': '/api/art/logo-404.png'},
        ],
      );
      await HttpOverrides.runZoned(
        () async {
          await _pumpDetail(tester, detail: detail, width: 390);
          expect(find.text('功夫'), findsOneWidget);
        },
        createHttpClient: (_) =>
            _FakeHttpClient(succeedUrl: 'http://test/api/art/other.png'),
      );
    });
  });

  group('TechBadge and DetailMeta', () {
    testWidgets('detail page updates TechBadge when switching versions', (
      tester,
    ) async {
      final detail = _movieJson(
        versions: [
          {
            'id': 1,
            'label': '4K · REMUX · 58 GB',
            'size': 58 * 1024 * 1024 * 1024,
            'subtitles': <Object>[],
            'media': {
              'duration': 6000,
              'width': 3840,
              'height': 2160,
              'video_codec': 'hevc',
              'hdr': 'hdr10',
              'audio': [
                {
                  'language': 'chi',
                  'title': null,
                  'codec': 'truehd',
                  'channels': 8,
                  'default': true,
                },
              ],
              'subtitles': <Object>[],
            },
          },
          {
            'id': 2,
            'label': '1080p · WEB-DL · DV · 8 GB',
            'size': 8 * 1024 * 1024 * 1024,
            'subtitles': <Object>[],
          },
        ],
      );
      await _pumpDetail(tester, detail: detail, width: 390);

      for (final spec in ['4K', 'HDR10', 'HEVC', 'TrueHD']) {
        expect(
          find.descendant(
            of: find.byType(TechBadge),
            matching: find.text(spec),
          ),
          findsOneWidget,
        );
      }

      await tester.tap(find.text('版本'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('1080p · WEB-DL · DV · 8 GB'));
      await tester.pumpAndSettle();

      for (final spec in ['1080p', '杜比视界']) {
        expect(
          find.descendant(
            of: find.byType(TechBadge),
            matching: find.text(spec),
          ),
          findsOneWidget,
        );
      }
      expect(
        find.descendant(of: find.byType(TechBadge), matching: find.text('4K')),
        findsNothing,
      );
    });

    testWidgets('episode detail page shows TechBadge for active version', (
      tester,
    ) async {
      await _pumpEpisode(tester, width: 390);

      for (final spec in ['4K', '杜比视界', 'HEVC', 'TrueHD']) {
        expect(
          find.descendant(
            of: find.byType(TechBadge),
            matching: find.text(spec),
          ),
          findsOneWidget,
        );
      }

      await tester.tap(find.text('自动'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('1080p · WEB-DL · 2 GB'));
      await tester.pumpAndSettle();

      expect(
        find.descendant(
          of: find.byType(TechBadge),
          matching: find.text('1080p'),
        ),
        findsOneWidget,
      );
      expect(
        find.descendant(
          of: find.byType(TechBadge),
          matching: find.text('杜比视界'),
        ),
        findsNothing,
      );
    });
  });

  group('Facts, genres dedup, and SourceRatingBadge', () {
    testWidgets(
      'formats release date, hides meta genres on wide aside, cleans label',
      (tester) async {
        final detail = _movieJson(
          releaseDate: '1996-12-21',
          genres: const ['喜剧', '动作', '犯罪'],
          ratings: const [
            {'source': 'builtin.tmdb', 'label': 'TMDB 元数据', 'value': 8.5},
          ],
        );
        await _pumpDetail(tester, detail: detail, width: 1400);

        expect(find.text('1996年12月21日'), findsOneWidget);
        expect(find.text('1996-12-21'), findsNothing);
        // 右侧 _Facts 展示完整类型，主元数据行不重复截断版
        expect(find.text('喜剧 / 动作 / 犯罪'), findsOneWidget);
        expect(find.textContaining('喜剧 / 动作'), findsOneWidget); // 仅来自完整串，不是截断串
        expect(find.text('2004 · 1 小时 39 分'), findsOneWidget);
        expect(find.text('TMDB 8.5', findRichText: true), findsOneWidget);
      },
    );

    testWidgets(
      'falls back to raw release date when invalid and shows meta genres when narrow',
      (tester) async {
        final detail = _movieJson(
          releaseDate: '1996年冬',
          genres: const ['喜剧', '动作', '犯罪'],
        );
        await _pumpDetail(tester, detail: detail, width: 1000);

        expect(find.text('2004 · 喜剧 / 动作 · 1 小时 39 分'), findsOneWidget);
        expect(find.text('上映日期'), findsNothing);
      },
    );
  });

  group('DetailTopBar quick nav', () {
    testWidgets(
      'shows search and home on wide non-TV and fades scrim on scroll',
      (tester) async {
        final detail =
            _movieJson(
                versions: [
                  for (var i = 0; i < 4; i++)
                    {
                      'id': i + 1,
                      'label': '版本 $i',
                      'size': 1024,
                      'subtitles': <Object>[],
                    },
                ],
              )
              ..['directors'] = [
                {'person_id': 1, 'name': '导演甲', 'profile': <Object>[]},
              ]
              ..['overview'] = List.filled(40, '这是一段足够长以触发页面滚动的剧情简介。').join();
        final router = await _pumpDetail(
          tester,
          detail: detail,
          related: [
            {
              'id': 11,
              'library_id': 1,
              'kind': 'movie',
              'title': '少林足球',
              'original_title': null,
              'year': 2001,
              'rating': 7.8,
              'genres': <String>['喜剧'],
              'runtime': 112,
              'poster': <Object>[],
              'backdrop': <Object>[],
              'watched': false,
            },
          ],
          width: 1200,
          height: 500,
        );

        final searchBtn = find.byTooltip('搜索');
        final homeBtn = find.byTooltip('首页');
        expect(searchBtn, findsOneWidget);
        expect(homeBtn, findsOneWidget);

        Color? bgOf(Finder f) => tester
            .widget<IconButton>(
              find.ancestor(of: f, matching: find.byType(IconButton)),
            )
            .style
            ?.backgroundColor
            ?.resolve(<WidgetState>{});

        expect(bgOf(searchBtn), CinemaColors.scrim);
        expect(bgOf(homeBtn), CinemaColors.scrim);

        await tester.drag(find.byType(CustomScrollView), const Offset(0, -600));
        await tester.pumpAndSettle();

        expect(bgOf(searchBtn), Colors.transparent);
        expect(bgOf(homeBtn), Colors.transparent);

        await tester.tap(searchBtn);
        await tester.pumpAndSettle();
        expect(router.state.uri.toString(), '/search');
      },
    );

    testWidgets('hides quick nav on narrow screens and on TV', (tester) async {
      await _pumpDetail(tester, detail: _movieJson(), width: 390);
      expect(find.byTooltip('搜索'), findsNothing);
      expect(find.byTooltip('首页'), findsNothing);

      await _pumpDetail(tester, detail: _movieJson(), width: 1200, tv: true);
      expect(find.byTooltip('搜索'), findsNothing);
      expect(find.byTooltip('首页'), findsNothing);
    });

    testWidgets('episode page wide non-TV top bar navigates home', (
      tester,
    ) async {
      final router = await _pumpEpisode(tester, width: 1200);
      expect(find.byTooltip('搜索'), findsOneWidget);
      expect(find.byTooltip('首页'), findsOneWidget);

      await tester.tap(find.byTooltip('首页'));
      await tester.pumpAndSettle();
      expect(router.state.uri.toString(), '/');
    });
  });
}
