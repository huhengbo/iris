import 'package:dio/dio.dart';
import 'package:iris/core/providers.dart';
import 'package:iris/core/servers.dart';
import 'package:iris/features/auth/auth_controller.dart';

import 'fake_api.dart';

/// 导航测试用的登录用户：管理员。
class SignedInAdmin extends AuthController {
  @override
  Future<AppUser?> build() async => const AppUser(
    id: 1,
    username: 'admin',
    role: 'admin',
    disabled: false,
    allowSubtitleDownload: true,
  );
}

/// 导航测试用的登录用户：普通用户。
class SignedInViewer extends AuthController {
  @override
  Future<AppUser?> build() async => const AppUser(
    id: 2,
    username: 'alice',
    role: 'user',
    disabled: false,
    allowSubtitleDownload: true,
  );
}

/// 导航测试的假接口和 provider：默认已登录管理员，Web 模式（不管服务器列表），
/// 服务器名「光圈」，两个资料库。
({FakeAdminApi api, List<dynamic> overrides}) navEnv({
  bool admin = true,
  void Function(FakeAdminApi api)? setup,
}) {
  final api = FakeAdminApi()
    ..on('GET /api/libraries', [
      {
        'id': 1,
        'name': '电影',
        'kind': 'movie',
        'item_count': 12,
        'cover': <Object>[],
      },
      {
        'id': 2,
        'name': '剧集',
        'kind': 'series',
        'item_count': 8,
        'cover': <Object>[],
      },
    ])
    ..on('GET /api/system/info', {
      'product': 'iris',
      'server_id': 'server-1',
      'server_name': '光圈',
      'api_version': 1,
    });
  setup?.call(api);
  final dio = Dio(BaseOptions(baseUrl: 'http://test'))..httpClientAdapter = api;
  return (
    api: api,
    overrides: [
      apiClientProvider.overrideWithValue(dio),
      apiBaseUrlProvider.overrideWithValue('http://test'),
      manageServersProvider.overrideWithValue(false),
      authControllerProvider.overrideWith(
        admin ? SignedInAdmin.new : SignedInViewer.new,
      ),
    ],
  );
}
