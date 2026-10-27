import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../core/providers.dart';
import '../auth/auth_controller.dart';

/// 首页的显示偏好。目前只有「首页显示我的收藏」，存在本机、按服务器和用户分开，不上传服务器。
abstract interface class HomePreferences {
  Future<bool> readShowFavorites();
  Future<void> writeShowFavorites(bool value);
}

class SharedPreferencesHomePreferences implements HomePreferences {
  /// [scope] 区分服务器和用户，同一台设备上切换账号互不影响。
  SharedPreferencesHomePreferences(String scope)
    : _showFavoritesKey = 'home.show_favorites.$scope';

  final SharedPreferencesAsync _prefs = SharedPreferencesAsync();

  final String _showFavoritesKey;

  @override
  Future<bool> readShowFavorites() async =>
      await _prefs.getBool(_showFavoritesKey) ?? false;

  @override
  Future<void> writeShowFavorites(bool value) =>
      _prefs.setBool(_showFavoritesKey, value);
}

/// 测试用：不碰本机存储。
class MemoryHomePreferences implements HomePreferences {
  MemoryHomePreferences({this.showFavorites = false});

  bool showFavorites;

  @override
  Future<bool> readShowFavorites() async => showFavorites;

  @override
  Future<void> writeShowFavorites(bool value) async => showFavorites = value;
}

final homePreferencesProvider = Provider<HomePreferences>(
  (ref) => SharedPreferencesHomePreferences(
    '${ref.watch(apiBaseUrlProvider)}#'
    '${ref.watch(authControllerProvider).value?.id ?? 0}',
  ),
);

/// 首页是否显示「我的收藏」行，默认关闭。收藏行在稍后观看之后。
final showHomeFavoritesProvider =
    AsyncNotifierProvider<ShowHomeFavorites, bool>(ShowHomeFavorites.new);

class ShowHomeFavorites extends AsyncNotifier<bool> {
  @override
  Future<bool> build() async {
    try {
      return await ref.watch(homePreferencesProvider).readShowFavorites();
    } catch (_) {
      // 本机存储读不出来时按默认关闭，不影响首页其他内容
      return false;
    }
  }

  Future<void> set(bool value) async {
    final previous = state.value ?? false;
    if (previous == value) return;
    state = AsyncData(value);
    try {
      await ref.read(homePreferencesProvider).writeShowFavorites(value);
    } catch (error) {
      if (ref.mounted) state = AsyncData(previous);
      rethrow;
    }
  }
}
