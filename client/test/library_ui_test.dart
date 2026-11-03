import 'dart:async';

import 'package:iris/app/theme.dart';
import 'package:iris/app/tv.dart';
import 'package:iris/core/providers.dart';
import 'package:iris/core/network_status.dart';
import 'package:iris/core/widgets/expandable_text.dart';
import 'package:iris/core/widgets/focus_tile.dart';
import 'package:iris/features/admin/users_page.dart';
import 'package:iris/features/auth/auth_controller.dart';
import 'package:iris/features/detail/detail_page.dart';
import 'package:iris/features/detail/episode_detail_page.dart';
import 'package:iris/features/detail/rating_badges.dart';
import 'package:iris/features/home/home_page.dart';
import 'package:iris/features/library/art_image.dart';
import 'package:iris/features/library/collections_page.dart';
import 'package:iris/features/library/library_page.dart';
import 'package:iris/features/me/settings_pages.dart';
import 'package:iris/features/person/person_page.dart';
import 'package:iris/features/play/cast.dart';
import 'package:iris/features/play/cast_page.dart';
import 'package:iris/features/play/dlna.dart';
import 'package:iris/features/play/play_api.dart';
import 'package:iris/features/play/player_page.dart';
import 'package:iris/features/play/playlists_page.dart';
import 'package:iris/features/search/search_page.dart';
import 'package:iris/main.dart' show appShortcuts;
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_riverpod/misc.dart' show Override;
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';

import 'support/fake_api.dart';

// ---- 接口数据，字段和服务端 /api 的响应一致 ----

const gb = 1024 * 1024 * 1024;

Map<String, Object?> card(
  int id,
  String title, {
  String kind = 'movie',
  int library = 1,
  int? year,
  double? rating,
  List<String> genres = const [],
  bool watched = false,
}) => {
  'id': id,
  'library_id': library,
  'kind': kind,
  'title': title,
  'original_title': null,
  'year': year,
  'rating': rating,
  'genres': genres,
  'runtime': kind == 'movie' ? 125 : null,
  'poster': <Object>[],
  'backdrop': <Object>[],
  'watched': watched,
};

Map<String, Object?> page(
  List<Map<String, Object?>> items, {
  int? total,
  List<Map<String, Object?>> people = const [],
  List<Map<String, Object?>> episodes = const [],
}) => {
  'items': items,
  'total': total ?? items.length,
  'people': people,
  'episodes': episodes,
};

const movies = {
  'id': 1,
  'name': '电影',
  'kind': 'movie',
  'item_count': 3,
  'cover': <Object>[],
};
const shows = {
  'id': 2,
  'name': '剧集',
  'kind': 'series',
  'item_count': 1,
  'cover': <Object>[],
};

final wanderingEarth = card(
  10,
  '流浪地球',
  year: 2019,
  rating: 7.9,
  genres: ['科幻', '冒险'],
);
final interstellar = card(11, '星际穿越', year: 2014, rating: 8.7);
final crazyAlien = card(12, '疯狂的外星人', year: 2019, rating: 6.5);
final threeBody = card(20, '三体', kind: 'series', library: 2, year: 2023);

Map<String, Object?> credit(int id, String name, [String? character]) => {
  'person_id': id,
  'name': name,
  'character': character,
  'profile': <Object>[],
};

Map<String, Object?> detail(
  Map<String, Object?> card, {
  List<Map<String, Object?>> versions = const [],
  List<Map<String, Object?>> seasons = const [],
  Map<String, Object?>? next,
  Map<String, Object?>? progress,
  List<Map<String, Object?>> ratings = const [],
  bool favorite = false,
  bool watchlisted = false,
}) => {
  ...card,
  'en_title': null,
  'release_date': null,
  'overview': '${card['title']}的简介',
  'content_rating': null,
  'studios': <Object>[],
  'countries': <Object>[],
  'thumb': <Object>[],
  'logo': <Object>[],
  'directors': [credit(5, '郭帆')],
  'writers': [credit(9, '刘慈欣')],
  'cast': [credit(7, '吴京', '刘培强')],
  'versions': versions,
  'seasons': seasons,
  'next_episode': next,
  'progress': progress,
  'ratings': ratings,
  'favorite': favorite,
  'watchlisted': watchlisted,
};

/// 看到一半的进度，单位秒
Map<String, Object?> progressAt(int position, int duration) => {
  'position': position,
  'duration': duration,
};

Map<String, Object?> episode(
  int id,
  int number,
  String name, {
  bool watched = false,
  List<Map<String, Object?>>? versions,
  Map<String, Object?>? progress,
}) => {
  'id': id,
  'number': number,
  'name': name,
  'overview': null,
  'air_date': null,
  'runtime': 45,
  'rating': null,
  'still': <Object>[],
  'versions':
      versions ??
      [
        {'id': id * 10, 'label': '1080p', 'size': gb, 'subtitles': <Object>[]},
      ],
  'watched': watched,
  'progress': progress,
  'favorite': false,
};

Map<String, Object?> season(int id, int number, int count) => {
  'id': id,
  'number': number,
  'name': '第 $number 季',
  'air_date': null,
  'poster': <Object>[],
  'episode_count': count,
};

Map<String, Object?> work(
  Map<String, Object?> card,
  String character, {
  bool directed = false,
}) => {
  ...card,
  'roles': ['cast', if (directed) 'director'],
  'character': character,
};

Map<String, Object?> seasonDetail(
  int id,
  int number,
  List<Map<String, Object?>> episodes,
) => {
  'id': id,
  'item_id': 20,
  'number': number,
  'name': '第 $number 季',
  'overview': null,
  'air_date': null,
  'poster': <Object>[],
  'episodes': episodes,
};

Map<String, Object?> home({
  List<Map<String, Object?>> libraries = const [],
  List<Map<String, Object?>> recent = const [],
  List<Map<String, Object?>> shelves = const [],
}) => {
  'libraries': libraries,
  'recent': recent,
  'favorites': <Object>[],
  'watchlist': <Object>[],
  'shelves': shelves,
};

Map<String, Object?> hubShelf(
  Map<String, Object?> library,
  String kind,
  String title,
  List<Map<String, Object?>> items, {
  String? arg,
  List<Map<String, Object?>> recent = const [],
}) => {
  'library': library,
  'hub': {
    'kind': kind,
    'arg': arg,
    'title': title,
    'items': items,
    'recent': recent,
  },
};

/// 签发播放链接的响应。
/// [external] 为假时模拟服务器没配 public_url：只有相对路径，没有调起链接。
Map<String, Object?> ticket({
  String title = '流浪地球',
  int versionId = 1,
  bool external = true,
  int? position,
}) {
  const path = '/play/tok/movie.mkv';
  const subPath = '/play/tok2/movie.zh.srt';
  const url = 'https://movie.example.com$path';
  const sub = 'https://movie.example.com$subPath';
  return {
    'playable_id': 1,
    'version_id': versionId,
    'title': title,
    'filename': 'movie.mkv',
    'url': external ? url : null,
    'path': path,
    'expires_at': '2026-10-02T12:00:00Z',
    'subtitles': [
      {
        'id': 1,
        'label': '简体中文',
        'language': 'zh',
        'forced': false,
        'url': external ? sub : null,
        'path': subPath,
      },
    ],
    'position': position,
    'launch': !external
        ? null
        : {
            'infuse': 'infuse://x-callback-url/play?url=$url&sub=$sub',
            'vlc': 'vlc-x-callback://x-callback-url/stream?url=$url&sub=$sub',
            'nplayer': 'nplayer-$url',
            'senplayer': 'senplayer://x-callback-url/play?url=$url',
            'android':
                'intent://movie.example.com/play/tok/movie.mkv'
                '#Intent;scheme=https;type=video/*;end',
          },
  };
}

String itemsRoute({
  String sort = 'added',
  String order = 'desc',
  String filters = '',
  int offset = 0,
}) =>
    'GET /api/libraries/1/items?sort=$sort&order=$order$filters'
    '&offset=$offset&limit=60';

// ---- 测试环境 ----

/// 和正式路由相同的浏览页面路径，但不经过登录守卫。
GoRouter testRouter(String location) => GoRouter(
  initialLocation: location,
  routes: [
    // 真播放器依赖原生 libmpv，测试里只看传给它的参数
    GoRoute(
      path: '/player',
      builder: (_, state) => _FakePlayerPage(state.extra! as PlayerArgs),
    ),
    GoRoute(
      path: '/cast',
      builder: (_, state) => CastPage(args: state.extra! as CastArgs),
    ),
    GoRoute(
      path: '/',
      builder: (_, _) => const HomePage(),
      routes: [
        GoRoute(
          path: 'library/:id',
          builder: (_, state) => LibraryPage(
            id: int.parse(state.pathParameters['id']!),
            params: state.uri.queryParameters,
          ),
        ),
      ],
    ),
    GoRoute(
      path: '/search',
      builder: (_, state) =>
          SearchPage(query: state.uri.queryParameters['q'] ?? ''),
    ),
    GoRoute(
      path: '/item/:itemId/episode/:episodeId',
      builder: (_, state) => EpisodeDetailPage(
        itemId: int.parse(state.pathParameters['itemId']!),
        seasonId: int.parse(state.uri.queryParameters['season']!),
        episodeId: int.parse(state.pathParameters['episodeId']!),
      ),
    ),
    GoRoute(
      path: '/item/:id',
      builder: (_, state) => DetailPage(
        id: int.parse(state.pathParameters['id']!),
        args: state.extra as DetailArgs?,
      ),
    ),
    GoRoute(
      path: '/person/:id',
      builder: (_, state) =>
          PersonPage(id: int.parse(state.pathParameters['id']!)),
    ),
    GoRoute(path: '/playlists', builder: (_, _) => const PlaylistsPage()),
    GoRoute(
      path: '/playlists/:id',
      builder: (_, state) => PlaylistDetailPage(
        playlistId: int.parse(state.pathParameters['id']!),
      ),
    ),
    GoRoute(
      path: '/library/:id/collections',
      builder: (_, state) =>
          CollectionsPage(libraryId: int.parse(state.pathParameters['id']!)),
    ),
    GoRoute(
      path: '/collections/:id',
      builder: (_, state) => CollectionDetailPage(
        collectionId: int.parse(state.pathParameters['id']!),
      ),
    ),
  ],
);

/// 播放相关的平台能力：调起的链接记在 [launched] 里，[opens] 为 false 时模拟调起失败。
class _FakePlayerPage extends StatelessWidget {
  const _FakePlayerPage(this.args);

  final PlayerArgs args;

  @override
  Widget build(BuildContext context) => Scaffold(
    body: Column(
      children: [
        Text('playing ${args.url}'),
        for (final s in args.subtitles) Text('subtitle ${s.id} ${s.title}'),
        Text('start ${args.start?.inSeconds}'),
        if (args.playlistId case final id?) Text('playlist $id'),
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('退出播放'),
        ),
        // 真播放器里是控制条上的投屏按钮，选好电视后带着它退出
        if (args.castable)
          TextButton(
            onPressed: () => Navigator.of(context).pop(livingRoomTv),
            child: const Text('转到电视'),
          ),
      ],
    ),
  );
}

final livingRoomTv = CastDevice(
  id: 'uuid:tv-1',
  name: '客厅的小米电视',
  controlUrl: Uri.parse('http://192.168.1.20:49152/AVTransport/control'),
  serviceType: 'urn:schemas-upnp-org:service:AVTransport:1',
);

/// 假的搜索：一次推出 [devices] 就结束。
class FakeDiscovery implements CastDiscovery {
  FakeDiscovery(this.devices);

  List<CastDevice> devices;
  int searches = 0;

  @override
  Stream<CastDevice> search({Duration timeout = Duration.zero}) {
    searches++;
    return Stream.fromIterable(devices);
  }
}

/// 假电视：记下收到的指令，[state]、[position] 是下一次查询的结果。
class FakeRenderer implements CastRenderer {
  final List<String> calls = [];
  CastState state = CastState.playing;
  Duration position = Duration.zero;
  Duration? duration = const Duration(hours: 2);
  CastException? loadError;
  CastException? playError;

  /// 设置后 load 要等它完成，模拟电视加载得慢
  Completer<void>? loading;

  @override
  Future<void> load(String url, String title) async {
    calls.add('load $url $title');
    await loading?.future;
    if (loadError case final error?) throw error;
  }

  @override
  Future<void> play() async {
    calls.add('play');
    if (playError case final error?) throw error;
  }

  @override
  Future<void> pause() async => calls.add('pause');

  @override
  Future<void> stop() async => calls.add('stop');

  @override
  Future<void> seek(Duration position) async =>
      calls.add('seek ${position.inSeconds}');

  @override
  Future<CastStatus> status() async =>
      CastStatus(state: state, position: position, duration: duration);
}

class _FixedNetworkStatus implements NetworkStatusSource {
  const _FixedNetworkStatus(this.kind);

  final NetworkKind kind;

  @override
  Future<NetworkKind> current() async => kind;

  @override
  Stream<NetworkKind> get changes => const Stream<NetworkKind>.empty();
}

class FakePlayer {
  FakePlayer({this.platform = PlayPlatform.ios, this.opens = true});

  final PlayPlatform platform;
  bool opens;
  final List<Uri> launched = [];
  final List<String> intents = [];
  final prefs = MemoryPlayerPrefs();
  final discovery = FakeDiscovery([livingRoomTv]);
  final renderer = FakeRenderer();

  List<Override> get overrides => [
    castDiscoveryProvider.overrideWithValue(discovery),
    castRendererProvider.overrideWithValue((_) => renderer),
    playPlatformProvider.overrideWithValue(platform),
    networkStatusProvider.overrideWithValue(
      const _FixedNetworkStatus(NetworkKind.wifi),
    ),
    playerPrefsProvider.overrideWithValue(prefs),
    urlOpenerProvider.overrideWithValue((uri) async {
      launched.add(uri);
      return opens;
    }),
    videoIntentProvider.overrideWithValue((url) async {
      if (!opens) throw PlatformException(code: 'ActivityNotFound');
      intents.add(url);
    }),
  ];
}

Future<(FakeAdminApi, GoRouter)> pumpBrowse(
  WidgetTester tester,
  String location,
  void Function(FakeAdminApi api) setup, {
  double height = 2400,
  FakePlayer? player,
  bool television = false,
  List<Override> overrides = const [],
}) async {
  final api = FakeAdminApi();
  setup(api);
  final dio = Dio(BaseOptions(baseUrl: 'http://test'))..httpClientAdapter = api;
  final router = testRouter(location);
  addTearDown(router.dispose);
  if (television) {
    // 1080p 电视，密度 2，即 960×540dp
    tester.view.physicalSize = const Size(1920, 1080);
    tester.view.devicePixelRatio = 2;
    enterTelevisionMode();
    addTearDown(
      () => FocusManager.instance.highlightStrategy =
          FocusHighlightStrategy.automatic,
    );
  } else {
    // 手机竖屏宽度
    tester.view.physicalSize = Size(390 * 3, height * 3);
    tester.view.devicePixelRatio = 3;
  }
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        apiClientProvider.overrideWithValue(dio),
        apiBaseUrlProvider.overrideWithValue('http://test'),
        ...(player ?? FakePlayer()).overrides,
        ...overrides,
      ],
      child: MaterialApp.router(
        theme: buildTheme(),
        shortcuts: appShortcuts,
        builder: television ? televisionBuilder : null,
        routerConfig: router,
      ),
    ),
  );
  await tester.pumpAndSettle();
  return (api, router);
}

String location(GoRouter router) => router.state.uri.toString();

/// 记下路由报给浏览器地址栏的地址和是否替换当前历史。
/// router.state 是路由内部状态，命令式导航时和地址栏可能不一致，要单独看。
List<({String uri, bool replace})> recordBrowserUrls(WidgetTester tester) {
  final reported = <({String uri, bool replace})>[];
  tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
    SystemChannels.navigation,
    (call) async {
      if (call.method == 'routeInformationUpdated') {
        final args = call.arguments as Map<Object?, Object?>;
        reported.add((
          uri: args['uri']! as String,
          replace: args['replace']! as bool,
        ));
      }
      return null;
    },
  );
  addTearDown(
    () => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
      SystemChannels.navigation,
      null,
    ),
  );
  return reported;
}

Future<void> pickMenu(WidgetTester tester, String chip, String option) async {
  await tester.tap(find.text(chip));
  await tester.pumpAndSettle();
  await tester.tap(
    find.ancestor(
      of: find.text(option).last,
      matching: find.byWidgetPredicate((w) => w is PopupMenuItem),
    ),
  );
  await tester.pumpAndSettle();
}

/// Plex 式筛选：先点「筛选」选一个维度，再在弹层里选值。
Future<void> pickFilter(
  WidgetTester tester,
  String dimension,
  String option,
) async {
  await tester.tap(find.text('筛选'));
  await tester.pumpAndSettle();
  await tester.tap(find.text(dimension).last);
  await tester.pumpAndSettle();
  await tester.tap(find.text(option).last);
  await tester.pumpAndSettle();
}

class _SignedInAdmin extends AuthController {
  @override
  Future<AppUser?> build() async => const AppUser(
    id: 1,
    username: 'admin',
    role: 'admin',
    disabled: false,
    allowSubtitleDownload: true,
  );
}

class _SignedInViewer extends AuthController {
  @override
  Future<AppUser?> build() async => const AppUser(
    id: 2,
    username: 'viewer',
    role: 'user',
    disabled: false,
    allowSubtitleDownload: true,
  );
}

Map<String, Object?> playlistEntry(
  int playableId,
  Map<String, Object?> card, {
  int? seasonNumber,
  int? episodeId,
  int? episodeNumber,
  String? episodeName,
}) => {
  'position': playableId,
  'playable_id': playableId,
  'version_id': playableId * 10,
  'item_id': card['id'],
  'title': card['title'],
  'season_number': seasonNumber,
  'episode_number': episodeNumber,
  'episode_id': episodeId,
  'episode_name': episodeName,
};

void main() {
  group('home', () {
    testWidgets('pulling down refreshes the home feed', (tester) async {
      var calls = 0;
      await pumpBrowse(tester, '/', (api) {
        api.routes['GET /api/home'] = (_) {
          calls++;
          return (
            200,
            home(
              libraries: [movies],
              shelves: [
                hubShelf(movies, 'latest', '最近添加的电影', [wanderingEarth]),
              ],
            ),
          );
        };
      }, height: 800);
      expect(calls, 1);

      await tester.dragFrom(const Offset(195, 700), const Offset(0, 500));
      await tester.pumpAndSettle();
      await tester.pumpAndSettle();
      expect(calls, 2);
    });

    testWidgets('empty library explains how content gets there', (
      tester,
    ) async {
      await pumpBrowse(tester, '/', (api) {
        api.on('GET /api/home', home());
      });
      expect(find.text('片库里还没有影片'), findsOneWidget);
      expect(find.textContaining('需要管理员开放资料库'), findsOneWidget);
    });

    testWidgets('one shelf per library with content, "全部" opens the wall', (
      tester,
    ) async {
      final browser = recordBrowserUrls(tester);
      final (_, router) = await pumpBrowse(tester, '/', (api) {
        api
          ..on(
            'GET /api/home',
            home(
              libraries: [movies, shows],
              shelves: [
                hubShelf(movies, 'latest', '最近添加的电影', [
                  wanderingEarth,
                  interstellar,
                ]),
              ],
            ),
          )
          ..on('GET /api/libraries/1', {
            ...movies,
            'genres': ['科幻'],
            'years': [2019],
            'categories': <Object>[],
          })
          ..on(itemsRoute(), page([wanderingEarth, interstellar]));
      });
      expect(find.text('最近添加的电影'), findsOneWidget);
      // 剧集库没有新内容，不出空片架
      expect(find.text('最近添加的剧集'), findsNothing);
      expect(find.text('星际穿越'), findsOneWidget);

      await tester.tap(find.text('全部'));
      await tester.pumpAndSettle();
      expect(location(router), '/library/1?tab=library');
      expect(browser.last.uri, '/library/1?tab=library');
      expect(find.text('共 2 部'), findsOneWidget);
    });

    testWidgets('shelves still fit with larger system text', (tester) async {
      tester.platformDispatcher.textScaleFactorTestValue = 1.3;
      addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
      await pumpBrowse(tester, '/', (api) {
        api.on(
          'GET /api/home',
          home(
            libraries: [movies],
            shelves: [
              hubShelf(movies, 'latest', '最近添加的电影', [
                wanderingEarth,
                interstellar,
                crazyAlien,
              ]),
            ],
            recent: [
              {
                'item': threeBody,
                'episode': {
                  'id': 302,
                  'season_id': 21,
                  'season_number': 1,
                  'number': 2,
                  'name': '疑云',
                },
                'still': <Object>[],
                'played_at': '2026-10-01T12:00:00Z',
              },
            ],
          ),
        );
      });
      expect(tester.takeException(), isNull);
      expect(find.text('最近添加的电影'), findsOneWidget);
      expect(find.text('第 1 季 第 2 集'), findsOneWidget);
    });

    testWidgets('recent plays come first and open the detail', (tester) async {
      final (_, router) = await pumpBrowse(tester, '/', (api) {
        api
          ..on(
            'GET /api/home',
            home(
              libraries: [movies, shows],
              shelves: [
                hubShelf(movies, 'latest', '最近添加的电影', [interstellar]),
              ],
              recent: [
                {
                  'item': threeBody,
                  'episode': {
                    'id': 302,
                    'season_id': 21,
                    'season_number': 1,
                    'number': 2,
                    'name': '疑云',
                  },
                  'still': <Object>[],
                  'played_at': '2026-10-01T12:00:00Z',
                },
                {
                  'item': wanderingEarth,
                  'episode': null,
                  'still': <Object>[],
                  'played_at': '2026-09-30T12:00:00Z',
                },
              ],
            ),
          )
          ..on('GET /api/items/20', detail(threeBody))
          ..on('GET /api/items/10', detail(wanderingEarth));
      });
      final recentTop = tester.getTopLeft(find.text('最近播放')).dy;
      expect(recentTop, lessThan(tester.getTopLeft(find.text('最近添加的电影')).dy));
      expect(find.text('第 1 季 第 2 集'), findsOneWidget);
      expect(find.text('2019 · 电影'), findsOneWidget);

      await tester.tap(find.text('第 1 季 第 2 集'));
      await tester.pumpAndSettle();
      expect(location(router), '/item/20');
    });

    testWidgets('home renders the server-ordered recommendation shelves', (
      tester,
    ) async {
      await pumpBrowse(tester, '/', (api) {
        api.on(
          'GET /api/home',
          home(
            libraries: [movies, shows],
            shelves: [
              hubShelf(movies, 'rating', '评分最高的电影', [
                interstellar,
                wanderingEarth,
              ]),
              hubShelf(shows, 'latest', '最近添加的剧集', [threeBody]),
            ],
          ),
        );
      });
      expect(find.text('评分最高的电影'), findsOneWidget);
      expect(find.text('最近添加的剧集'), findsOneWidget);
      expect(find.text('最近添加的电影'), findsNothing);
      expect(
        tester.getTopLeft(find.text('评分最高的电影')).dy,
        lessThan(tester.getTopLeft(find.text('最近添加的剧集')).dy),
      );
      expect(find.text('星际穿越'), findsOneWidget);
      expect(find.text('三体'), findsOneWidget);
    });

    testWidgets('turning every home row off does not resurrect latest', (
      tester,
    ) async {
      await pumpBrowse(tester, '/', (api) {
        api.on('GET /api/home', home(libraries: [movies]));
      });
      expect(find.text('最近添加的电影'), findsNothing);
      expect(find.text('星际穿越'), findsNothing);
    });
  });

  group('library wall', () {
    void library(FakeAdminApi api) => api.on('GET /api/libraries/1', {
      ...movies,
      'genres': ['科幻', '喜剧'],
      'years': [2019, 2014],
      'categories': <Object>[],
    });

    testWidgets('sort and filter live in the address and the request', (
      tester,
    ) async {
      final browser = recordBrowserUrls(tester);
      final (api, router) = await pumpBrowse(tester, '/library/1?tab=library', (
        api,
      ) {
        library(api);
        api
          ..on(itemsRoute(), page([crazyAlien, wanderingEarth, interstellar]))
          ..on(
            itemsRoute(sort: 'rating'),
            page([interstellar, wanderingEarth, crazyAlien]),
          )
          ..on(
            itemsRoute(sort: 'rating', order: 'asc'),
            page([crazyAlien, wanderingEarth, interstellar]),
          )
          ..on(
            itemsRoute(sort: 'rating', filters: '&genre=科幻'),
            page([interstellar, wanderingEarth]),
          )
          ..on(
            itemsRoute(sort: 'rating', filters: '&genre=科幻&year=2014'),
            page([]),
          );
      });
      expect(find.text('电影'), findsOneWidget);
      expect(find.text('共 3 部'), findsOneWidget);

      await pickMenu(tester, '最近添加', '评分');
      expect(location(router), '/library/1?tab=library&sort=rating');
      // 按评分排时海报下显示评分
      expect(find.text('8.7 分'), findsOneWidget);

      await tester.tap(find.byTooltip('评分从高到低'));
      await tester.pumpAndSettle();
      expect(location(router), '/library/1?tab=library&sort=rating&order=asc');
      await tester.tap(find.byTooltip('评分从低到高'));
      await tester.pumpAndSettle();
      expect(location(router), '/library/1?tab=library&sort=rating');

      await pickFilter(tester, '类型', '科幻');
      expect(Uri.parse(location(router)).queryParameters, {
        'tab': 'library',
        'sort': 'rating',
        'genre': '科幻',
      });
      // 地址栏也要跟着变，刷新和分享才能还原；改筛选替换当前历史，不堆返回记录
      final reported = Uri.parse(browser.last.uri);
      expect(reported.path, '/library/1');
      expect(reported.queryParameters, {
        'tab': 'library',
        'sort': 'rating',
        'genre': '科幻',
      });
      expect(browser.last.replace, isTrue);
      expect(find.text('共 2 部'), findsOneWidget);

      await pickFilter(tester, '年份', '2014');
      expect(find.text('没有符合条件的影片'), findsOneWidget);
      await tester.tap(find.widgetWithText(FilledButton, '清除筛选'));
      await tester.pumpAndSettle();
      expect(location(router), '/library/1?tab=library&sort=rating');
      expect(find.text('共 3 部'), findsOneWidget);
      expect(
        api.requests.where((r) => r.contains('/items')).toSet(),
        hasLength(5),
      );
    });

    testWidgets('a shared link restores the filters', (tester) async {
      await pumpBrowse(tester, '/library/1?tab=library&sort=name&genre=喜剧', (
        api,
      ) {
        library(api);
        api.on(
          itemsRoute(sort: 'name', order: 'asc', filters: '&genre=喜剧'),
          page([crazyAlien]),
        );
      });
      expect(find.text('名称'), findsOneWidget);
      expect(find.text('类型：喜剧'), findsOneWidget);
      expect(find.text('疯狂的外星人'), findsOneWidget);
    });

    testWidgets('scrolling to the end loads more, and a failure can retry', (
      tester,
    ) async {
      final first = [
        for (var i = 0; i < 60; i++)
          card(100 + i, '影片 $i', year: 2000 + i % 20),
      ];
      final (api, _) = await pumpBrowse(
        tester,
        '/library/1?tab=library',
        height: 800,
        (api) {
          library(api);
          api
            ..on(itemsRoute(), page(first, total: 61))
            ..on(itemsRoute(offset: 60), {
              'error': {'code': 'internal', 'message': '服务器暂时不可用'},
            }, status: 500);
        },
      );
      final scrollable = find
          .descendant(
            of: find.byType(CustomScrollView),
            matching: find.byType(Scrollable),
          )
          .first;
      await tester.scrollUntilVisible(
        find.text('服务器暂时不可用'),
        600,
        scrollable: scrollable,
      );
      await tester.pumpAndSettle();
      // 失败后不会自己反复重试
      expect(api.requests.where((r) => r.contains('offset=60')), hasLength(1));

      api.on(itemsRoute(offset: 60), page([card(999, '最后一部')], total: 61));
      await tester.tap(find.text('重试'));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(
        find.text('共 61 部'),
        600,
        scrollable: scrollable,
      );
      expect(find.text('最后一部'), findsOneWidget);
    });

    testWidgets('first page failure offers a retry', (tester) async {
      final (api, _) = await pumpBrowse(tester, '/library/1?tab=library', (
        api,
      ) {
        library(api);
      });
      expect(find.text('重试'), findsOneWidget);
      api.on(itemsRoute(), page([wanderingEarth]));
      await tester.tap(find.text('重试'));
      await tester.pumpAndSettle();
      expect(find.text('流浪地球'), findsOneWidget);
    });

    testWidgets('a library the account cannot see shows the error', (
      tester,
    ) async {
      await pumpBrowse(tester, '/library/1?tab=library', (_) {});
      expect(find.text('重试'), findsOneWidget);
      expect(find.byType(GridView), findsNothing);
    });
  });

  group('detail', () {
    testWidgets('movie shows versions and people, a person opens their page', (
      tester,
    ) async {
      final (_, router) = await pumpBrowse(tester, '/item/10', (api) {
        api
          ..on(
            'GET /api/items/10',
            detail(
              wanderingEarth,
              versions: [
                // 服务端的 label 已含大小，客户端不再拼一次
                {
                  'id': 1,
                  'label': '2160p · 3.0 GB',
                  'size': 3 * gb,
                  'subtitles': <Object>[],
                  // 内置播放器播过，带着实际的流信息
                  'media': {
                    'duration': 7500,
                    'width': 3840,
                    'height': 2160,
                    'video_codec': 'hevc',
                    'hdr': 'hdr10',
                    'audio': [
                      {
                        'language': 'chi',
                        'title': null,
                        'codec': 'aac',
                        'channels': 2,
                        'default': true,
                      },
                    ],
                    'subtitles': [
                      {
                        'language': 'eng',
                        'title': null,
                        'codec': 'subrip',
                        'forced': false,
                        'default': false,
                      },
                    ],
                  },
                },
                {
                  'id': 2,
                  'label': '1080p · 1.0 GB',
                  'size': gb,
                  'subtitles': <Object>[],
                },
              ],
            ),
          )
          ..on('GET /api/people/7', {
            'id': 7,
            'provider_id': 'builtin.tmdb',
            'remote_id': '7001',
            'name': '吴京',
            'original_name': 'Wu Jing',
            'biography': '演员、导演。',
            'birthday': '1974-04-03',
            'deathday': null,
            'place_of_birth': '中国北京',
            'profile': <Object>[],
            'detailed': true,
            'favorite': false,
            'works': [
              work(wanderingEarth, '刘培强'),
              work(crazyAlien, '大飞', directed: true),
            ],
          });
      });
      expect(find.text('流浪地球'), findsOneWidget);
      expect(find.text('2019 · 科幻 / 冒险 · 2 小时 5 分'), findsOneWidget);
      expect(find.text('2160p · 3.0 GB'), findsOneWidget);
      expect(find.text('播放'), findsOneWidget);
      expect(find.text('郭帆'), findsOneWidget);
      expect(find.text('刘慈欣'), findsOneWidget);
      expect(find.text('编剧'), findsOneWidget);
      expect(find.text('刘培强'), findsOneWidget);
      expect(find.text('中文 AAC 2.0（默认）'), findsOneWidget);
      expect(find.text('内封 · 英语（SRT）'), findsOneWidget);

      // 版本和单集页一样在底部面板里选，电影没有「自动」；画面信息在每一项的说明里
      await tester.tap(find.text('版本'));
      await tester.pumpAndSettle();
      expect(find.text('自动'), findsNothing);
      expect(find.text('4K · HEVC · HDR10'), findsOneWidget);
      await tester.tap(find.text('1080p · 1.0 GB'));
      await tester.pumpAndSettle();
      // 另一个版本还没播过，没有流信息
      expect(find.text('4K · HEVC · HDR10'), findsNothing);
      await tester.tap(find.text('播放'));
      await tester.pumpAndSettle();
      expect(find.text('流浪地球 · 1080p · 1.0 GB'), findsOneWidget);
      // 还没有记住的播放器，不能凭空标一个默认
      expect(find.text('默认'), findsNothing);
      await tester.tapAt(const Offset(10, 10));
      await tester.pumpAndSettle();

      await tester.tap(find.text('吴京'));
      await tester.pumpAndSettle();
      expect(location(router), '/person/7');
      expect(find.text('Wu Jing'), findsOneWidget);
      expect(find.text('1974年4月3日 出生 · 中国北京'), findsOneWidget);
      expect(find.text('演员、导演。'), findsOneWidget);
      expect(find.text('饰 刘培强'), findsOneWidget);
      expect(find.text('导演 · 饰 大飞'), findsOneWidget);

      await tester.tap(find.byType(BackButton));
      await tester.pumpAndSettle();
      expect(location(router), '/item/10');
    });

    testWidgets('series plays the next episode and switches seasons', (
      tester,
    ) async {
      await pumpBrowse(tester, '/item/20', (api) {
        api
          ..on(
            'GET /api/items/20',
            detail(
              threeBody,
              seasons: [season(21, 1, 2), season(22, 2, 1)],
              next: {
                'id': 301,
                'season_id': 21,
                'season_number': 1,
                'number': 1,
                'name': '科学边界',
              },
            ),
          )
          ..on(
            'GET /api/seasons/21',
            seasonDetail(21, 1, [
              episode(301, 1, '科学边界'),
              episode(302, 2, '疑云'),
            ]),
          )
          ..on(
            'GET /api/seasons/22',
            seasonDetail(22, 2, [episode(311, 1, '面壁者')]),
          );
      });
      expect(find.text('播放 第 1 季 第 1 集'), findsOneWidget);
      expect(find.text('2023 · 3 集'), findsOneWidget);
      expect(find.text('1. 科学边界'), findsOneWidget);
      expect(find.text('2. 疑云'), findsOneWidget);
      // 剧集的版本在每一集上，页头不显示
      expect(find.text('版本'), findsNothing);

      await tester.tap(find.byTooltip('选择季'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('第 2 季（1 集）'));
      await tester.pumpAndSettle();
      expect(find.text('1. 面壁者'), findsOneWidget);
      expect(find.text('2. 疑云'), findsNothing);

      await tester.tap(find.text('1. 面壁者'));
      await tester.pumpAndSettle();
      expect(find.text('面壁者'), findsOneWidget);
      expect(find.textContaining('第 2 季 第 1 集'), findsOneWidget);
      // 只有一个版本时不用选
      expect(find.text('自动'), findsNothing);
    });

    testWidgets(
      'episode page shows ratings and streams, picks a version and subtitle',
      (tester) async {
        final player = FakePlayer(platform: PlayPlatform.android);
        final (api, router) = await pumpBrowse(
          tester,
          '/item/20/episode/301?season=21',
          (api) {
            api
              ..on('GET /api/items/20', {
                ...detail(threeBody),
                'content_rating': 'TV-MA',
              })
              ..on(
                'GET /api/seasons/21',
                seasonDetail(21, 1, [
                  {
                    ...episode(
                      301,
                      1,
                      '科学边界',
                      versions: [
                        {
                          'id': 3011,
                          'label': '2160p · 8 GB',
                          'size': 8 * gb,
                          'subtitles': <Object>[],
                          'media': {
                            'duration': 2700,
                            'width': 3840,
                            'height': 2160,
                            'video_codec': 'hevc',
                            'hdr': null,
                            'audio': [
                              {
                                'language': 'chi',
                                'title': null,
                                'codec': 'aac',
                                'channels': 2,
                                'default': true,
                              },
                            ],
                            'subtitles': <Object>[],
                          },
                        },
                        {
                          'id': 3012,
                          'label': '1080p · 2 GB',
                          'size': 2 * gb,
                          'subtitles': [
                            {
                              'id': 9,
                              'label': 'chs.srt',
                              'language': 'zh',
                              'forced': false,
                              'source': 'external',
                            },
                          ],
                        },
                      ],
                    ),
                    'air_date': '2023-01-15',
                    'rating': 8.1,
                    'overview': '汪淼卷入科学家离奇自杀的调查。',
                  },
                  episode(302, 2, '疑云'),
                ]),
              )
              ..on('POST /api/play', ticket(title: '三体 第 1 集'));
          },
          player: player,
        );
        expect(find.text('三体'), findsOneWidget);
        // 页头和本季横条各一处
        expect(find.text('科学边界'), findsNWidgets(2));
        expect(find.text('第 1 季 第 1 集 · 2023年1月15日 · 45 分钟'), findsOneWidget);
        expect(find.text('TV-MA'), findsOneWidget);
        expect(find.textContaining('8.1'), findsOneWidget);
        expect(find.text('汪淼卷入科学家离奇自杀的调查。'), findsOneWidget);
        // 没选版本时展示排在最前的版本；画面信息在版本下拉每一项的说明里
        expect(find.text('自动'), findsOneWidget);
        expect(find.text('中文 AAC 2.0（默认）'), findsOneWidget);
        // 普通用户没有管理菜单
        expect(find.byTooltip('更多操作'), findsNothing);
        // 本季其他集
        expect(find.text('当前 · 第 1 集'), findsOneWidget);
        expect(find.text('疑云'), findsOneWidget);

        await tester.tap(find.text('自动'));
        await tester.pumpAndSettle();
        expect(find.text('4K · HEVC'), findsOneWidget);
        await tester.tap(find.text('1080p · 2 GB'));
        await tester.pumpAndSettle();
        // 换了没播过的版本：没有流信息，有外挂字幕可选
        expect(find.text('4K · HEVC'), findsNothing);
        await tester.tap(find.text('字幕'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('中文'));
        await tester.pumpAndSettle();
        expect(find.text('中文'), findsOneWidget);

        await tester.tap(find.text('播放'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('系统播放器'));
        await tester.pumpAndSettle();
        expect(api.bodies['POST /api/play'], {
          'version_id': 3012,
          'subtitle_id': 9,
          'client': 'android',
        });

        await tester.tap(find.text('三体'));
        await tester.pumpAndSettle();
        expect(location(router), '/item/20');
      },
    );

    testWidgets('admins edit an episode from the more menu', (tester) async {
      final (api, _) = await pumpBrowse(
        tester,
        '/item/20/episode/301?season=21',
        (api) {
          api
            ..on('GET /api/items/20', detail(threeBody))
            ..on(
              'GET /api/seasons/21',
              seasonDetail(21, 1, [episode(301, 1, '科学边界')]),
            )
            ..on('PATCH /api/admin/episodes/301/metadata', null);
        },
        overrides: [authControllerProvider.overrideWith(_SignedInAdmin.new)],
      );
      // 只有一集时不显示本季横条
      expect(find.textContaining('共 1 集'), findsNothing);
      await tester.tap(find.byTooltip('更多操作'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('编辑'));
      await tester.pumpAndSettle();
      await tester.enterText(find.widgetWithText(TextField, '标题'), '新标题');
      await tester.tap(find.text('保存'));
      await tester.pumpAndSettle();
      expect(api.bodies['PATCH /api/admin/episodes/301/metadata'], {
        'values': {'name': '新标题', 'overview': ''},
      });
    });

    testWidgets('admins edit and refresh an episode from the season list', (
      tester,
    ) async {
      final (api, _) = await pumpBrowse(tester, '/item/20', (api) {
        api
          ..on(
            'GET /api/items/20',
            detail(threeBody, seasons: [season(21, 1, 1)]),
          )
          ..on(
            'GET /api/seasons/21',
            seasonDetail(21, 1, [episode(301, 1, '科学边界')]),
          )
          ..on('PATCH /api/admin/episodes/301/metadata', null)
          ..on('POST /api/admin/episodes/301/refresh', null);
      }, overrides: [authControllerProvider.overrideWith(_SignedInAdmin.new)]);
      await tester.tap(find.byTooltip('管理单集'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('编辑'));
      await tester.pumpAndSettle();
      expect(find.text('编辑第 1 季 第 1 集'), findsOneWidget);
      await tester.enterText(find.widgetWithText(TextField, '简介'), '新简介');
      await tester.tap(find.text('保存'));
      await tester.pumpAndSettle();
      expect(api.bodies['PATCH /api/admin/episodes/301/metadata'], {
        'values': {'name': '科学边界', 'overview': '新简介'},
      });

      await tester.tap(find.byTooltip('管理单集'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('刷新元数据'));
      await tester.pumpAndSettle();
      expect(api.requests, contains('POST /api/admin/episodes/301/refresh'));
      expect(find.text('已刷新元数据'), findsOneWidget);
    });

    testWidgets('season refresh failure shows the server message', (
      tester,
    ) async {
      final (api, _) = await pumpBrowse(tester, '/item/20', (api) {
        api
          ..on(
            'GET /api/items/20',
            detail(threeBody, seasons: [season(21, 1, 1)]),
          )
          ..on(
            'GET /api/seasons/21',
            seasonDetail(21, 1, [episode(301, 1, '科学边界')]),
          )
          ..on('POST /api/admin/seasons/21/refresh', {
            'error': {'code': 'tmdb_unavailable', 'message': 'TMDB 暂时不可用'},
          }, status: 502)
          ..on('DELETE /api/admin/seasons/21?confirm=true', null, status: 202);
      }, overrides: [authControllerProvider.overrideWith(_SignedInAdmin.new)]);
      await tester.tap(find.byTooltip('季操作'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('刷新季元数据'));
      await tester.pumpAndSettle();
      expect(find.text('TMDB 暂时不可用'), findsOneWidget);

      await tester.tap(find.byTooltip('季操作'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('删除整季'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(FilledButton, '删除'));
      await tester.pumpAndSettle();
      expect(
        api.requests,
        contains('DELETE /api/admin/seasons/21?confirm=true'),
      );
      expect(find.text('删除第 1 季任务已提交'), findsOneWidget);
    });

    testWidgets('admins delete an episode from its row', (tester) async {
      final (api, _) = await pumpBrowse(tester, '/item/20', (api) {
        api
          ..on(
            'GET /api/items/20',
            detail(threeBody, seasons: [season(21, 1, 1)]),
          )
          ..on(
            'GET /api/seasons/21',
            seasonDetail(21, 1, [episode(301, 1, '科学边界')]),
          )
          ..on(
            'DELETE /api/admin/episodes/301?confirm=true',
            null,
            status: 202,
            headers: {'X-Task-Id': '9'},
          )
          ..on('GET /api/admin/tasks/9', {
            'id': 9,
            'kind': 'delete_episode',
            'phase': 'deleting_cloud',
            'status': 'succeeded',
            'progress_done': 1,
            'progress_total': 1,
            'cancel_requested': false,
            'error': null,
            'started_at': '2026-10-03T08:00:00Z',
            'finished_at': '2026-10-03T08:00:01Z',
          });
      }, overrides: [authControllerProvider.overrideWith(_SignedInAdmin.new)]);
      final fetched = api.requests
          .where((r) => r == 'GET /api/items/20')
          .length;
      await tester.tap(find.byTooltip('管理单集'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('删除单集'));
      await tester.pumpAndSettle();
      expect(find.text('删除「科学边界」？'), findsOneWidget);
      await tester.tap(find.widgetWithText(FilledButton, '删除'));
      await tester.pumpAndSettle();
      expect(
        api.requests,
        contains('DELETE /api/admin/episodes/301?confirm=true'),
      );
      // 等后台删除任务结束才刷新
      expect(api.requests, contains('GET /api/admin/tasks/9'));
      expect(find.text('已删除，来源上的文件已清理'), findsOneWidget);
      // 集数和接着看的一集都可能变了
      expect(
        api.requests.where((r) => r == 'GET /api/items/20'),
        hasLength(fetched + 1),
      );
    });

    testWidgets('admins rename a season', (tester) async {
      final (api, _) = await pumpBrowse(tester, '/item/20', (api) {
        api
          ..on(
            'GET /api/items/20',
            detail(threeBody, seasons: [season(21, 1, 1)]),
          )
          ..on(
            'GET /api/seasons/21',
            seasonDetail(21, 1, [episode(301, 1, '科学边界')]),
          )
          ..on('PATCH /api/admin/seasons/21/metadata', null);
      }, overrides: [authControllerProvider.overrideWith(_SignedInAdmin.new)]);
      await tester.tap(find.byTooltip('季操作'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('编辑季'));
      await tester.pumpAndSettle();
      await tester.enterText(find.widgetWithText(TextField, '标题'), ' 地球往事 ');
      await tester.tap(find.text('保存'));
      await tester.pumpAndSettle();
      expect(api.bodies['PATCH /api/admin/seasons/21/metadata'], {
        'values': {'name': '地球往事'},
      });
    });

    testWidgets('detail that fails to load offers a retry', (tester) async {
      await pumpBrowse(tester, '/item/404', (_) {});
      expect(find.text('重试'), findsOneWidget);
    });

    testWidgets('one rating badge per source, else fall back to TMDB', (
      tester,
    ) async {
      final (_, _) = await pumpBrowse(tester, '/item/10', (api) {
        api.on(
          'GET /api/items/10',
          detail(
            wanderingEarth,
            ratings: [
              {'source': 'builtin.tmdb', 'label': 'TMDB', 'value': 7.9},
              {'source': 'builtin.douban', 'label': '豆瓣', 'value': 8.6},
            ],
          ),
        );
      });
      expect(find.byType(SourceRatingBadge), findsNWidgets(2));
      expect(find.byType(TmdbRatingBadge), findsNothing);
      expect(find.text('TMDB 7.9', findRichText: true), findsOneWidget);
      expect(find.text('豆瓣 8.6', findRichText: true), findsOneWidget);
    });

    testWidgets('no ratings field keeps the single TMDB badge', (tester) async {
      await pumpBrowse(tester, '/item/10', (api) {
        api.on('GET /api/items/10', detail(wanderingEarth));
      });
      expect(find.byType(SourceRatingBadge), findsOneWidget);
      expect(find.byType(TmdbRatingBadge), findsOneWidget);
      expect(find.text('TMDB 7.9', findRichText: true), findsOneWidget);
    });
  });

  group('play', () {
    const movieVersions = [
      {
        'id': 1,
        'label': '2160p · 3.0 GB',
        'size': 3 * gb,
        'subtitles': <Object>[],
      },
      {'id': 2, 'label': '1080p · 1.0 GB', 'size': gb, 'subtitles': <Object>[]},
    ];
    void movie(FakeAdminApi api) => api
      ..on('GET /api/items/10', detail(wanderingEarth, versions: movieVersions))
      ..on('POST /api/play', ticket());

    Map<String, Object?> threeBodyDetail() => detail(
      threeBody,
      seasons: [season(21, 1, 2)],
      next: {
        'id': 301,
        'season_id': 21,
        'season_number': 1,
        'number': 1,
        'name': '科学边界',
      },
    );

    testWidgets('a movie sends the picked subtitle with the version', (
      tester,
    ) async {
      final player = FakePlayer(platform: PlayPlatform.android);
      final (api, _) = await pumpBrowse(tester, '/item/10', (api) {
        api
          ..on(
            'GET /api/items/10',
            detail(
              wanderingEarth,
              versions: [
                movieVersions.first,
                {
                  ...movieVersions.last,
                  'subtitles': [
                    {
                      'id': 9,
                      'label': 'chs.srt',
                      'language': 'zh',
                      'forced': false,
                      'source': 'external',
                    },
                  ],
                },
              ],
            ),
          )
          ..on('POST /api/play', ticket(versionId: 2));
      }, player: player);
      // 第一个版本没有外挂字幕
      expect(find.text('字幕'), findsNothing);
      await tester.tap(find.text('版本'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('1080p · 1.0 GB'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('字幕'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('中文'));
      await tester.pumpAndSettle();

      await tester.tap(find.text('播放'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('系统播放器'));
      await tester.pumpAndSettle();
      expect(api.bodies['POST /api/play'], {
        'version_id': 2,
        'subtitle_id': 9,
        'client': 'android',
      });
    });

    testWidgets('iOS opens the picked player with the selected version', (
      tester,
    ) async {
      final player = FakePlayer();
      final (api, _) = await pumpBrowse(
        tester,
        '/item/10',
        movie,
        player: player,
      );
      await tester.tap(find.text('版本'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('1080p · 1.0 GB'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('播放'));
      await tester.pumpAndSettle();
      for (final name in ['Infuse', 'VLC', 'nPlayer', 'SenPlayer', '复制播放链接']) {
        expect(find.text(name), findsOneWidget);
      }
      // 电影的版本在详情页选，弹层里不再出现
      expect(find.text('自动'), findsNothing);

      await tester.tap(find.text('Infuse'));
      await tester.pumpAndSettle();
      expect(api.bodies['POST /api/play'], {'version_id': 2, 'client': 'ios'});
      expect(player.launched.single.scheme, 'infuse');
      expect(
        player.launched.single.queryParameters['url'],
        'https://movie.example.com/play/tok/movie.mkv',
      );
      // 没勾记住，下次还问
      expect(await player.prefs.read(PlayPlatform.ios), isNull);
      // 播放后刷新详情：接着看的一集会变
      expect(api.requests.where((r) => r == 'GET /api/items/10'), hasLength(2));
    });

    testWidgets('a remembered player skips the picker', (tester) async {
      final player = FakePlayer();
      final (api, _) = await pumpBrowse(
        tester,
        '/item/10',
        movie,
        player: player,
      );
      await tester.tap(find.text('播放'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('记住我的选择'));
      await tester.pump();
      await tester.tap(find.text('VLC'));
      await tester.pumpAndSettle();
      expect(await player.prefs.read(PlayPlatform.ios), ExternalPlayer.vlc);
      expect(api.bodies['POST /api/play'], {'version_id': 1, 'client': 'ios'});

      await tester.tap(find.text('播放'));
      await tester.pumpAndSettle();
      expect(find.text('用什么播放'), findsNothing);
      expect(player.launched.map((u) => u.scheme), [
        'vlc-x-callback',
        'vlc-x-callback',
      ]);
    });

    testWidgets(
      'Android picks an episode version and opens the system player',
      (tester) async {
        final player = FakePlayer(platform: PlayPlatform.android);
        final (api, _) = await pumpBrowse(tester, '/item/20', (api) {
          api
            ..on('GET /api/items/20', threeBodyDetail())
            ..on(
              'GET /api/seasons/21',
              seasonDetail(21, 1, [
                episode(
                  301,
                  1,
                  '科学边界',
                  versions: [
                    {
                      'id': 3011,
                      'label': '2160p · 8 GB',
                      'size': 8 * gb,
                      'subtitles': <Object>[],
                    },
                    {
                      'id': 3012,
                      'label': '1080p · 2 GB',
                      'size': 2 * gb,
                      'subtitles': <Object>[],
                    },
                  ],
                ),
                episode(302, 2, '疑云'),
              ]),
            )
            ..on('POST /api/play', ticket(title: '三体 第 1 集'));
        }, player: player);

        await tester.tap(find.text('1. 科学边界'));
        await tester.pumpAndSettle();
        // 版本在单集页的「版本」行里选，播放器选择里不再重复列版本
        await tester.tap(find.text('自动'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('1080p · 2 GB'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('播放'));
        await tester.pumpAndSettle();
        expect(find.text('Infuse'), findsNothing);
        expect(find.text('系统播放器'), findsOneWidget);
        expect(find.text('2160p · 8 GB'), findsNothing);
        await tester.tap(find.text('系统播放器'));
        await tester.pumpAndSettle();
        expect(api.bodies['POST /api/play'], {
          'version_id': 3012,
          'client': 'android',
        });
        expect(player.launched.single.scheme, 'intent');

        // 不选版本时交给服务端：上次用过的，没有则选最大的
        await tester.tap(find.text('1080p · 2 GB'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('自动'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('播放'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('系统播放器'));
        await tester.pumpAndSettle();
        expect(api.bodies['POST /api/play'], {
          'episode_id': 301,
          'client': 'android',
        });
      },
    );

    testWidgets(
      'Android app plays in the built-in player without a public address',
      (tester) async {
        final player = FakePlayer(platform: PlayPlatform.androidApp);
        final (api, router) = await pumpBrowse(tester, '/item/10', (api) {
          api
            ..on(
              'GET /api/items/10',
              detail(wanderingEarth, versions: movieVersions),
            )
            ..on('POST /api/play', ticket(external: false, position: 754));
        }, player: player);
        await tester.tap(find.text('播放'));
        await tester.pumpAndSettle();
        expect(find.text('Infuse'), findsNothing);
        for (final name in ['内置播放器', '投屏到电视', '系统播放器', '复制播放链接']) {
          expect(find.text(name), findsOneWidget);
        }

        await tester.tap(find.text('内置播放器'));
        await tester.pumpAndSettle();
        expect(api.bodies['POST /api/play'], {
          'version_id': 1,
          'client': 'android',
          'player': 'builtin',
        });
        // 用 App 连的服务器地址拼播放地址，从上次的位置接着放
        expect(location(router), '/player');
        expect(
          find.text('playing http://test/play/tok/movie.mkv'),
          findsOneWidget,
        );
        expect(
          find.text('subtitle http://test/play/tok2/movie.zh.srt 简体中文'),
          findsOneWidget,
        );
        expect(find.text('start 754'), findsOneWidget);
        expect(player.launched, isEmpty);

        // 退出播放器后刷新详情，进度和已看是新的
        final before = api.requests.where((r) => r == 'GET /api/items/10');
        final fetched = before.length;
        await tester.tap(find.text('退出播放'));
        await tester.pumpAndSettle();
        expect(location(router), '/item/10');
        expect(
          api.requests.where((r) => r == 'GET /api/items/10'),
          hasLength(fetched + 1),
        );
      },
    );

    testWidgets('Android app hands the link to other video apps', (
      tester,
    ) async {
      final player = FakePlayer(platform: PlayPlatform.androidApp);
      final (api, _) = await pumpBrowse(
        tester,
        '/item/10',
        movie,
        player: player,
      );
      await tester.tap(find.text('播放'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('系统播放器'));
      await tester.pumpAndSettle();
      expect(api.bodies['POST /api/play'], {
        'version_id': 1,
        'client': 'android',
      });
      expect(player.intents, ['https://movie.example.com/play/tok/movie.mkv']);
      expect(player.launched, isEmpty);

      // 没有能放视频的应用时给出复制链接的出路
      player.opens = false;
      await tester.tap(find.text('播放'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('系统播放器'));
      await tester.pumpAndSettle();
      expect(find.text('没有找到能播放视频的应用，可以复制链接到其他播放器'), findsOneWidget);
      await tester.tap(find.text('复制链接'));
      await tester.pumpAndSettle();
      expect(find.text('播放链接'), findsOneWidget);
    });

    testWidgets('Android app casts to a TV and acts as its remote', (
      tester,
    ) async {
      final player = FakePlayer(platform: PlayPlatform.androidApp);
      final tv = player.renderer..position = const Duration(seconds: 754);
      final (api, router) = await pumpBrowse(tester, '/item/10', (api) {
        movie(api);
        api
          ..on('POST /api/play', ticket(position: 754))
          ..on('PUT /api/playables/1/progress', null, status: 204);
      }, player: player);
      await tester.tap(find.text('播放'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('投屏到电视'));
      await tester.pumpAndSettle();

      // 先选电视，选好了才签发
      expect(find.text('投屏'), findsOneWidget);
      expect(api.requests, isNot(contains('POST /api/play')));
      await tester.tap(find.text('客厅的小米电视'));
      await tester.pumpAndSettle();

      // 推给电视的是对外的跳转地址，电视自己请求，服务端按电视的 UA 取直链
      expect(api.bodies['POST /api/play'], {
        'version_id': 1,
        'client': 'android',
      });
      expect(location(router), '/cast');
      expect(tv.calls, [
        'load https://movie.example.com/play/tok/movie.mkv 流浪地球',
        'play',
        // 开始播放后接着上次的位置
        'seek 754',
      ]);
      expect(find.text('客厅的小米电视'), findsOneWidget);
      expect(find.text('正在播放'), findsOneWidget);
      expect(find.text('12:34'), findsOneWidget);
      expect(find.text('2:00:00'), findsOneWidget);
      expect(find.textContaining('外挂字幕投不到电视上'), findsOneWidget);
      // 投屏不能记成默认播放器
      expect(await player.prefs.read(PlayPlatform.androidApp), isNull);

      await tester.tap(find.byTooltip('快进 10 秒'));
      await tester.pump();
      expect(tv.calls.last, 'seek 764');

      await tester.tap(find.byTooltip('暂停'));
      await tester.pumpAndSettle();
      expect(tv.calls.last, 'pause');
      // 暂停时存一次进度
      expect(api.bodies['PUT /api/playables/1/progress'], {
        'position': 764,
        'duration': 7200,
        'event': 'paused',
      });

      // 电视上的状态每秒同步一次
      tv
        ..state = CastState.paused
        ..position = const Duration(seconds: 800);
      await tester.pump(const Duration(seconds: 3));
      expect(find.text('已暂停'), findsOneWidget);
      expect(find.text('13:20'), findsOneWidget);

      await tester.tap(find.text('结束投屏'));
      await tester.pumpAndSettle();
      expect(tv.calls.last, 'stop');
      expect(api.bodies['PUT /api/playables/1/progress'], {
        'position': 800,
        'duration': 7200,
        'event': 'ended',
      });
      expect(location(router), '/item/10');
    });

    testWidgets('casting asks before leaving a TV that is still playing', (
      tester,
    ) async {
      final player = FakePlayer(platform: PlayPlatform.androidApp);
      final (_, router) = await pumpBrowse(tester, '/item/10', (api) {
        movie(api);
        api.on('PUT /api/playables/1/progress', null, status: 204);
      }, player: player);
      await tester.tap(find.text('播放'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('投屏到电视'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('客厅的小米电视'));
      await tester.pumpAndSettle();

      await tester.tap(find.byTooltip('返回'));
      await tester.pumpAndSettle();
      expect(find.text('电视还在播放'), findsOneWidget);
      await tester.tap(find.text('继续放并退出'));
      await tester.pumpAndSettle();
      expect(location(router), '/item/10');
      expect(player.renderer.calls, isNot(contains('stop')));
    });

    testWidgets('leaving while the TV is still loading stops it', (
      tester,
    ) async {
      final player = FakePlayer(platform: PlayPlatform.androidApp);
      final tv = player.renderer..loading = Completer<void>();
      final (_, router) = await pumpBrowse(tester, '/item/10', (api) {
        movie(api);
        api.on('PUT /api/playables/1/progress', null, status: 204);
      }, player: player);
      await tester.tap(find.text('播放'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('投屏到电视'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('客厅的小米电视'));
      for (var i = 0; i < 5; i++) {
        await tester.pump(const Duration(milliseconds: 200));
      }
      expect(find.text('正在连接电视…'), findsOneWidget);

      // 还没开始放，不用确认，直接让电视停下
      await tester.tap(find.byTooltip('返回'));
      await tester.pump();
      expect(find.text('电视还在播放'), findsNothing);
      expect(tv.calls.last, 'stop');

      // 电视这时才加载完，也不能再开始放
      tv.loading!.complete();
      await tester.pumpAndSettle();
      expect(tv.calls, isNot(contains('play')));
      expect(location(router), '/item/10');
    });

    testWidgets('casting explains a TV that stops or refuses the video', (
      tester,
    ) async {
      final player = FakePlayer(platform: PlayPlatform.androidApp);
      final tv = player.renderer
        ..loadError = const CastException('电视拒绝了操作（714 Illegal MIME-type）');
      await pumpBrowse(tester, '/item/10', movie, player: player);
      await tester.tap(find.text('播放'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('投屏到电视'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('客厅的小米电视'));
      await tester.pumpAndSettle();
      expect(find.text('没能投到电视'), findsOneWidget);
      expect(find.text('电视拒绝了操作（714 Illegal MIME-type）'), findsOneWidget);

      // 重试成功后，电视那边停了（放完或者用遥控器停的）
      tv.loadError = null;
      await tester.tap(find.text('重试'));
      await tester.pumpAndSettle();
      expect(find.text('正在播放'), findsOneWidget);
      tv.state = CastState.stopped;
      await tester.pump(const Duration(seconds: 2));
      await tester.pumpAndSettle();
      expect(find.text('电视已停止播放'), findsOneWidget);
      expect(find.text('重新投屏'), findsOneWidget);
    });

    testWidgets('a TV that drops the play command but starts playing is fine', (
      tester,
    ) async {
      final player = FakePlayer(platform: PlayPlatform.androidApp);
      player.renderer.playError = const CastException('连不上电视');
      await pumpBrowse(tester, '/item/10', movie, player: player);
      await tester.tap(find.text('播放'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('投屏到电视'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('客厅的小米电视'));
      await tester.pumpAndSettle();
      expect(find.text('正在播放'), findsOneWidget);
      expect(find.text('没能投到电视'), findsNothing);
    });

    testWidgets('a TV that refuses the play command is reported', (
      tester,
    ) async {
      final player = FakePlayer(platform: PlayPlatform.androidApp);
      player.renderer.playError = const CastException(
        '电视拒绝了操作（701 Transition not available）',
        fault: true,
      );
      await pumpBrowse(tester, '/item/10', movie, player: player);
      await tester.tap(find.text('播放'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('投屏到电视'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('客厅的小米电视'));
      await tester.pumpAndSettle();
      expect(find.text('没能投到电视'), findsOneWidget);
    });

    testWidgets('no TV found explains why and searches again', (tester) async {
      final player = FakePlayer(platform: PlayPlatform.androidApp);
      player.discovery.devices = [];
      final (api, _) = await pumpBrowse(
        tester,
        '/item/10',
        movie,
        player: player,
      );
      await tester.tap(find.text('播放'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('投屏到电视'));
      await tester.pumpAndSettle();
      expect(find.text('未发现可投屏的设备'), findsOneWidget);
      expect(find.textContaining('同一个 Wi-Fi'), findsWidgets);

      player.discovery.devices = [livingRoomTv];
      await tester.tap(find.text('重新搜索'));
      await tester.pumpAndSettle();
      expect(player.discovery.searches, 2);
      expect(find.text('客厅的小米电视'), findsOneWidget);
      // 关掉不投，也就不签发
      await tester.tap(find.byTooltip('关闭'));
      await tester.pumpAndSettle();
      expect(api.requests, isNot(contains('POST /api/play')));
    });

    testWidgets('built-in player hands over to a TV from where it stopped', (
      tester,
    ) async {
      final player = FakePlayer(platform: PlayPlatform.androidApp);
      final (api, router) = await pumpBrowse(tester, '/item/10', (api) {
        api
          ..on(
            'GET /api/items/10',
            detail(wanderingEarth, versions: movieVersions),
          )
          ..on('POST /api/play', ticket(external: false, versionId: 2));
      }, player: player);
      await tester.tap(find.text('播放'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('内置播放器'));
      await tester.pumpAndSettle();
      expect(location(router), '/player');

      api.on('POST /api/play', ticket(versionId: 2, position: 90));
      await tester.tap(find.text('转到电视'));
      await tester.pumpAndSettle();
      // 按刚才放的版本重新签发对外地址
      expect(api.bodies['POST /api/play'], {
        'version_id': 2,
        'client': 'android',
      });
      expect(location(router), '/cast');
      expect(player.renderer.calls, contains('seek 90'));
    });

    testWidgets('desktop shows the links to copy', (tester) async {
      final copied = <String>[];
      tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        SystemChannels.platform,
        (call) async {
          if (call.method == 'Clipboard.setData') {
            copied.add((call.arguments as Map)['text'] as String);
          }
          return null;
        },
      );
      addTearDown(
        () => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
          SystemChannels.platform,
          null,
        ),
      );
      final player = FakePlayer(platform: PlayPlatform.desktop);
      final (api, _) = await pumpBrowse(
        tester,
        '/item/10',
        movie,
        player: player,
      );
      await tester.tap(find.text('播放'));
      await tester.pumpAndSettle();
      // 只有复制链接一种方式，不用再选
      expect(find.text('用什么播放'), findsNothing);
      expect(api.bodies['POST /api/play'], {'version_id': 1, 'client': 'web'});
      expect(find.text('播放链接'), findsOneWidget);
      expect(find.textContaining('24 小时内有效'), findsOneWidget);
      expect(find.text('字幕 · 简体中文'), findsOneWidget);

      await tester.tap(find.byTooltip('复制视频链接'));
      await tester.pump();
      expect(copied, ['https://movie.example.com/play/tok/movie.mkv']);
      expect(find.byTooltip('已复制'), findsOneWidget);
      await tester.tap(find.byTooltip('复制字幕 · 简体中文链接'));
      await tester.pump();
      expect(copied.last, 'https://movie.example.com/play/tok2/movie.zh.srt');
      await tester.pump(const Duration(seconds: 2));
      expect(player.launched, isEmpty);
    });

    testWidgets('issue failures and launch failures explain what to do', (
      tester,
    ) async {
      final player = FakePlayer(opens: false);
      final (api, _) = await pumpBrowse(tester, '/item/10', (api) {
        api
          ..on(
            'GET /api/items/10',
            detail(wanderingEarth, versions: movieVersions),
          )
          ..on('POST /api/play', {
            'error': {
              'code': 'public_url_not_configured',
              'message': '管理员还没有配置对外访问地址，暂时不能播放',
            },
          }, status: 409);
      }, player: player);
      await tester.tap(find.text('播放'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Infuse'));
      await tester.pumpAndSettle();
      expect(find.text('管理员还没有配置对外访问地址，暂时不能播放'), findsOneWidget);
      expect(find.text('正在获取播放地址…'), findsNothing);
      expect(player.launched, isEmpty);

      api.on('POST /api/play', ticket());
      await tester.tap(find.text('播放'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Infuse'));
      await tester.pumpAndSettle();
      expect(player.launched, hasLength(1));
      expect(find.text('没能打开 Infuse，可以复制链接到其他播放器'), findsOneWidget);
      await tester.tap(find.text('复制链接'));
      await tester.pumpAndSettle();
      expect(find.text('播放链接'), findsOneWidget);
    });

    testWidgets('a series without playable episodes cannot play', (
      tester,
    ) async {
      await pumpBrowse(tester, '/item/20', (api) {
        api.on('GET /api/items/20', detail(threeBody));
      });
      final button = tester.widget<FilledButton>(
        find.ancestor(
          of: find.text('播放'),
          matching: find.bySubtype<FilledButton>(),
        ),
      );
      expect(button.onPressed, isNull);
    });

    testWidgets('the default player can be changed under 设置 › 播放器', (
      tester,
    ) async {
      final player = FakePlayer();
      await player.prefs.write(PlayPlatform.ios, ExternalPlayer.infuse);
      await pumpAdmin(
        tester,
        const PlayerSettingsPage(),
        (_) {},
        overrides: [
          authControllerProvider.overrideWith(_SignedInAdmin.new),
          ...player.overrides,
        ],
      );
      expect(find.text('默认播放器'), findsOneWidget);
      expect(find.text('Infuse'), findsOneWidget);

      await tester.tap(find.text('默认播放器'));
      await tester.pumpAndSettle();
      // 复制链接不算播放器，不能设为默认
      expect(find.text('复制播放链接'), findsNothing);
      await tester.tap(find.text('每次询问'));
      await tester.pumpAndSettle();
      expect(await player.prefs.read(PlayPlatform.ios), isNull);
      expect(find.text('每次询问'), findsOneWidget);
    });

    testWidgets('desktop has no default player setting', (tester) async {
      await pumpAdmin(
        tester,
        const PlayerSettingsPage(),
        (_) {},
        overrides: [
          authControllerProvider.overrideWith(_SignedInAdmin.new),
          ...FakePlayer(platform: PlayPlatform.desktop).overrides,
        ],
      );
      expect(find.text('默认播放器'), findsNothing);
      expect(find.text('这个平台没有可选的播放器'), findsOneWidget);
    });
  });

  group('watched', () {
    testWidgets('a movie toggles watched and the detail refreshes', (
      tester,
    ) async {
      final (api, _) = await pumpBrowse(tester, '/item/10', (api) {
        api
          ..on('GET /api/items/10', detail(wanderingEarth))
          ..on('PUT /api/items/10/watched', null, status: 204);
      });
      api.on(
        'GET /api/items/10',
        detail(card(10, '流浪地球', year: 2019, watched: true)),
      );
      await tester.tap(find.byTooltip('标记已看'));
      await tester.pumpAndSettle();
      expect(api.requests, contains('PUT /api/items/10/watched'));
      expect(find.byTooltip('标记未看'), findsOneWidget);

      api.on('DELETE /api/items/10/watched', {
        'error': {'code': 'not_found', 'message': '找不到这部作品'},
      }, status: 404);
      await tester.tap(find.byTooltip('标记未看'));
      await tester.pumpAndSettle();
      expect(find.text('找不到这部作品'), findsOneWidget);
      expect(find.byTooltip('标记未看'), findsOneWidget);
    });

    testWidgets('episodes and whole seasons toggle watched', (tester) async {
      final (api, _) = await pumpBrowse(tester, '/item/20', (api) {
        api
          ..on(
            'GET /api/items/20',
            detail(threeBody, seasons: [season(21, 1, 2)]),
          )
          ..on(
            'GET /api/seasons/21',
            seasonDetail(21, 1, [
              episode(301, 1, '科学边界', watched: true),
              episode(302, 2, '疑云'),
            ]),
          )
          ..on('PUT /api/episodes/302/watched', null, status: 204)
          ..on('DELETE /api/seasons/21/watched', null, status: 204);
      });
      // 集和整部剧的按钮都叫「标记已看」，集的在列表里
      final rows = find.descendant(
        of: find.byType(SliverList),
        matching: find.byType(IconButton),
      );
      expect(
        find.descendant(of: rows, matching: find.byIcon(Icons.check_circle)),
        findsOneWidget,
      );

      api.on(
        'GET /api/seasons/21',
        seasonDetail(21, 1, [
          episode(301, 1, '科学边界', watched: true),
          episode(302, 2, '疑云', watched: true),
        ]),
      );
      await tester.tap(
        find.descendant(
          of: find.byType(SliverList),
          matching: find.byTooltip('标记已看'),
        ),
      );
      await tester.pumpAndSettle();
      expect(api.requests, contains('PUT /api/episodes/302/watched'));
      expect(
        find.descendant(of: rows, matching: find.byIcon(Icons.check_circle)),
        findsNWidgets(2),
      );
      // 首页的最近播放和整部剧的状态都要跟着刷新
      expect(api.requests.where((r) => r == 'GET /api/items/20'), hasLength(2));

      await tester.tap(find.byTooltip('季操作'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('整季标记未看'));
      await tester.pumpAndSettle();
      expect(api.requests, contains('DELETE /api/seasons/21/watched'));
    });

    testWidgets('the wall filters by watched state and marks watched posters', (
      tester,
    ) async {
      final (api, router) = await pumpBrowse(tester, '/library/1?tab=library', (
        api,
      ) {
        api
          ..on('GET /api/libraries/1', {
            ...movies,
            'genres': <String>[],
            'years': <int>[],
            'categories': <Object>[],
          })
          ..on(
            itemsRoute(),
            page([card(10, '流浪地球', year: 2019, watched: true), interstellar]),
          )
          ..on(itemsRoute(filters: '&watched=false'), page([interstellar]));
      });
      expect(find.bySemanticsLabel('流浪地球，2019，已看'), findsOneWidget);
      expect(find.byIcon(Icons.check), findsOneWidget);

      await pickFilter(tester, '观看状态', '未看');
      expect(location(router), '/library/1?tab=library&watched=false');
      expect(api.requests.last, itemsRoute(filters: '&watched=false'));
      expect(find.text('流浪地球'), findsNothing);

      await tester.tap(find.widgetWithText(TextButton, '全部清除'));
      await tester.pumpAndSettle();
      expect(location(router), '/library/1?tab=library');
    });

    testWidgets('a shared link restores the watched filter', (tester) async {
      await pumpBrowse(tester, '/library/1?tab=library&watched=true', (api) {
        api
          ..on('GET /api/libraries/1', {
            ...movies,
            'genres': <String>[],
            'years': <int>[],
            'categories': <Object>[],
          })
          ..on(
            itemsRoute(filters: '&watched=true'),
            page([card(10, '流浪地球', watched: true)]),
          );
      });
      expect(find.text('已看'), findsOneWidget);
      expect(find.text('流浪地球'), findsOneWidget);
    });

    testWidgets('the wall filters by favorites and keeps it in the address', (
      tester,
    ) async {
      final (api, router) = await pumpBrowse(
        tester,
        '/library/1?tab=library&favorite=true',
        (api) {
          api
            ..on('GET /api/libraries/1', {
              ...movies,
              'genres': <String>[],
              'years': <int>[],
              'categories': <Object>[],
            })
            ..on(
              itemsRoute(filters: '&favorite=true'),
              page([card(10, '流浪地球')]),
            )
            ..on(itemsRoute(), page([card(10, '流浪地球'), interstellar]));
        },
      );
      expect(find.text('已收藏'), findsOneWidget);
      expect(find.text('流浪地球'), findsOneWidget);
      expect(api.requests.last, itemsRoute(filters: '&favorite=true'));

      await pickFilter(tester, '收藏', '全部');
      expect(location(router), '/library/1?tab=library');
      expect(api.requests.last, itemsRoute());
    });
  });

  group('person', () {
    testWidgets('without provider details only the name and works show', (
      tester,
    ) async {
      await pumpBrowse(tester, '/person/9', (api) {
        api.on('GET /api/people/9', {
          'id': 9,
          'provider_id': 'builtin.tmdb',
          'remote_id': '9',
          'name': '张鲁一',
          'original_name': null,
          'biography': null,
          'birthday': null,
          'deathday': null,
          'place_of_birth': null,
          'profile': <Object>[],
          'detailed': false,
          'favorite': false,
          'works': [work(threeBody, '罗辑')],
        });
      });
      expect(find.text('张鲁一'), findsWidgets);
      expect(find.textContaining('出生'), findsNothing);
      expect(find.text('饰 罗辑'), findsOneWidget);
    });

    testWidgets('no visible works explains why', (tester) async {
      await pumpBrowse(tester, '/person/9', (api) {
        api.on('GET /api/people/9', {
          'id': 9,
          'provider_id': 'builtin.tmdb',
          'remote_id': '9',
          'name': '张鲁一',
          'original_name': null,
          'biography': null,
          'birthday': null,
          'deathday': null,
          'place_of_birth': null,
          'profile': <Object>[],
          'detailed': false,
          'favorite': false,
          'works': <Object>[],
        });
      });
      expect(find.text('资料库里还没有这个人的作品'), findsOneWidget);
    });
  });

  group('search', () {
    testWidgets(
      'searches after typing stops and keeps the query in the address',
      (tester) async {
        final browser = recordBrowserUrls(tester);
        final (api, router) = await pumpBrowse(tester, '/search', (api) {
          api
            ..on(
              'GET /api/search?q=地球&offset=0&limit=60',
              page(
                [wanderingEarth],
                people: [
                  {
                    'id': 9,
                    'name': '吴京',
                    'original_name': 'Wu Jing',
                    'profile': <Object>[],
                  },
                ],
                episodes: [
                  {
                    'id': 91,
                    'item_id': 20,
                    'season_id': 21,
                    'season_number': 1,
                    'number': 2,
                    'name': '第二集',
                    'show_title': '三体',
                    'still': <Object>[],
                  },
                ],
              ),
            )
            ..on('GET /api/search?q=不存在&offset=0&limit=60', page([]));
        });
        expect(find.text('搜索影片和剧集'), findsOneWidget);

        await tester.enterText(find.byType(TextField), '地');
        await tester.pump(const Duration(milliseconds: 100));
        await tester.enterText(find.byType(TextField), '地球');
        await tester.pump(const Duration(milliseconds: 100));
        expect(api.requests, isEmpty);
        await tester.pump(const Duration(milliseconds: 300));
        await tester.pumpAndSettle();
        expect(api.requests, ['GET /api/search?q=地球&offset=0&limit=60']);
        expect(Uri.parse(location(router)).queryParameters, {'q': '地球'});
        expect(Uri.parse(browser.last.uri).queryParameters, {'q': '地球'});
        expect(browser.last.replace, isTrue);
        expect(find.text('流浪地球'), findsOneWidget);
        expect(find.text('吴京'), findsOneWidget);
        expect(find.text('三体 · 第二集'), findsOneWidget);

        await tester.enterText(find.byType(TextField), '不存在');
        await tester.pump(const Duration(milliseconds: 300));
        await tester.pumpAndSettle();
        expect(find.text('没有找到「不存在」'), findsOneWidget);

        await tester.tap(find.byTooltip('清除'));
        await tester.pumpAndSettle();
        expect(location(router), '/search');
        expect(find.text('搜索影片和剧集'), findsOneWidget);
      },
    );

    testWidgets('opening a search link runs it right away', (tester) async {
      await pumpBrowse(tester, '/search?q=地球', (api) {
        api.on(
          'GET /api/search?q=地球&offset=0&limit=60',
          page([wanderingEarth]),
        );
      });
      expect(find.text('流浪地球'), findsOneWidget);
      expect(
        tester.widget<TextField>(find.byType(TextField)).controller!.text,
        '地球',
      );
    });
  });

  testWidgets('admin assigns libraries to a member', (tester) async {
    final api = await pumpAdmin(tester, const UsersPage(), (api) {
      api
        ..on('GET /api/admin/users', [
          {
            'id': 1,
            'username': 'admin',
            'role': 'admin',
            'disabled': false,
            'allow_subtitle_download': true,
            'created_at': '',
          },
          {
            'id': 2,
            'username': 'alice',
            'role': 'user',
            'disabled': false,
            'allow_subtitle_download': true,
            'created_at': '',
          },
        ])
        ..on('GET /api/admin/libraries', [
          {
            'id': 1,
            'name': '院线电影',
            'kind': 'movie',
            'folders': <Object>[],
            'video_count': 3,
            'missing_count': 0,
            'metadata_provider': 'builtin.tmdb',
            'cover': <Object>[],
            'cover_custom': false,
          },
          {
            'id': 2,
            'name': '国产剧',
            'kind': 'series',
            'folders': <Object>[],
            'video_count': 4,
            'missing_count': 0,
            'metadata_provider': 'builtin.tmdb',
            'cover': <Object>[],
            'cover_custom': false,
          },
        ])
        ..on('GET /api/admin/users/2/libraries', {
          'library_ids': [1],
        })
        ..on('PUT /api/admin/users/2/libraries', {
          'library_ids': [1, 2],
        });
    }, overrides: [authControllerProvider.overrideWith(_SignedInAdmin.new)]);

    // 管理员总能看到全部资料库，菜单里没有这一项
    await tester.tap(find.byTooltip('管理 admin'));
    await tester.pumpAndSettle();
    expect(find.text('资料库权限'), findsNothing);
    await tester.tapAt(const Offset(10, 10));
    await tester.pumpAndSettle();

    await tester.tap(find.byTooltip('管理 alice'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('资料库权限'));
    await tester.pumpAndSettle();
    expect(find.text('alice 能看到的资料库'), findsOneWidget);
    final movieBox = find.widgetWithText(CheckboxListTile, '院线电影');
    final showBox = find.widgetWithText(CheckboxListTile, '国产剧');
    expect(tester.widget<CheckboxListTile>(movieBox).value, isTrue);
    expect(tester.widget<CheckboxListTile>(showBox).value, isFalse);

    // 没改动直接保存不发请求
    await tester.tap(find.text('保存'));
    await tester.pumpAndSettle();
    expect(api.requests.where((r) => r.startsWith('PUT')), isEmpty);

    await tester.tap(find.byTooltip('管理 alice'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('资料库权限'));
    await tester.pumpAndSettle();
    await tester.tap(showBox);
    await tester.pump();
    await tester.tap(find.text('保存'));
    await tester.pumpAndSettle();
    expect(api.bodies['PUT /api/admin/users/2/libraries'], {
      'library_ids': [1, 2],
      'all_libraries': false,
    });
    expect(find.text('资料库权限已更新，该用户需要重新登录'), findsOneWidget);

    // 打开「全部资料库」后勾选框不可用，以后新建的资料库也能看到
    await tester.tap(find.byTooltip('管理 alice'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('资料库权限'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('全部资料库'));
    await tester.pump();
    expect(tester.widget<CheckboxListTile>(movieBox).onChanged, isNull);
    await tester.tap(find.text('保存'));
    await tester.pumpAndSettle();
    expect(api.bodies['PUT /api/admin/users/2/libraries'], {
      'library_ids': [1],
      'all_libraries': true,
    });
  });

  group('focus', () {
    testWidgets('keyboard focus enlarges a tile, touch does not', (
      tester,
    ) async {
      var taps = 0;
      await tester.pumpWidget(
        MaterialApp(
          home: Center(
            child: FocusTile(
              semanticLabel: '奥本海默',
              onTap: () => taps++,
              builder: (_, _) => const SizedBox.square(dimension: 80),
            ),
          ),
        ),
      );
      double scale() => tester
          .widget<AnimatedScale>(
            find
                .descendant(
                  of: find.byType(FocusTile),
                  matching: find.byType(AnimatedScale),
                )
                .last,
          )
          .scale;

      await tester.tap(find.byType(FocusTile));
      await tester.pumpAndSettle();
      expect(taps, 1);
      expect(scale(), 1);

      await tester.sendKeyEvent(LogicalKeyboardKey.tab);
      await tester.pumpAndSettle();
      expect(scale(), 1.08);

      // 遥控器的确定键和回车都能触发
      await tester.sendKeyEvent(LogicalKeyboardKey.enter);
      await tester.pumpAndSettle();
      expect(taps, 2);
    });

    testWidgets('enter opens a tile on the web too', (tester) async {
      var taps = 0;
      await tester.pumpWidget(
        MaterialApp(
          // 模拟 Web 的默认映射：回车只发 ButtonActivateIntent
          shortcuts: {
            ...WidgetsApp.defaultShortcuts,
            const SingleActivator(LogicalKeyboardKey.enter):
                const ButtonActivateIntent(),
          },
          home: Center(
            child: FocusTile(
              semanticLabel: '繁花',
              onTap: () => taps++,
              builder: (_, _) => const SizedBox.square(dimension: 80),
            ),
          ),
        ),
      );
      await tester.sendKeyEvent(LogicalKeyboardKey.tab);
      await tester.sendKeyEvent(LogicalKeyboardKey.enter);
      await tester.pump();
      expect(taps, 1);
    });

    testWidgets('arrow keys move focus between tiles like a TV remote', (
      tester,
    ) async {
      final focused = <String>[];
      Widget tile(String name) => FocusTile(
        semanticLabel: name,
        onTap: () {},
        builder: (_, highlight) {
          if (highlight.focused) focused.add(name);
          return const SizedBox.square(dimension: 80);
        },
      );
      await tester.pumpWidget(
        MaterialApp(
          shortcuts: appShortcuts,
          home: Center(
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [tile('繁花'), tile('三体')],
            ),
          ),
        ),
      );
      await tester.sendKeyEvent(LogicalKeyboardKey.tab);
      await tester.pumpAndSettle();
      expect(focused.last, '繁花');
      await tester.sendKeyEvent(LogicalKeyboardKey.arrowRight);
      await tester.pumpAndSettle();
      expect(focused.last, '三体');
    });
  });

  group('progress', () {
    testWidgets('half-watched plays are called 继续观看 with time left', (
      tester,
    ) async {
      await pumpBrowse(tester, '/', (api) {
        api.on(
          'GET /api/home',
          home(
            libraries: [movies],
            recent: [
              {
                'item': wanderingEarth,
                'episode': null,
                'still': <Object>[],
                'played_at': '2026-10-01T12:00:00Z',
                'progress': progressAt(1800, 7500),
              },
            ],
          ),
        );
      });
      expect(find.text('继续观看'), findsOneWidget);
      expect(find.text('最近播放'), findsNothing);
      expect(find.text('2019 · 电影 · 剩 95 分钟'), findsOneWidget);
      expect(find.byType(ProgressLine), findsOneWidget);
    });

    testWidgets('a half-watched movie resumes from the play button', (
      tester,
    ) async {
      await pumpBrowse(tester, '/item/10', (api) {
        api.on(
          'GET /api/items/10',
          detail(wanderingEarth, progress: progressAt(600, 3600)),
        );
      });
      expect(find.text('继续播放'), findsOneWidget);
      expect(find.text('剩 50 分钟'), findsOneWidget);
      final line = tester.widget<ProgressLine>(find.byType(ProgressLine));
      expect(line.value, closeTo(1 / 6, 0.001));
    });

    testWidgets('episodes show how far they were watched', (tester) async {
      await pumpBrowse(tester, '/item/20', (api) {
        api
          ..on(
            'GET /api/items/20',
            detail(
              threeBody,
              seasons: [season(21, 1, 2)],
              next: {
                'id': 301,
                'season_id': 21,
                'season_number': 1,
                'number': 1,
                'name': '科学边界',
                'progress': progressAt(1200, 2700),
              },
            ),
          )
          ..on(
            'GET /api/seasons/21',
            seasonDetail(21, 1, [
              episode(301, 1, '科学边界', progress: progressAt(1200, 2700)),
              episode(302, 2, '疑云'),
            ]),
          );
      });
      expect(find.text('继续播放 第 1 集'), findsOneWidget);
      // 按钮下一条，第 1 集行内一条；看了一半的第 1 集标「正在看」
      expect(find.text('剩 25 分钟'), findsOneWidget);
      expect(find.text('正在看 · 剩 25 分钟'), findsOneWidget);
      expect(find.byType(ProgressLine), findsNWidgets(2));
      expect(find.text('45 分钟'), findsOneWidget);
    });
  });

  group('tv', () {
    /// 焦点是否落在这段文字所在的按钮上
    bool focusedOn(WidgetTester tester, Finder label) =>
        Focus.of(tester.element(label)).hasPrimaryFocus;

    testWidgets('TV mode adds the overscan margin and readable text', (
      tester,
    ) async {
      const phone = MediaQueryData(
        size: Size(960, 540),
        padding: EdgeInsets.only(top: 4),
      );
      final tv = televisionMediaQuery(phone);
      expect(tv.navigationMode, NavigationMode.directional);
      expect(tv.padding, const EdgeInsets.fromLTRB(48, 31, 48, 27));
      // 最小的字（12）放大到 14
      expect(tv.textScaler.scale(12), closeTo(14, 0.01));
      // 系统字号已经更大时不缩小
      final large = televisionMediaQuery(
        phone.copyWith(textScaler: const TextScaler.linear(1.5)),
      );
      expect(large.textScaler.scale(12), 18);
      // 输入法盖住底边时不再留底部过扫描
      final typing = televisionMediaQuery(
        phone.copyWith(viewInsets: const EdgeInsets.only(bottom: 260)),
      );
      expect(typing.padding, const EdgeInsets.fromLTRB(48, 31, 48, 0));
    });

    testWidgets('TV plays with the built-in or system player only', (
      tester,
    ) async {
      final container = ProviderContainer(
        overrides: [televisionProvider.overrideWithValue(true)],
      );
      addTearDown(container.dispose);
      expect(container.read(playPlatformProvider), PlayPlatform.androidTv);

      final player = FakePlayer(platform: PlayPlatform.androidTv);
      final (api, _) = await pumpBrowse(
        tester,
        '/item/10',
        (api) => api
          ..on('GET /api/items/10', detail(wanderingEarth))
          ..on('POST /api/play', ticket()),
        player: player,
        television: true,
      );
      // 剧照铺满，正文让开过扫描区
      expect(tester.getTopLeft(find.text('流浪地球')).dx, 48 + 32);
      // 详情页进入时焦点在主按钮上，确定键就能播放
      expect(focusedOn(tester, find.text('播放')), isTrue);
      await tester.sendKeyEvent(LogicalKeyboardKey.select);
      await tester.pumpAndSettle();
      // 电视上没法粘贴，不给复制链接
      expect(find.text('内置播放器'), findsOneWidget);
      expect(find.text('系统播放器'), findsOneWidget);
      expect(find.text('复制播放链接'), findsNothing);
      // 没有默认播放器时焦点在第一个，按确定就能播
      expect(focusedOn(tester, find.text('内置播放器')), isTrue);

      player.opens = false;
      await tester.tap(find.text('系统播放器'));
      await tester.pumpAndSettle();
      expect(api.bodies['POST /api/play'], {
        'item_id': 10,
        'client': 'android',
      });
      expect(find.text('没有找到能播放视频的应用，可以改用内置播放器'), findsOneWidget);
      expect(find.text('复制链接'), findsNothing);
    });

    testWidgets('the overview above the play button shows focus', (
      tester,
    ) async {
      await pumpBrowse(
        tester,
        '/item/10',
        (api) => api.on('GET /api/items/10', detail(wanderingEarth)),
        television: true,
      );
      final ring = find.byWidgetPredicate(
        (w) =>
            w is DecoratedBox &&
            w.decoration is BoxDecoration &&
            (w.decoration as BoxDecoration).border ==
                Border.all(color: CinemaColors.amber, width: 2),
      );
      final overview = find.byType(ExpandableText);
      expect(find.descendant(of: overview, matching: ring), findsNothing);
      await tester.sendKeyEvent(LogicalKeyboardKey.arrowUp);
      await tester.pumpAndSettle();
      expect(find.descendant(of: overview, matching: ring), findsOneWidget);
    });

    testWidgets('home shelves keep their ends inside the overscan margin', (
      tester,
    ) async {
      await pumpBrowse(
        tester,
        '/',
        (api) => api
          ..on(
            'GET /api/home',
            home(
              libraries: [movies],
              shelves: [
                hubShelf(movies, 'latest', '最近添加的电影', [
                  wanderingEarth,
                  interstellar,
                ]),
              ],
            ),
          ),
        television: true,
      );
      // 片架第一张从安全区开始，「全部」停在右侧安全区内
      expect(tester.getTopLeft(find.text('流浪地球')).dx, 48 + 32);
      expect(tester.getTopRight(find.text('全部')).dx, lessThan(960 - 48));
    });

    testWidgets('the wall starts on the first poster, the menus show where '
        'focus is', (tester) async {
      await pumpBrowse(
        tester,
        '/library/1?tab=library',
        (api) => api
          ..on('GET /api/libraries/1', {
            ...movies,
            'genres': <String>[],
            'years': <int>[],
            'categories': <Object>[],
          })
          ..on(itemsRoute(), page([wanderingEarth, interstellar])),
        television: true,
      );
      expect(focusedOn(tester, find.text('流浪地球')), isTrue);
      // 网格右端停在安全区内
      expect(
        tester.getTopRight(find.text('共 2 部')).dx,
        lessThanOrEqualTo(960 - 48),
      );

      // 遥控器从第一张海报往上落在工具栏最左边的「筛选」上
      Decoration? ring() => tester
          .widget<Container>(
            find
                .ancestor(of: find.text('筛选'), matching: find.byType(Container))
                .first,
          )
          .foregroundDecoration;
      expect(ring(), isNull);
      await tester.sendKeyEvent(LogicalKeyboardKey.arrowUp);
      await tester.pumpAndSettle();
      expect(ring(), isNotNull);
    });

    testWidgets('phones do not grab focus on the home page', (tester) async {
      await pumpBrowse(
        tester,
        '/',
        (api) => api.on(
          'GET /api/home',
          home(
            shelves: [
              hubShelf(movies, 'latest', '最近添加的电影', [wanderingEarth]),
            ],
          ),
        ),
      );
      // 首页没有默认焦点，遥控器进来之前焦点只停在路由的焦点域上
      expect(FocusManager.instance.primaryFocus, isA<FocusScopeNode>());
    });

    testWidgets('up and down leave a text field', (tester) async {
      await tester.pumpWidget(
        MaterialApp(
          shortcuts: appShortcuts,
          builder: televisionBuilder,
          home: Scaffold(
            body: Column(
              children: [
                TextButton(onPressed: () {}, child: const Text('上面')),
                const TextField(autofocus: true),
                TextButton(onPressed: () {}, child: const Text('下面')),
              ],
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.sendKeyEvent(LogicalKeyboardKey.arrowDown);
      await tester.pumpAndSettle();
      expect(focusedOn(tester, find.text('下面')), isTrue);
      await tester.sendKeyEvent(LogicalKeyboardKey.arrowUp);
      await tester.sendKeyEvent(LogicalKeyboardKey.arrowUp);
      await tester.pumpAndSettle();
      expect(focusedOn(tester, find.text('上面')), isTrue);
    });
  });

  group('playlists and collections', () {
    Map<String, Object?> playlist(int id, String title, int owner) => {
      'id': id,
      'user_id': owner,
      'title': title,
      'shared': owner != 2,
    };

    testWidgets('detail adds to an own playlist or a new one', (tester) async {
      final (api, _) = await pumpBrowse(tester, '/item/10', (api) {
        api
          ..on('GET /api/items/10', detail(wanderingEarth))
          ..on('GET /api/playlists', [
            playlist(1, '周末', 2),
            playlist(2, '别人的片单', 3),
          ])
          ..on('POST /api/playlists/1/items', {}, status: 201)
          ..on('POST /api/playlists', {'id': 5}, status: 201)
          ..on('POST /api/playlists/5/items', {}, status: 201);
      }, overrides: [authControllerProvider.overrideWith(_SignedInViewer.new)]);
      await tester.tap(find.byTooltip('更多操作'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('加入播放列表'));
      await tester.pumpAndSettle();
      // 别人共享的列表不能往里加
      expect(find.text('别人的片单'), findsNothing);
      await tester.tap(find.text('周末'));
      await tester.pumpAndSettle();
      expect(api.bodies['POST /api/playlists/1/items'], {'item_id': 10});
      expect(find.text('已加入播放列表'), findsOneWidget);

      await tester.tap(find.byTooltip('更多操作'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('加入播放列表'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('新建播放列表'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField), ' 科幻 ');
      await tester.tap(find.text('创建'));
      await tester.pumpAndSettle();
      expect(api.bodies['POST /api/playlists'], {
        'title': '科幻',
        'shared': false,
      });
      expect(api.bodies['POST /api/playlists/5/items'], {'item_id': 10});
    });

    testWidgets('an episode page adds only that episode', (tester) async {
      final (api, _) = await pumpBrowse(
        tester,
        '/item/20/episode/301?season=21',
        (api) {
          api
            ..on(
              'GET /api/items/20',
              detail(threeBody, seasons: [season(21, 1, 1)]),
            )
            ..on(
              'GET /api/seasons/21',
              seasonDetail(21, 1, [episode(301, 1, '科学边界')]),
            )
            ..on('GET /api/playlists', [playlist(1, '周末', 2)])
            ..on('POST /api/playlists/1/items', {}, status: 201);
        },
        overrides: [authControllerProvider.overrideWith(_SignedInViewer.new)],
      );
      await tester.tap(find.byTooltip('加入播放列表'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('周末'));
      await tester.pumpAndSettle();
      expect(api.bodies['POST /api/playlists/1/items'], {
        'item_id': 20,
        'episode_id': 301,
      });
    });

    testWidgets('a playlist plays on in the built-in player and edits its '
        'own items', (tester) async {
      final player = FakePlayer(platform: PlayPlatform.androidApp);
      final (api, router) = await pumpBrowse(
        tester,
        '/playlists/1',
        (api) {
          api
            ..on('GET /api/playlists/1', {
              'playlist': playlist(1, '周末', 2),
              'items': [
                playlistEntry(11, wanderingEarth),
                playlistEntry(
                  12,
                  threeBody,
                  seasonNumber: 1,
                  episodeId: 301,
                  episodeNumber: 1,
                  episodeName: '科学边界',
                ),
              ],
            })
            ..on('PUT /api/playlists/1/items', null, status: 204)
            ..on('POST /api/play', ticket(external: false));
        },
        player: player,
        overrides: [authControllerProvider.overrideWith(_SignedInViewer.new)],
      );
      expect(find.text('周末'), findsOneWidget);
      expect(find.text('第 1 季 第 1 集 · 科学边界'), findsOneWidget);
      expect(find.text('2 项 · 拖动可调整顺序'), findsOneWidget);

      await tester.tap(find.byTooltip('管理 流浪地球'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('移出列表'));
      await tester.pumpAndSettle();
      // 移出要先确认，确认前不发请求
      expect(find.text('移出「流浪地球」？'), findsOneWidget);
      expect(api.requests, isNot(contains('PUT /api/playlists/1/items')));
      await tester.tap(find.widgetWithText(FilledButton, '移出'));
      await tester.pumpAndSettle();
      expect(api.bodies['PUT /api/playlists/1/items'], {
        'item_ids': [12],
      });

      // 连播只有内置播放器能做，不再让用户选播放器
      await tester.tap(find.text('三体'));
      await tester.pumpAndSettle();
      expect(find.text('内置播放器'), findsNothing);
      expect(api.bodies['POST /api/play'], {
        'episode_id': 301,
        'client': 'android',
        'player': 'builtin',
      });
      expect(location(router), '/player');
      expect(find.text('playlist 1'), findsOneWidget);
    });

    testWidgets("someone else's shared playlist is read only", (tester) async {
      await pumpBrowse(tester, '/playlists/2', (api) {
        api.on('GET /api/playlists/2', {
          'playlist': playlist(2, '别人的片单', 3),
          'items': [playlistEntry(11, wanderingEarth)],
        });
      }, overrides: [authControllerProvider.overrideWith(_SignedInViewer.new)]);
      expect(find.text('播放全部'), findsOneWidget);
      expect(find.text('1 项'), findsOneWidget);
      expect(find.byTooltip('管理 流浪地球'), findsNothing);
      expect(find.byTooltip('拖动排序'), findsNothing);
      expect(find.byTooltip('重命名'), findsNothing);
    });

    testWidgets('deleting a playlist asks first', (tester) async {
      final (api, _) = await pumpBrowse(tester, '/playlists', (api) {
        api
          ..on('GET /api/playlists', [
            playlist(1, '周末', 2),
            playlist(2, '别人的片单', 3),
          ])
          ..on('DELETE /api/playlists/1', null, status: 204);
      }, overrides: [authControllerProvider.overrideWith(_SignedInViewer.new)]);
      // 只有自己的列表能管理
      expect(find.byTooltip('管理列表'), findsOneWidget);
      await tester.tap(find.byTooltip('管理列表'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('删除'));
      await tester.pumpAndSettle();
      expect(find.text('删除「周末」？'), findsOneWidget);
      expect(api.requests, isNot(contains('DELETE /api/playlists/1')));
      await tester.tap(find.widgetWithText(FilledButton, '删除'));
      await tester.pumpAndSettle();
      expect(api.requests, contains('DELETE /api/playlists/1'));
      expect(find.text('已删除播放列表'), findsOneWidget);
    });

    testWidgets('collections list shows covers and member counts', (
      tester,
    ) async {
      final (api, router) = await pumpBrowse(tester, '/library/1/collections', (
        api,
      ) {
        api
          ..on('GET /api/libraries/1/collections', [
            {
              'id': 3,
              'title': '刘慈欣宇宙',
              'overview': null,
              'item_count': 2,
              'cover': wanderingEarth['poster'],
            },
            {'id': 4, 'title': '空合集', 'item_count': 0, 'cover': []},
          ])
          ..on('GET /api/collections/3', {
            'collection': {
              'id': 3,
              'library_id': 1,
              'title': '刘慈欣宇宙',
              'cover': <Object>[],
              'backdrop': <Object>[],
            },
            'items': [wanderingEarth],
          });
      });
      expect(find.text('刘慈欣宇宙'), findsOneWidget);
      expect(find.text('2 部'), findsOneWidget);
      expect(find.text('空合集'), findsOneWidget);
      expect(find.text('暂无影片'), findsOneWidget);
      expect(api.requests, contains('GET /api/libraries/1/collections'));

      await tester.tap(find.text('刘慈欣宇宙'));
      await tester.pumpAndSettle();
      expect(location(router), '/collections/3');
    });

    testWidgets('admins pick a member as the collection cover', (tester) async {
      final (api, _) = await pumpBrowse(tester, '/collections/3', (api) {
        api
          ..on('GET /api/collections/3', {
            'collection': {
              'id': 3,
              'library_id': 1,
              'title': '刘慈欣宇宙',
              'cover_item_id': 12,
              'cover': <Object>[],
              'backdrop': <Object>[],
            },
            'items': [wanderingEarth, crazyAlien],
          })
          ..on('PUT /api/admin/collections/3/cover', <String, Object?>{});
      }, overrides: [authControllerProvider.overrideWith(_SignedInAdmin.new)]);
      // 已指定过封面时顶栏有「恢复默认封面」，点了发空的 item_id
      expect(find.text('2 部影片'), findsOneWidget);
      await tester.tap(find.byTooltip('恢复默认封面'));
      await tester.pumpAndSettle();
      const cover = 'PUT /api/admin/collections/3/cover';
      expect(api.bodies[cover], {'item_id': null});
      expect(find.text('已恢复默认封面'), findsOneWidget);

      await tester.tap(find.byTooltip('选择条目'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('流浪地球'));
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('设为封面'));
      await tester.pumpAndSettle();
      expect(api.bodies[cover], {'item_id': 10});
      expect(find.text('已将「流浪地球」设为封面'), findsOneWidget);
    });

    testWidgets('a collection shows posters, admins remove members', (
      tester,
    ) async {
      final (api, router) = await pumpBrowse(tester, '/collections/3', (api) {
        api
          ..on('GET /api/collections/3', {
            'collection': {
              'id': 3,
              'library_id': 1,
              'title': '刘慈欣宇宙',
              'overview': '改编自刘慈欣的作品',
              'cover': <Object>[],
              'backdrop': <Object>[],
            },
            'items': [wanderingEarth, crazyAlien],
          })
          ..on('DELETE /api/admin/collections/3/items/12', null, status: 204)
          ..on('GET /api/items/10', detail(wanderingEarth));
      }, overrides: [authControllerProvider.overrideWith(_SignedInAdmin.new)]);
      expect(find.text('刘慈欣宇宙'), findsOneWidget);
      expect(find.text('改编自刘慈欣的作品'), findsOneWidget);

      await tester.tap(find.byTooltip('选择条目'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('疯狂的外星人'));
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('移出合集'));
      await tester.pumpAndSettle();
      // 移出前先确认，确认前不发请求
      expect(find.text('把 1 项移出合集？'), findsOneWidget);
      expect(
        api.requests,
        isNot(contains('DELETE /api/admin/collections/3/items/12')),
      );
      await tester.tap(find.widgetWithText(FilledButton, '移出'));
      await tester.pumpAndSettle();
      expect(
        api.requests,
        contains('DELETE /api/admin/collections/3/items/12'),
      );
      expect(find.text('已移出 1 项'), findsOneWidget);

      await tester.tap(find.text('流浪地球'));
      await tester.pumpAndSettle();
      expect(location(router), '/item/10');
    });
  });
}
