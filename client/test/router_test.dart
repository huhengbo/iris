import 'dart:async';

import 'package:iris/app/router.dart';
import 'package:iris/core/servers.dart';
import 'package:iris/features/auth/auth_controller.dart';
import 'package:iris/features/me/settings_page.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

const _admin = AppUser(
  id: 1,
  username: 'admin',
  role: 'admin',
  disabled: false,
  allowSubtitleDownload: true,
);
const _user = AppUser(
  id: 2,
  username: 'alice',
  role: 'user',
  disabled: false,
  allowSubtitleDownload: true,
);

String? redirect(AsyncValue<AppUser?> auth, String location) =>
    authRedirect(auth, Uri.parse(location));

void main() {
  test('while auth state is loading, wait on the splash page', () {
    expect(
      redirect(const AsyncLoading(), '/settings/manage/users'),
      '/splash?from=%2Fsettings%2Fmanage%2Fusers',
    );
    expect(redirect(const AsyncLoading(), '/splash'), isNull);
  });

  test('signed-out users go to login and keep the intended page', () {
    expect(redirect(const AsyncData(null), '/'), '/login');
    expect(redirect(const AsyncData(null), '/me'), '/login?from=%2Fme');
    expect(
      redirect(const AsyncData(null), '/splash?from=%2Fme'),
      '/login?from=%2Fme',
    );
    expect(redirect(const AsyncData(null), '/login'), isNull);
  });

  test('after login, return to the intended page', () {
    expect(redirect(const AsyncData(_user), '/login?from=%2Fme'), '/me');
    expect(redirect(const AsyncData(_user), '/login'), '/');
  });

  test('from parameter cannot redirect off-site', () {
    expect(
      redirect(const AsyncData(_user), '/login?from=https://evil.example'),
      '/',
    );
    expect(redirect(const AsyncData(_user), '/login?from=//evil.example'), '/');
  });

  test('every settings entry has its own route', () {
    TestWidgetsFlutterBinding.ensureInitialized();
    final container = ProviderContainer(
      overrides: [
        authControllerProvider.overrideWith(_Admin.new),
        manageServersProvider.overrideWithValue(false),
      ],
    );
    addTearDown(container.dispose);
    final router = container.read(routerProvider);
    for (final entry in allSettingsEntries()) {
      final match = router.configuration.findMatch(Uri.parse(entry.path));
      expect(match.isError, isFalse, reason: entry.path);
      expect(match.matches.last.matchedLocation, entry.path);
    }
    // 设置项里再进一层的整页详情
    for (final path in [
      '/settings/manage/libraries/1',
      '/settings/manage/libraries/1/recommendations',
      '/settings/manage/sources/1',
      '/settings/manage/plugins/source.115',
      '/settings/manage/items/1',
      '/settings/manage/troubleshooting/health',
      '/settings/manage/troubleshooting/logs',
      '/settings/manage/troubleshooting/backups',
    ]) {
      final match = router.configuration.findMatch(Uri.parse(path));
      expect(match.isError, isFalse, reason: path);
      expect(match.matches.last.matchedLocation, path);
    }
  });

  test('favorites and playlists are routes under me', () {
    TestWidgetsFlutterBinding.ensureInitialized();
    final container = ProviderContainer(
      overrides: [
        authControllerProvider.overrideWith(_Admin.new),
        manageServersProvider.overrideWithValue(false),
      ],
    );
    addTearDown(container.dispose);
    final router = container.read(routerProvider);
    for (final path in ['/me/favorites', '/me/playlists']) {
      final match = router.configuration.findMatch(Uri.parse(path));
      expect(match.isError, isFalse, reason: path);
      expect(match.matches.last.matchedLocation, path);
    }
  });

  test('regular users cannot open admin pages', () {
    expect(redirect(const AsyncData(_user), '/settings/manage/users'), '/');
    expect(redirect(const AsyncData(_admin), '/settings/manage/users'), isNull);
    // 新结构里服务器、管理、状态和配置向导仍然只有管理员能进
    for (final path in [
      '/settings/server/general',
      '/settings/manage/users',
      '/settings/status/dashboard',
      '/settings/setup',
    ]) {
      expect(redirect(const AsyncData(_user), path), '/', reason: path);
      expect(redirect(const AsyncData(_admin), path), isNull, reason: path);
    }
    // 账户和本机是所有用户自己的设置
    for (final path in [
      '/settings',
      '/settings/account/profile',
      '/settings/account/devices',
      '/settings/local/general',
      '/settings/local/player',
      '/settings/local/subtitles',
      '/settings/local/about',
    ]) {
      expect(redirect(const AsyncData(_user), path), isNull, reason: path);
    }
  });

  test('the App picks a server before anything else', () {
    String? gated(ServerGate server, String location) => authRedirect(
      const AsyncData(_user),
      Uri.parse(location),
      server: server,
    );
    expect(gated(ServerGate.loading, '/me'), '/splash?from=%2Fme');
    expect(gated(ServerGate.missing, '/'), '/servers');
    expect(gated(ServerGate.missing, '/servers'), isNull);
    // 有服务器后登录前后都能回到服务器列表
    expect(gated(ServerGate.ready, '/servers'), isNull);
    expect(
      authRedirect(
        const AsyncData(null),
        Uri.parse('/servers'),
        server: ServerGate.ready,
      ),
      isNull,
    );
  });

  group('re-checking after a signed-out result', () {
    // 冷启动时服务器列表还没读出来，登录状态先是 null；读出来后重新检查，
    // Riverpod 在检查中和出错时都带着这个 null，不能当成未登录先闪一下登录页
    Future<AsyncValue<AppUser?>> recheck(
      Future<AppUser?> Function() next,
    ) async {
      var calls = 0;
      final provider = FutureProvider<AppUser?>(
        (ref) => ++calls == 1 ? Future.value() : next(),
      );
      final container = ProviderContainer();
      addTearDown(container.dispose);
      container.listen(provider, (_, _) {});
      await container.read(provider.future);
      container.invalidate(provider);
      container.read(provider);
      await Future<void>.delayed(Duration.zero);
      return container.read(provider);
    }

    test('still checking: stay on the splash page', () async {
      final auth = await recheck(() => Completer<AppUser?>().future);
      expect(auth.isLoading, isTrue);
      expect(auth.hasValue, isTrue);
      expect(redirect(auth, '/'), '/splash');
      expect(sessionSettled(auth, ServerGate.ready), isFalse);
    });

    test('check failed: the splash page offers a retry', () async {
      final auth = await recheck(() => Future.error(Exception('offline')));
      expect(auth.hasError, isTrue);
      expect(auth.hasValue, isTrue);
      expect(redirect(auth, '/me'), '/splash?from=%2Fme');
      expect(sessionSettled(auth, ServerGate.ready), isTrue);
    });
  });

  test('the launch animation waits until the destination is known', () {
    expect(sessionSettled(const AsyncData(_user), ServerGate.loading), isFalse);
    expect(sessionSettled(const AsyncLoading(), ServerGate.ready), isFalse);
    expect(sessionSettled(const AsyncLoading(), ServerGate.missing), isTrue);
    expect(sessionSettled(const AsyncData(null), ServerGate.ready), isTrue);
    expect(sessionSettled(const AsyncData(_user), ServerGate.ready), isTrue);
  });
}

class _Admin extends AuthController {
  @override
  Future<AppUser?> build() async => _admin;
}
