import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

class TokenPair {
  const TokenPair({required this.accessToken, required this.refreshToken});

  final String accessToken;
  final String refreshToken;
}

abstract interface class TokenStore {
  Future<TokenPair?> read();
  Future<void> write(TokenPair tokens);
  Future<void> clear();
}

/// 删除某台服务器时清掉本机为它保存的令牌（不需要先连上它）。
Future<void> forgetServerTokens(String serverId) async {
  final prefs = SharedPreferencesAsync();
  await prefs.remove('auth.$serverId.access_token');
  await prefs.remove('auth.$serverId.refresh_token');
  await prefs.remove('auth.$serverId.accounts');
}

/// Web 上落在 localStorage。第一期只服务自用和家人朋友，接受这一取舍；App 端再换成系统安全存储。
/// 令牌按服务器 ID 分开保存，切换服务器时互不影响（ADR 0017）。
class SharedPreferencesTokenStore implements TokenStore {
  SharedPreferencesTokenStore({required this.serverId});

  /// 当前服务器的 ID，第一次读写令牌时才去获取
  final Future<String> Function() serverId;
  final SharedPreferencesAsync _prefs = SharedPreferencesAsync();

  Future<(String, String)> _keys() async {
    final id = await serverId();
    return ('auth.$id.access_token', 'auth.$id.refresh_token');
  }

  @override
  Future<TokenPair?> read() async {
    final (accessKey, refreshKey) = await _keys();
    final access = await _prefs.getString(accessKey);
    final refresh = await _prefs.getString(refreshKey);
    if (access == null || refresh == null) return null;
    return TokenPair(accessToken: access, refreshToken: refresh);
  }

  @override
  Future<void> write(TokenPair tokens) async {
    final (accessKey, refreshKey) = await _keys();
    await _prefs.setString(accessKey, tokens.accessToken);
    await _prefs.setString(refreshKey, tokens.refreshToken);
  }

  @override
  Future<void> clear() async {
    final (accessKey, refreshKey) = await _keys();
    await _prefs.remove(accessKey);
    await _prefs.remove(refreshKey);
  }
}

class MemoryTokenStore implements TokenStore {
  MemoryTokenStore([this._tokens]);

  TokenPair? _tokens;

  @override
  Future<TokenPair?> read() async => _tokens;

  @override
  Future<void> write(TokenPair tokens) async => _tokens = tokens;

  @override
  Future<void> clear() async => _tokens = null;
}

/// 本机记住的其他账号。当前账号的令牌仍在 [TokenStore] 里，这里只放切换走的账号，
/// 切回来时不用再输密码。
class SavedAccount {
  const SavedAccount({
    required this.id,
    required this.username,
    required this.role,
    required this.tokens,
  });

  factory SavedAccount.fromJson(Map<String, dynamic> json) => SavedAccount(
    id: json['id'] as int,
    username: json['username'] as String,
    role: json['role'] as String,
    tokens: TokenPair(
      accessToken: json['access_token'] as String,
      refreshToken: json['refresh_token'] as String,
    ),
  );

  final int id;
  final String username;
  final String role;
  final TokenPair tokens;

  bool get isAdmin => role == 'admin';

  Map<String, dynamic> toJson() => {
    'id': id,
    'username': username,
    'role': role,
    'access_token': tokens.accessToken,
    'refresh_token': tokens.refreshToken,
  };
}

abstract interface class AccountStore {
  Future<List<SavedAccount>> read();
  Future<void> write(List<SavedAccount> accounts);
}

/// 和令牌一样按服务器 ID 分开保存，存储位置的取舍也相同。
class SharedPreferencesAccountStore implements AccountStore {
  SharedPreferencesAccountStore({required this.serverId});

  final Future<String> Function() serverId;
  final SharedPreferencesAsync _prefs = SharedPreferencesAsync();

  Future<String> _key() async => 'auth.${await serverId()}.accounts';

  @override
  Future<List<SavedAccount>> read() async {
    final raw = await _prefs.getString(await _key());
    if (raw == null) return const [];
    try {
      return [
        for (final item in jsonDecode(raw) as List<dynamic>)
          SavedAccount.fromJson(item as Map<String, dynamic>),
      ];
    } on Object {
      // 存的内容坏了就当没有记住的账号，重新登录即可
      return const [];
    }
  }

  @override
  Future<void> write(List<SavedAccount> accounts) async {
    final key = await _key();
    if (accounts.isEmpty) {
      await _prefs.remove(key);
    } else {
      await _prefs.setString(
        key,
        jsonEncode([for (final a in accounts) a.toJson()]),
      );
    }
  }
}

class MemoryAccountStore implements AccountStore {
  MemoryAccountStore([List<SavedAccount> accounts = const []])
    : _accounts = accounts;

  List<SavedAccount> _accounts;

  @override
  Future<List<SavedAccount>> read() async => _accounts;

  @override
  Future<void> write(List<SavedAccount> accounts) async =>
      _accounts = List.unmodifiable(accounts);
}
