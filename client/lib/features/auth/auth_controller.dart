import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/api/api_client.dart';
import '../../core/api/api_error.dart';
import '../../core/api/token_store.dart';
import '../../core/providers.dart';

class AppUser {
  const AppUser({
    required this.id,
    required this.username,
    required this.role,
    required this.disabled,
    required this.allowSubtitleDownload,
  });

  factory AppUser.fromJson(Map<String, dynamic> json) => AppUser(
    id: json['id'] as int,
    username: json['username'] as String,
    role: json['role'] as String,
    disabled: json['disabled'] as bool,
    allowSubtitleDownload: json['allow_subtitle_download'] as bool,
  );

  final int id;
  final String username;
  final String role;
  final bool disabled;

  /// 普通用户可以搜索和下载在线字幕；管理员始终允许
  final bool allowSubtitleDownload;

  bool get isAdmin => role == 'admin';

  bool get canDownloadSubtitles => isAdmin || allowSubtitleDownload;
}

TokenPair tokensFromJson(Map<String, dynamic> json) => TokenPair(
  accessToken: json['access_token'] as String,
  refreshToken: json['refresh_token'] as String,
);

final authControllerProvider = AsyncNotifierProvider<AuthController, AppUser?>(
  AuthController.new,
);

/// 本机记住的其他账号，「切换用户」里列出来。
final savedAccountsProvider = FutureProvider.autoDispose<List<SavedAccount>>((
  ref,
) {
  // 换了服务器就换一份
  ref.watch(apiBaseUrlProvider);
  return ref.watch(accountStoreProvider).read();
});

/// 当前登录用户。null 表示未登录。
class AuthController extends AsyncNotifier<AppUser?> {
  Dio get _dio => ref.read(apiClientProvider);
  TokenStore get _tokens => ref.read(tokenStoreProvider);
  AccountStore get _accounts => ref.read(accountStoreProvider);

  @override
  Future<AppUser?> build() async {
    // 刷新令牌被拒绝时，API 客户端会发出登录失效信号
    ref.listen(sessionExpiredProvider, (_, _) => state = const AsyncData(null));
    // 切换服务器后按新服务器的令牌重新判断登录状态
    if (ref.watch(apiBaseUrlProvider).isEmpty) return null;
    if (await _tokens.read() == null) return null;
    try {
      final response = await _dio.get<Map<String, dynamic>>('/api/auth/me');
      return AppUser.fromJson(response.data!);
    } on DioException catch (e) {
      if (ApiError.from(e).isUnauthorized) return null;
      rethrow;
    }
  }

  Future<void> login(String username, String password) async {
    final device = await ref.read(clientDeviceProvider.future);
    final response = await _dio.post<Map<String, dynamic>>(
      '/api/auth/login',
      data: {'username': username, 'password': password, ...device.toJson()},
      options: Options(extra: {skipAuthExtra: true}),
    );
    final data = response.data!;
    final user = AppUser.fromJson(data['user'] as Map<String, dynamic>);
    await _tokens.write(tokensFromJson(data));
    // 重新登录了一个记住的账号，以新令牌为准；旧会话在服务端也注销，不留在设备列表里
    final stale = (await _accounts.read()).where((a) => a.id == user.id);
    await _forgetSaved(user.id);
    for (final account in stale) {
      await _revoke(account.tokens);
    }
    _changed(user);
  }

  Future<void> changePassword(String current, String next) async {
    final response = await _dio.post<Map<String, dynamic>>(
      '/api/auth/password',
      data: {'current_password': current, 'new_password': next},
    );
    // 改密码后旧会话全部失效，换上服务端签发的新令牌
    await _tokens.write(tokensFromJson(response.data!));
  }

  Future<void> logout() async {
    final tokens = await _tokens.read();
    if (tokens != null) await _revoke(tokens);
    await _tokens.clear();
    _changed(null);
  }

  /// 换到记住的另一个账号。先单独验证它的令牌（必要时刷新），
  /// 失效了就忘掉它并抛出错误，当前账号保持登录不受影响。
  Future<void> switchTo(SavedAccount account) async {
    final verified = await _verify(account);
    if (verified == null) {
      await _forgetSaved(account.id);
      throw ApiError(
        code: 'account_expired',
        message: '${account.username} 的登录已失效，请添加账号重新登录',
      );
    }
    final (user, tokens) = verified;
    await _stashCurrent();
    await _tokens.write(tokens);
    await _forgetSaved(user.id);
    _changed(user);
  }

  /// 记住当前账号后回到登录页，登录另一个账号。
  Future<void> addAccount() async {
    await _stashCurrent();
    await _tokens.clear();
    _changed(null);
  }

  /// 不再记住某个账号，顺带让服务端注销它的会话。
  Future<void> forget(SavedAccount account) async {
    await _forgetSaved(account.id);
    await _revoke(account.tokens);
  }

  void _changed(AppUser? user) {
    ref.read(sessionEpochProvider.notifier).bump();
    ref.invalidate(savedAccountsProvider);
    state = AsyncData(user);
  }

  Future<void> _stashCurrent() async {
    final user = state.value;
    final tokens = await _tokens.read();
    if (user == null || tokens == null) return;
    final saved = await _accounts.read();
    await _accounts.write([
      SavedAccount(
        id: user.id,
        username: user.username,
        role: user.role,
        tokens: tokens,
      ),
      ...saved.where((a) => a.id != user.id),
    ]);
  }

  Future<void> _forgetSaved(int id) async {
    final saved = await _accounts.read();
    if (saved.any((a) => a.id == id)) {
      await _accounts.write(saved.where((a) => a.id != id).toList());
    }
    ref.invalidate(savedAccountsProvider);
  }

  /// 不经过 API 客户端的自动刷新：那条路失败会把当前账号也登出。
  /// 返回 null 表示服务端拒绝了这组令牌（过期、被注销或账号被停用）；网络错误照常抛出。
  Future<(AppUser, TokenPair)?> _verify(SavedAccount account) async {
    try {
      return (await _me(account.tokens), account.tokens);
    } on DioException catch (e) {
      if (!ApiError.from(e).isUnauthorized) {
        if (_rejected(e)) return null;
        rethrow;
      }
    }
    try {
      final response = await _dio.post<Map<String, dynamic>>(
        refreshPath,
        data: {'refresh_token': account.tokens.refreshToken},
        options: Options(extra: {skipAuthExtra: true}),
      );
      final refreshed = tokensFromJson(response.data!);
      // 旧的刷新令牌已经作废，先存下新的，后面断网也不会丢掉这个账号
      final saved = await _accounts.read();
      await _accounts.write([
        for (final a in saved)
          a.id == account.id
              ? SavedAccount(
                  id: a.id,
                  username: a.username,
                  role: a.role,
                  tokens: refreshed,
                )
              : a,
      ]);
      return (await _me(refreshed), refreshed);
    } on DioException catch (e) {
      if (_rejected(e)) return null;
      rethrow;
    }
  }

  Future<AppUser> _me(TokenPair tokens) async {
    final response = await _dio.get<Map<String, dynamic>>(
      '/api/auth/me',
      options: Options(
        extra: {skipAuthExtra: true},
        headers: {'Authorization': 'Bearer ${tokens.accessToken}'},
      ),
    );
    return AppUser.fromJson(response.data!);
  }

  static bool _rejected(DioException e) {
    final status = e.response?.statusCode;
    return status != null && status >= 400 && status < 500;
  }

  Future<void> _revoke(TokenPair tokens) async {
    try {
      await _dio.post<void>(
        '/api/auth/logout',
        data: {'refresh_token': tokens.refreshToken},
        options: Options(extra: {skipAuthExtra: true}),
      );
    } on DioException {
      // 服务端注销失败不影响本地忘掉
    }
  }
}
