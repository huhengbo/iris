import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/core/providers.dart';
import 'package:iris/features/auth/auth_controller.dart';
import 'package:iris/features/detail/detail_page.dart';
import 'package:iris/features/detail/related_row.dart';

import 'support/fake_api.dart';

// ---- 接口数据 ----

Map<String, Object?> card(
  int id,
  String title, {
  int library = 1,
  String kind = 'movie',
  bool watched = false,
  bool favorite = false,
}) => {
  'id': id,
  'library_id': library,
  'kind': kind,
  'title': title,
  'original_title': null,
  'year': 2019,
  'rating': 7.9,
  'genres': <String>['科幻'],
  'runtime': kind == 'movie' ? 125 : null,
  'poster': <Object>[],
  'backdrop': <Object>[],
  'watched': watched,
  'favorite': favorite,
};

/// 内置播放器上报过的流信息：版本下拉的说明和「获取信息」都从这里来。
Map<String, Object?> mediaInfo() => {
  'duration': 7200,
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
    {
      'language': 'eng',
      'title': null,
      'codec': 'eac3',
      'channels': 6,
      'default': false,
    },
  ],
  'subtitles': [
    {
      'language': 'eng',
      'title': 'English',
      'codec': 'subrip',
      'forced': false,
      'default': false,
    },
  ],
};

Map<String, Object?> movieDetail({
  bool watched = false,
  bool favorite = false,
  bool watchlisted = false,
  bool withMedia = true,
}) => {
  ...card(10, '流浪地球', watched: watched),
  'favorite': favorite,
  'watchlisted': watchlisted,
  'en_title': null,
  'release_date': null,
  'overview': '流浪地球的简介',
  'content_rating': null,
  'thumb': <Object>[],
  'logo': <Object>[],
  'directors': <Object>[],
  'writers': <Object>[],
  'cast': <Object>[],
  'studios': <Object>[],
  'countries': <Object>[],
  'ratings': <Object>[],
  'versions': [
    {
      'id': 1,
      'label': '4K · REMUX · 58 GB',
      'size': 58 * 1024 * 1024 * 1024,
      'subtitles': <Object>[],
      'media': withMedia ? mediaInfo() : null,
    },
    if (withMedia)
      {
        'id': 2,
        'label': '1080p · WEB-DL · 8 GB',
        'size': 8 * 1024 * 1024 * 1024,
        'subtitles': <Object>[],
        'media': null,
      },
  ],
  'seasons': <Object>[],
  'next_episode': null,
  'progress': null,
};

Map<String, Object?> seriesDetail() => {
  ...card(20, '三体', library: 2, kind: 'series'),
  'favorite': false,
  'watchlisted': false,
  'en_title': null,
  'release_date': null,
  'overview': '三体的简介',
  'content_rating': null,
  'thumb': <Object>[],
  'logo': <Object>[],
  'directors': <Object>[],
  'writers': <Object>[],
  'cast': <Object>[],
  'studios': <Object>[],
  'countries': <Object>[],
  'ratings': <Object>[],
  'versions': <Object>[],
  'seasons': [
    {
      'id': 21,
      'number': 1,
      'name': '第 1 季',
      'air_date': null,
      'poster': <Object>[],
      'episode_count': 1,
    },
  ],
  'next_episode': {
    'id': 211,
    'season_id': 21,
    'season_number': 1,
    'number': 1,
    'name': '第 1 集',
    'progress': null,
  },
  'progress': null,
};

// ---- 测试环境 ----

class _User extends AuthController {
  _User(this.role);

  final String role;

  @override
  Future<AppUser?> build() async => AppUser(
    id: 1,
    username: 'u',
    role: role,
    disabled: false,
    allowSubtitleDownload: true,
  );
}

GoRouter _router(String location) => GoRouter(
  initialLocation: location,
  routes: [
    GoRoute(
      path: '/',
      builder: (_, _) => const Scaffold(body: Text('首页')),
    ),
    GoRoute(
      path: '/item/:id',
      builder: (_, state) =>
          DetailPage(id: int.parse(state.pathParameters['id']!)),
    ),
    GoRoute(
      path: '/settings/manage/items/:id',
      builder: (_, _) => const Scaffold(body: Text('条目编辑页')),
    ),
  ],
);

/// 详情页和它依赖的接口：偏好、字幕下载器、更多类似。
Future<(FakeAdminApi, GoRouter)> _pump(
  WidgetTester tester, {
  required String role,
  String location = '/item/10',
  Map<String, Object?>? detail,
  List<Map<String, Object?>> related = const [],
  int relatedStatus = 200,
  void Function(FakeAdminApi api)? setup,
  double width = 390,
}) async {
  final api = FakeAdminApi()
    ..on('GET /api/items/10', detail ?? movieDetail())
    ..on(
      'GET /api/items/10/related?limit=$relatedLimit',
      related,
      status: relatedStatus,
    )
    ..on('GET /api/me/preferences', {'subtitle_language': 'zh-CN'})
    ..on('GET /api/subtitles/providers?version_id=1', {
      'allowed': true,
      'providers': <Object>[],
    });
  setup?.call(api);
  final dio = Dio(BaseOptions(baseUrl: 'http://test'))..httpClientAdapter = api;
  final router = _router(location);
  addTearDown(router.dispose);
  tester.view.physicalSize = Size(width * 3, 2400 * 3);
  tester.view.devicePixelRatio = 3;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        apiClientProvider.overrideWithValue(dio),
        apiBaseUrlProvider.overrideWithValue('http://test'),
        authControllerProvider.overrideWith(() => _User(role)),
      ],
      child: MaterialApp.router(theme: buildTheme(), routerConfig: router),
    ),
  );
  await tester.pumpAndSettle();
  return (api, router);
}

/// 详情页操作行的按钮：播放、已看、稍后观看、编辑、更多
Finder get _play => find.byType(FilledButton);
Finder _button(String tooltip) => find.byTooltip(tooltip);

double _dx(WidgetTester tester, String tooltip) =>
    tester.getCenter(_button(tooltip)).dx;

Future<void> _openMenu(WidgetTester tester) async {
  await tester.tap(_button('更多操作'));
  await tester.pumpAndSettle();
}

void main() {
  group('detail action row', () {
    testWidgets(
      'admin: play, watched, watchlist, edit and more in Plex order',
      (tester) async {
        await _pump(tester, role: 'admin');

        expect(find.text('播放'), findsOneWidget);
        expect(_button('标记已看'), findsOneWidget);
        expect(_button('稍后观看'), findsOneWidget);
        expect(_button('编辑'), findsOneWidget);
        expect(_button('更多操作'), findsOneWidget);
        // 收藏和加入播放列表移进「⋯」，操作行上不再有自己的按钮
        expect(find.byIcon(Icons.favorite_border), findsNothing);
        expect(find.byIcon(Icons.playlist_add), findsNothing);

        final play = tester.getCenter(_play).dx;
        final watched = _dx(tester, '标记已看');
        final watchlist = _dx(tester, '稍后观看');
        final edit = _dx(tester, '编辑');
        final more = _dx(tester, '更多操作');
        expect(play, lessThan(watched));
        expect(watched, lessThan(watchlist));
        expect(watchlist, lessThan(edit));
        expect(edit, lessThan(more));
      },
    );

    testWidgets('regular users have no edit button but still have ⋯', (
      tester,
    ) async {
      await _pump(tester, role: 'user');

      expect(_play, findsOneWidget);
      expect(_button('标记已看'), findsOneWidget);
      expect(_button('稍后观看'), findsOneWidget);
      expect(_button('编辑'), findsNothing);
      expect(_button('更多操作'), findsOneWidget);
    });

    testWidgets('the edit pencil opens the existing item editor', (
      tester,
    ) async {
      final (_, router) = await _pump(tester, role: 'admin');

      await tester.tap(_button('编辑'));
      await tester.pumpAndSettle();
      expect(find.text('条目编辑页'), findsOneWidget);
      expect(router.state.uri.toString(), '/settings/manage/items/10');
    });
  });

  group('⋯ menu', () {
    testWidgets('admin sees the admin actions in order, grouped by dividers', (
      tester,
    ) async {
      await _pump(tester, role: 'admin');
      await _openMenu(tester);

      const labels = [
        '收藏',
        '加入播放列表',
        '获取信息',
        '修正匹配',
        '取消匹配',
        '刷新元数据',
        '重新读取文件',
        '编辑字幕',
        '同步此目录',
        '加入合集',
        '删除',
      ];
      for (final label in labels) {
        expect(find.text(label), findsOneWidget, reason: label);
      }
      final tops = [
        for (final label in labels) tester.getTopLeft(find.text(label)).dy,
      ];
      expect(tops, orderedEquals([...tops]..sort()));
      // 旧名字不再出现
      expect(find.text('重新匹配'), findsNothing);
      expect(find.text('删除并清理来源上的文件'), findsNothing);
      // 收藏和加入播放列表不再占操作行的位置
      expect(find.text('收藏'), findsOneWidget);
      // 两组分隔线：普通操作和管理操作、删除
      expect(find.byType(Divider), findsNWidgets(2));
    });

    testWidgets('regular users get favorite, playlist and info only', (
      tester,
    ) async {
      await _pump(tester, role: 'user');
      await _openMenu(tester);

      expect(find.text('收藏'), findsOneWidget);
      expect(find.text('加入播放列表'), findsOneWidget);
      expect(find.text('获取信息'), findsOneWidget);
      for (final label in ['修正匹配', '取消匹配', '刷新元数据', '删除']) {
        expect(find.text(label), findsNothing, reason: label);
      }
      expect(find.byType(Divider), findsNothing);
    });

    testWidgets(
      'a favorited and watchlisted item offers the opposite actions',
      (tester) async {
        // 详情页的 ⋯ 没有稍后观看入口（那是操作行上的按钮）
        await _pump(
          tester,
          role: 'user',
          detail: movieDetail(favorite: true, watchlisted: true),
        );
        await _openMenu(tester);

        expect(find.text('取消收藏'), findsOneWidget);
        expect(find.text('收藏'), findsNothing);
        expect(find.text('移出稍后观看'), findsNothing);
      },
    );

    testWidgets('deleting asks first and says source files are removed too', (
      tester,
    ) async {
      await _pump(tester, role: 'admin');
      await _openMenu(tester);

      await tester.tap(find.text('删除'));
      await tester.pumpAndSettle();
      expect(find.text('删除「流浪地球」？'), findsOneWidget);
      expect(find.textContaining('来源上的文件夹'), findsOneWidget);
      expect(find.text('取消'), findsOneWidget);
      // 确认按钮写「删除」，不写「确定」
      expect(
        find.descendant(
          of: find.byType(FilledButton),
          matching: find.text('删除'),
        ),
        findsOneWidget,
      );
    });

    testWidgets('regular users can read media info', (tester) async {
      await _pump(tester, role: 'user');
      await _openMenu(tester);

      await tester.tap(find.text('获取信息'));
      await tester.pumpAndSettle();
      expect(find.text('「流浪地球」信息'), findsOneWidget);
      expect(find.text('版本 2 个'), findsOneWidget);
      expect(find.text('画面：4K · HEVC · HDR10'), findsOneWidget);
      expect(find.textContaining('音频：中文 TrueHD 7.1'), findsOneWidget);
      expect(find.text('关闭'), findsOneWidget);
    });
  });

  group('stream dropdowns', () {
    testWidgets('version, audio and subtitle are drop-downs, no video row', (
      tester,
    ) async {
      await _pump(tester, role: 'user');

      expect(find.text('版本'), findsOneWidget);
      expect(find.text('音频'), findsOneWidget);
      expect(find.text('字幕'), findsOneWidget);
      // 画面信息并进版本下拉，不再单独占一行
      expect(find.text('画面'), findsNothing);
    });

    testWidgets('version items carry the picture description', (tester) async {
      await _pump(tester, role: 'user');

      await tester.tap(find.text('版本'));
      await tester.pumpAndSettle();
      // 当前版本同时出现在下拉入口和选项里
      expect(find.text('4K · REMUX · 58 GB'), findsWidgets);
      expect(find.text('4K · HEVC · HDR10'), findsOneWidget);
      expect(find.text('1080p · WEB-DL · 8 GB'), findsOneWidget);
    });

    testWidgets('wide screens open an anchored drop-down instead of a dialog', (
      tester,
    ) async {
      await _pump(tester, role: 'user', width: 1000);

      await tester.tap(find.text('版本'));
      await tester.pumpAndSettle();
      expect(find.byType(Dialog), findsNothing);
      expect(find.byType(PopupMenuItem<int?>), findsWidgets);
      expect(find.text('4K · HEVC · HDR10'), findsOneWidget);
      expect(find.text('1080p · WEB-DL · 8 GB'), findsOneWidget);
    });

    testWidgets('picking a version updates the entry', (tester) async {
      await _pump(tester, role: 'user');

      await tester.tap(find.text('版本'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('1080p · WEB-DL · 8 GB'));
      await tester.pumpAndSettle();
      // 下拉里显示选中的版本
      expect(find.text('1080p · WEB-DL · 8 GB'), findsOneWidget);
      expect(find.text('自动'), findsNothing);
    });
  });

  group('episodes', () {
    Map<String, Object?> episode(int number) => {
      'id': 300 + number,
      'number': number,
      'name': '分集$number',
      'overview': null,
      'air_date': null,
      'runtime': 45,
      'rating': null,
      'still': <Object>[],
      'versions': [
        {
          'id': 3000 + number,
          'label': '1080p',
          'size': 1,
          'subtitles': <Object>[],
        },
      ],
      'watched': false,
      'progress': null,
      'favorite': false,
    };

    Future<void> pumpSeries(WidgetTester tester, double width) => _pump(
      tester,
      role: 'user',
      location: '/item/10',
      detail: {...seriesDetail(), 'id': 10},
      width: width,
      setup: (api) => api.on('GET /api/seasons/21', {
        'id': 21,
        'item_id': 10,
        'number': 1,
        'name': '第 1 季',
        'overview': null,
        'air_date': null,
        'poster': <Object>[],
        'episodes': [for (var n = 1; n <= 4; n++) episode(n)],
      }),
    );

    testWidgets('a phone lists episodes in one column', (tester) async {
      await pumpSeries(tester, 390);

      final first = tester.getTopLeft(find.text('1. 分集1'));
      final second = tester.getTopLeft(find.text('2. 分集2'));
      expect(second.dx, first.dx);
      expect(second.dy, greaterThan(first.dy));
    });

    testWidgets('a wide screen lists episodes as a horizontal card rail', (
      tester,
    ) async {
      await pumpSeries(tester, 1600);

      final first = tester.getTopLeft(find.text('1. 分集1'));
      final second = tester.getTopLeft(find.text('2. 分集2'));
      expect(second.dy, first.dy);
      expect(second.dx, greaterThan(first.dx + 300));
      // 铺满宽度：第 5 集以后在屏幕外，片架可以横滑
      expect(find.text('4. 分集4').hitTestable(), findsOneWidget);
    });

    testWidgets('the rail keeps the watched toggle on every card', (
      tester,
    ) async {
      await pumpSeries(tester, 1600);

      // 4 张分集卡各一个，加上页头里整部剧的一个
      expect(find.byTooltip('标记已看'), findsNWidgets(5));
    });
  });

  group('facts aside', () {
    Map<String, Object?> detailWithFacts() => {
      ...seriesDetail(),
      'id': 10,
      'studios': ['中国电影集团', 'B 公司'],
      'countries': ['中国'],
      'release_date': '2023-01-22',
    };

    testWidgets('a wide screen shows studios, countries and release date', (
      tester,
    ) async {
      await _pump(tester, role: 'user', detail: detailWithFacts(), width: 1600);

      expect(find.text('制作公司'), findsOneWidget);
      expect(find.text('中国电影集团、B 公司'), findsOneWidget);
      expect(find.text('国家 / 地区'), findsOneWidget);
      expect(find.text('2023-01-22'), findsOneWidget);
    });

    testWidgets('a narrower screen leaves the aside out', (tester) async {
      await _pump(tester, role: 'user', detail: detailWithFacts(), width: 1000);

      expect(find.text('制作公司'), findsNothing);
    });
  });

  group('related row', () {
    testWidgets('lists related posters after the episodes', (tester) async {
      await _pump(
        tester,
        role: 'user',
        related: [card(11, '星际穿越'), card(12, '沙丘')],
      );

      expect(find.text('更多类似'), findsOneWidget);
      expect(find.text('星际穿越'), findsOneWidget);
      expect(find.text('沙丘'), findsOneWidget);
      // 海报片架在季集列表（这里没有季集）之后、页面底部之前
      expect(
        tester.getTopLeft(find.text('更多类似')).dy,
        greaterThan(tester.getTopLeft(find.text('流浪地球').first).dy),
      );
    });

    testWidgets('an empty list hides the row completely', (tester) async {
      await _pump(tester, role: 'user');

      expect(find.text('更多类似'), findsNothing);
      expect(find.text('流浪地球'), findsWidgets);
    });

    testWidgets('a failure only affects the row, not the detail page', (
      tester,
    ) async {
      await _pump(tester, role: 'user', relatedStatus: 500);

      expect(find.text('更多类似没有加载出来'), findsOneWidget);
      expect(find.text('重试'), findsOneWidget);
      // 详情页其余部分照常
      expect(find.text('流浪地球'), findsWidgets);
      expect(_play, findsOneWidget);
    });
  });
}
