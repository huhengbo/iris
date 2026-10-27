import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api/api_error.dart';
import 'api/token_store.dart';
import 'server.dart';

/// 本机保存的一台服务器。id 是服务器自己的 server_id，换地址也认得出是同一台（ADR 0017）。
class SavedServer {
  const SavedServer({required this.id, required this.name, required this.url});

  factory SavedServer.fromJson(Map<String, dynamic> json) => SavedServer(
    id: json['id'] as String,
    name: json['name'] as String,
    url: json['url'] as String,
  );

  final String id;
  final String name;

  /// 规范化后的地址，不带末尾斜杠，例如 http://192.168.1.10:8080
  final String url;

  Map<String, Object> toJson() => {'id': id, 'name': name, 'url': url};
}

class ServerList {
  const ServerList({this.servers = const [], this.currentId});

  final List<SavedServer> servers;
  final String? currentId;

  SavedServer? get current {
    for (final server in servers) {
      if (server.id == currentId) return server;
    }
    return null;
  }
}

abstract interface class ServerStore {
  Future<ServerList> read();
  Future<void> write(ServerList list);
}

class SharedPreferencesServerStore implements ServerStore {
  static const _key = 'servers';
  final SharedPreferencesAsync _prefs = SharedPreferencesAsync();

  @override
  Future<ServerList> read() async {
    final raw = await _prefs.getString(_key);
    if (raw == null) return const ServerList();
    final json = jsonDecode(raw) as Map<String, dynamic>;
    return ServerList(
      servers: [
        for (final item in json['servers'] as List<dynamic>)
          SavedServer.fromJson(item as Map<String, dynamic>),
      ],
      currentId: json['current'] as String?,
    );
  }

  @override
  Future<void> write(ServerList list) => _prefs.setString(
    _key,
    jsonEncode({
      'servers': [for (final s in list.servers) s.toJson()],
      'current': list.currentId,
    }),
  );
}

class MemoryServerStore implements ServerStore {
  MemoryServerStore([this._list = const ServerList()]);

  ServerList _list;

  @override
  Future<ServerList> read() async => _list;

  @override
  Future<void> write(ServerList list) async => _list = list;
}

final serverStoreProvider = Provider<ServerStore>(
  (ref) => SharedPreferencesServerStore(),
);

final forgetServerTokensProvider = Provider<Future<void> Function(String)>(
  (ref) => forgetServerTokens,
);

const _apiBaseUrlOverride = String.fromEnvironment('API_BASE_URL');

/// App 由用户自己添加和切换服务器；Web 默认连页面所在的服务器，不需要管理。
/// 开发时用 --dart-define=API_BASE_URL 固定地址，也不走服务器列表。
final manageServersProvider = Provider<bool>(
  (ref) => !kIsWeb && _apiBaseUrlOverride.isEmpty,
);

final serverListProvider =
    AsyncNotifierProvider<ServerListNotifier, ServerList>(
      ServerListNotifier.new,
    );

class ServerListNotifier extends AsyncNotifier<ServerList> {
  ServerStore get _store => ref.read(serverStoreProvider);

  @override
  Future<ServerList> build() => ref.watch(serverStoreProvider).read();

  /// 添加并切换到这台服务器。同一台（server_id 相同）只更新地址和名称。
  Future<void> add(SavedServer server) => _save(
    ServerList(
      servers: [
        for (final s in _list.servers)
          if (s.id != server.id) s,
        server,
      ],
      currentId: server.id,
    ),
  );

  Future<void> switchTo(String id) =>
      _save(ServerList(servers: _list.servers, currentId: id));

  /// 同时清掉本机为它保存的令牌。删掉当前服务器时不自动切到别的，让用户自己选。
  Future<void> remove(String id) async {
    final list = _list;
    await ref.read(forgetServerTokensProvider)(id);
    await _save(
      ServerList(
        servers: [
          for (final s in list.servers)
            if (s.id != id) s,
        ],
        currentId: list.currentId == id ? null : list.currentId,
      ),
    );
  }

  ServerList get _list => state.value ?? const ServerList();

  Future<void> _save(ServerList list) async {
    await _store.write(list);
    state = AsyncData(list);
  }
}

/// 路由守卫看的服务器状态。
enum ServerGate { loading, missing, ready }

final serverGateProvider = Provider<ServerGate>((ref) {
  if (!ref.watch(manageServersProvider)) return ServerGate.ready;
  final list = ref.watch(serverListProvider);
  if (!list.hasValue) {
    return list.hasError ? ServerGate.missing : ServerGate.loading;
  }
  return list.value!.current == null ? ServerGate.missing : ServerGate.ready;
});

/// 把用户输入的地址整理成 http(s)://host[:port][/path]。没写协议时按 http 处理（局域网常见）。
/// 不像地址时返回 null。
String? normalizeServerUrl(String input) {
  var text = input.trim();
  if (text.isEmpty) return null;
  if (!text.contains('://')) text = 'http://$text';
  final uri = Uri.tryParse(text);
  if (uri == null ||
      !(uri.scheme == 'http' || uri.scheme == 'https') ||
      uri.host.isEmpty ||
      uri.hasQuery ||
      uri.hasFragment ||
      uri.userInfo.isNotEmpty) {
    return null;
  }
  final path = uri.path.replaceAll(RegExp(r'/+$'), '');
  return uri.replace(path: path).toString().replaceAll(RegExp(r'/+$'), '');
}

/// 确认地址上跑的是光圈服务端，返回它的自我描述。
typedef ServerProbe = Future<ServerInfo> Function(String url);

final serverProbeProvider = Provider<ServerProbe>((ref) => probeServer);

Future<ServerInfo> probeServer(String url) async {
  final dio = Dio(
    BaseOptions(
      baseUrl: url,
      connectTimeout: const Duration(seconds: 8),
      receiveTimeout: const Duration(seconds: 10),
    ),
  );
  try {
    return await fetchServerInfo(dio);
  } on DioException catch (e) {
    // 地址上是别的服务：404、返回网页（响应体不是 JSON 对象，转换时出错）等
    final response = e.response;
    if (e.error is TypeError || (response != null && response.data is! Map)) {
      throw const ApiError(code: 'not_iris', message: '这个地址不是光圈服务器');
    }
    if (response == null) {
      throw const ApiError(code: 'network', message: '连不上这个地址，请检查地址、端口和网络');
    }
    rethrow;
  } finally {
    dio.close();
  }
}
