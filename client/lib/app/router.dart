import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../core/widgets/async_view.dart';
import '../core/api/api_error.dart';
import '../core/providers.dart';
import '../core/servers.dart';
import '../features/admin/backup_page.dart';
import '../features/admin/health_page.dart';
import '../features/admin/item_admin_page.dart';
import '../features/admin/library_advanced_page.dart';
import '../features/admin/library_recommendations_page.dart';
import '../features/admin/logs_page.dart';
import '../features/admin/plugin_detail_page.dart';
import '../features/admin/plugin_repositories_page.dart';
import '../features/admin/resource_source_detail_page.dart';
import '../features/auth/auth_controller.dart';
import '../features/auth/login_page.dart';
import '../features/detail/detail_page.dart';
import '../features/detail/episode_detail_page.dart';
import '../features/favorites/favorites_page.dart';
import '../features/home/home_page.dart';
import '../features/library/library_page.dart';
import '../features/library/collections_page.dart';
import '../features/library/libraries_page.dart' as library_browse;
import '../features/me/me_page.dart';
import '../features/me/settings_page.dart';
import '../features/person/person_page.dart';
import '../features/play/cast_page.dart';
import '../features/play/player_page.dart';
import '../features/play/playlists_page.dart';
import '../features/search/search_page.dart';
import '../features/servers/servers_page.dart';
import '../features/tv/tv_home_page.dart';
import '../features/tv/tv_shell.dart';
import '../features/watchlist/watchlist_page.dart';
import 'tv.dart' show televisionProvider;
import 'motion.dart';
import 'shell.dart';

final routerProvider = Provider<GoRouter>((ref) {
  // 电视用专门设计的页面，不是手机界面放大
  final tv = ref.read(televisionProvider);
  final authChanged = ValueNotifier<int>(0);
  ref.listen(authControllerProvider, (_, _) => authChanged.value++);
  ref.listen(serverGateProvider, (_, _) => authChanged.value++);

  final router = GoRouter(
    initialLocation: '/',
    refreshListenable: authChanged,
    redirect: (context, state) => authRedirect(
      ref.read(authControllerProvider),
      state.uri,
      server: ref.read(serverGateProvider),
    ),
    routes: [
      GoRoute(path: '/splash', builder: (_, _) => const _SplashPage()),
      GoRoute(path: '/login', builder: (_, _) => const LoginPage()),
      // 只有 App 管理服务器；Web 连的就是页面所在的服务器
      if (ref.read(manageServersProvider))
        GoRoute(path: '/servers', builder: (_, _) => const ServersPage()),
      // 内置播放器全屏，不在底部导航里；播放参数从调用方传入，刷新后回不来，没有参数时回首页
      GoRoute(
        path: '/player',
        redirect: (_, state) => state.extra is PlayerArgs ? null : '/',
        pageBuilder: (_, state) =>
            _fadePage(state, PlayerPage(args: state.extra! as PlayerArgs)),
      ),
      // 投屏控制页，同样不在底部导航里，没有参数时回首页
      GoRoute(
        path: '/cast',
        redirect: (_, state) => state.extra is CastArgs ? null : '/',
        builder: (_, state) => CastPage(args: state.extra! as CastArgs),
      ),
      // 设置与管理后台合一（第 ⑤ 批）。宽屏是左栏分组菜单加右栏内容，手机和 TV
      // 是分组列表逐层进入；每个设置项一个地址。
      GoRoute(
        path: '/settings',
        builder: (_, _) => const _Sides(SettingsPage()),
      ),
      for (final entry in allSettingsEntries())
        GoRoute(
          path: entry.path,
          builder: (_, _) => _Sides(SettingsPage(location: entry.path)),
        ),
      // 设置项里再进一层的页面：资料库统计和推荐、插件、来源、条目、故障排查。
      // 这些是整页打开的详情，不占设置的右栏。
      GoRoute(
        path: '/settings/manage/libraries/:id',
        builder: (_, state) => _Sides(
          LibraryAdvancedPage(
            id: _id(state),
            name: state.uri.queryParameters['name'] ?? '资料库',
          ),
        ),
      ),
      GoRoute(
        path: '/settings/manage/libraries/:id/recommendations',
        builder: (_, state) => _Sides(
          LibraryRecommendationsPage(
            id: _id(state),
            name: state.uri.queryParameters['name'] ?? '资料库',
          ),
        ),
      ),
      GoRoute(
        path: '/settings/manage/sources/:id',
        builder: (_, state) => _Sides(
          ResourceSourceDetailPage(
            sourceId: int.parse(state.pathParameters['id']!),
          ),
        ),
      ),
      GoRoute(
        path: '/settings/manage/plugin-repositories',
        builder: (_, _) => const _Sides(PluginRepositoriesPage()),
      ),
      GoRoute(
        path: '/settings/manage/plugins/:id',
        builder: (_, state) => _Sides(
          PluginDetailPage(
            pluginId: state.pathParameters['id']!,
            pageId: state.uri.queryParameters['page'],
          ),
        ),
      ),
      GoRoute(
        path: '/settings/manage/items/:id',
        builder: (_, state) => _Sides(ItemAdminPage(id: _id(state))),
      ),
      GoRoute(
        path: '/settings/manage/troubleshooting/health',
        builder: (_, _) => const _Sides(HealthPage()),
      ),
      GoRoute(
        path: '/settings/manage/troubleshooting/logs',
        builder: (_, _) => const _Sides(LogsPage()),
      ),
      GoRoute(
        path: '/settings/manage/troubleshooting/backups',
        builder: (_, _) => const _Sides(BackupPage()),
      ),
      GoRoute(
        path: '/item/:itemId/episode/:episodeId',
        builder: (_, state) => EpisodeDetailPage(
          itemId: int.tryParse(state.pathParameters['itemId']!) ?? 0,
          seasonId:
              int.tryParse(state.uri.queryParameters['season'] ?? '') ?? 0,
          episodeId: int.tryParse(state.pathParameters['episodeId']!) ?? 0,
        ),
      ),
      GoRoute(
        path: '/item/:id',
        // 比普通页面切换长：海报要有时间展开成详情页的剧照
        pageBuilder: (_, state) => _fadePage(
          state,
          DetailPage(
            id: _id(state),
            args: state.extra is DetailArgs ? state.extra as DetailArgs : null,
          ),
        ),
      ),
      GoRoute(
        path: '/collections/:id',
        builder: (_, state) =>
            _Sides(CollectionDetailPage(collectionId: _id(state))),
      ),
      GoRoute(
        path: '/person/:id',
        builder: (_, state) => _Sides(PersonPage(id: _id(state))),
      ),
      StatefulShellRoute.indexedStack(
        builder: (context, state, shell) => tv
            ? TvShell(navigationShell: shell)
            : AppShell(navigationShell: shell),
        branches: [
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/',
                builder: (_, _) => tv ? const TvHomePage() : const HomePage(),
                routes: [
                  // 稍后观看挂首页这一栏：手机底栏保持四项，宽屏由侧栏切过来；
                  // 手机首页右上角的书签图标和侧栏都用 /watchlist
                  GoRoute(
                    path: 'watchlist',
                    builder: (_, _) => const _Sides(WatchlistPage()),
                  ),
                  // 在首页这一栏里打开，底部导航保留；排序和筛选在查询参数里
                  GoRoute(
                    path: 'library/:id',
                    builder: (_, state) => _Sides(
                      LibraryPage(
                        id: _id(state),
                        params: state.uri.queryParameters,
                      ),
                    ),
                  ),
                ],
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/libraries',
                builder: (_, _) => const _Sides(library_browse.LibrariesPage()),
                routes: [
                  // 从「资料库」栏打开的海报墙留在这一栏，底部导航不跳回首页
                  GoRoute(
                    path: ':id',
                    builder: (_, state) => _Sides(
                      LibraryPage(
                        id: _id(state),
                        params: state.uri.queryParameters,
                      ),
                    ),
                  ),
                ],
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/search',
                builder: (_, state) => _Sides(
                  SearchPage(query: state.uri.queryParameters['q'] ?? ''),
                ),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(
                path: '/me',
                builder: (_, _) => const _Sides(MePage()),
                routes: [
                  GoRoute(
                    path: 'playlists',
                    builder: (_, _) => const _Sides(PlaylistsPage()),
                    routes: [
                      GoRoute(
                        path: ':id',
                        builder: (_, state) =>
                            _Sides(PlaylistDetailPage(playlistId: _id(state))),
                      ),
                    ],
                  ),
                  GoRoute(
                    path: 'favorites',
                    builder: (_, _) => const _Sides(FavoritesPage()),
                  ),
                ],
              ),
            ],
          ),
        ],
      ),
    ],
  );
  ref.onDispose(() {
    router.dispose();
    authChanged.dispose();
  });
  return router;
});

/// 地址里的 ID 不合法时按不存在的条目处理，页面显示错误。
int _id(GoRouterState state) => int.tryParse(state.pathParameters['id']!) ?? 0;

/// 只给详情页（海报展开）和播放器（全屏切入）用，其他页面走主题里的普通切换。
CustomTransitionPage<void> _fadePage(GoRouterState state, Widget child) =>
    CustomTransitionPage(
      key: state.pageKey,
      transitionDuration: Motion.long,
      reverseTransitionDuration: Motion.medium,
      transitionsBuilder: (_, animation, _, child) => FadeTransition(
        opacity: CurvedAnimation(parent: animation, curve: Motion.emphasized),
        child: child,
      ),
      child: child,
    );

/// 普通页面整体让开左右系统边距（TV 过扫描、横屏刘海），列表不用各自去算。
/// 首页、详情页、播放器的画面要铺满，自己处理；登录和服务器页本来就有 SafeArea。
class _Sides extends StatelessWidget {
  const _Sides(this.child);

  final Widget child;

  @override
  Widget build(BuildContext context) => ColoredBox(
    // 让开的边上也要有底色，否则切换页面时会透出下面那页
    color: Theme.of(context).scaffoldBackgroundColor,
    child: SafeArea(top: false, bottom: false, child: child),
  );
}

/// 路由守卫：App 没选服务器时先去选服务器；未登录去登录页，登录后回到原来想去的页面，
/// 普通用户进不了设置里的服务器、管理、状态三组。
/// 管理接口在服务端同样校验，这里只决定显示哪个页面。
String? authRedirect(
  AsyncValue<AppUser?> auth,
  Uri location, {
  ServerGate server = ServerGate.ready,
}) {
  final path = location.path;
  final from = _safeFrom(location.queryParameters['from']);
  switch (server) {
    case ServerGate.loading:
      return path == '/splash' ? null : _withFrom('/splash', location);
    case ServerGate.missing:
      return path == '/servers' ? null : '/servers';
    case ServerGate.ready:
      // 登录前后都能进服务器列表（登录页的「切换服务器」、我的页的「服务器」）
      if (path == '/servers') return null;
  }
  if (_authPending(auth)) {
    // 检查登录状态中，或检查失败（如网络错误）：停在启动页
    return path == '/splash' ? null : _withFrom('/splash', location);
  }
  final user = auth.value;
  if (user == null) {
    if (path == '/login') return null;
    return _withFrom('/login', path == '/splash' ? Uri.parse(from) : location);
  }
  if (path == '/login' || path == '/splash') return from;
  if (!user.isAdmin && _adminOnly(path)) return '/';
  return null;
}

/// 只有管理员能进的地址：设置里的服务器、管理、状态三组及配置向导。
/// 账户和本机对所有人开放。
bool _adminOnly(String path) => const [
  '/settings/server',
  '/settings/manage',
  '/settings/status',
  '/settings/setup',
].any((prefix) => path == prefix || path.startsWith('$prefix/'));

/// 登录状态还没确定。Riverpod 重新检查和出错时会带着上一次的值，
/// 上一次是 null（比如服务器列表还没读出来）不代表现在未登录，不能先跳登录页。
bool _authPending(AsyncValue<AppUser?> auth) =>
    !auth.hasValue || (auth.value == null && (auth.isLoading || auth.hasError));

/// 启动时能不能放开开机动画：要去哪个页面已经确定（含检查失败要显示重试）。
bool sessionSettled(AsyncValue<AppUser?> auth, ServerGate server) =>
    switch (server) {
      ServerGate.loading => false,
      ServerGate.missing => true,
      ServerGate.ready => auth.hasError || !_authPending(auth),
    };

String _withFrom(String target, Uri location) {
  final from = location.toString();
  if (from == '/' || from.isEmpty) return target;
  return Uri(path: target, queryParameters: {'from': from}).toString();
}

/// 只接受站内路径，防止 from 参数被用来跳转到外部站点。
String _safeFrom(String? from) {
  if (from == null || !from.startsWith('/') || from.startsWith('//')) {
    return '/';
  }
  if (from.startsWith('/login') || from.startsWith('/splash')) return '/';
  return from;
}

class _SplashPage extends ConsumerWidget {
  const _SplashPage();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final auth = ref.watch(authControllerProvider);
    return Scaffold(
      body: auth.hasError
          ? ErrorRetry(
              message: ApiError.from(auth.error!).message,
              onRetry: () {
                // 获取服务器信息失败时也要重试，否则令牌存储一直拿不到服务器 ID
                ref.invalidate(serverInfoProvider);
                ref.invalidate(authControllerProvider);
              },
              secondary: ref.watch(manageServersProvider)
                  ? TextButton(
                      onPressed: () => context.go('/servers'),
                      child: const Text('切换服务器'),
                    )
                  : null,
            )
          : const Center(child: CircularProgressIndicator()),
    );
  }
}
