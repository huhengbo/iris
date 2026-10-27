import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'api/api_client.dart';
import 'api/client_device.dart';
import 'api/token_store.dart';
import 'server.dart';
import 'servers.dart';

/// 后端地址。Web 部署时与页面同源；本地开发可用 --dart-define=API_BASE_URL=http://127.0.0.1:8080 指定。
const _apiBaseUrlOverride = String.fromEnvironment('API_BASE_URL');

String resolveApiBaseUrl() =>
    _apiBaseUrlOverride.isNotEmpty ? _apiBaseUrlOverride : Uri.base.origin;

/// 当前连接的服务器地址：Web 用页面地址，App 用服务器列表里选中的那台（ADR 0017）。
/// App 还没选服务器时为空字符串，路由守卫会先送去添加服务器。
final apiBaseUrlProvider = Provider<String>((ref) {
  if (!ref.watch(manageServersProvider)) return resolveApiBaseUrl();
  return ref.watch(serverListProvider).value?.current?.url ?? '';
});

/// 当前服务器的自我描述，第一次用到令牌时获取。
final FutureProvider<ServerInfo> serverInfoProvider =
    FutureProvider<ServerInfo>(
      (ref) => fetchServerInfo(ref.watch(apiClientProvider)),
    );

final Provider<TokenStore> tokenStoreProvider = Provider<TokenStore>(
  (ref) => SharedPreferencesTokenStore(
    serverId: () async => (await ref.read(serverInfoProvider.future)).id,
  ),
);

final Provider<AccountStore> accountStoreProvider = Provider<AccountStore>(
  (ref) => SharedPreferencesAccountStore(
    serverId: () async => (await ref.read(serverInfoProvider.future)).id,
  ),
);

/// 登录的账号换了一次就加一。API 客户端跟着重建，依赖它的数据（含 ETag 缓存）
/// 全部重新获取，不会把上一个账号的内容带给下一个账号。
final sessionEpochProvider = NotifierProvider<SessionEpochNotifier, int>(
  SessionEpochNotifier.new,
);

class SessionEpochNotifier extends Notifier<int> {
  @override
  int build() => 0;

  void bump() => state++;
}

/// 登录失效次数。路由监听它，变化时回到登录页。
final sessionExpiredProvider = NotifierProvider<SessionExpiredNotifier, int>(
  SessionExpiredNotifier.new,
);

class SessionExpiredNotifier extends Notifier<int> {
  @override
  int build() => 0;

  void signal() => state++;
}

/// 本机的设备 ID、设备名、平台和版本，登录和续期时上报。
final clientDeviceProvider = FutureProvider<ClientDevice>(
  (ref) => loadClientDevice(),
);

final Provider<Dio> apiClientProvider = Provider<Dio>((ref) {
  ref.watch(sessionEpochProvider);
  return createApiClient(
    baseUrl: ref.watch(apiBaseUrlProvider),
    tokenStore: ref.watch(tokenStoreProvider),
    onSessionExpired: () => ref.read(sessionExpiredProvider.notifier).signal(),
    deviceInfo: () async =>
        (await ref.read(clientDeviceProvider.future)).toJson(),
  );
});
