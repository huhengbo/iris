import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../../core/providers.dart';
import '../auth/auth_controller.dart';

/// 资料库「资料库」标签的视图：网格或列表。
enum LibraryViewMode { grid, list }

/// 视图选择的保存位置。存在本机，按服务器和用户分开，不上传服务器。
abstract interface class LibraryPreferences {
  Future<LibraryViewMode> readViewMode();
  Future<void> writeViewMode(LibraryViewMode value);
}

class SharedPreferencesLibraryPreferences implements LibraryPreferences {
  SharedPreferencesLibraryPreferences(String scope)
    : _viewModeKey = 'library.view_mode.$scope';

  final SharedPreferencesAsync _prefs = SharedPreferencesAsync();
  final String _viewModeKey;

  @override
  Future<LibraryViewMode> readViewMode() async =>
      await _prefs.getString(_viewModeKey) == 'list'
      ? LibraryViewMode.list
      : LibraryViewMode.grid;

  @override
  Future<void> writeViewMode(LibraryViewMode value) =>
      _prefs.setString(_viewModeKey, value.name);
}

/// 测试用：不碰本机存储。
class MemoryLibraryPreferences implements LibraryPreferences {
  MemoryLibraryPreferences({this.viewMode = LibraryViewMode.grid});

  LibraryViewMode viewMode;

  @override
  Future<LibraryViewMode> readViewMode() async => viewMode;

  @override
  Future<void> writeViewMode(LibraryViewMode value) async => viewMode = value;
}

final libraryPreferencesProvider = Provider<LibraryPreferences>(
  (ref) => SharedPreferencesLibraryPreferences(
    '${ref.watch(apiBaseUrlProvider)}#'
    '${ref.watch(authControllerProvider).value?.id ?? 0}',
  ),
);

/// 当前资料库页的视图，默认网格。读不出来时也按网格显示，不影响海报墙本身。
final libraryViewModeProvider =
    AsyncNotifierProvider<LibraryViewModeNotifier, LibraryViewMode>(
      LibraryViewModeNotifier.new,
    );

class LibraryViewModeNotifier extends AsyncNotifier<LibraryViewMode> {
  @override
  Future<LibraryViewMode> build() async {
    try {
      return await ref.watch(libraryPreferencesProvider).readViewMode();
    } catch (_) {
      return LibraryViewMode.grid;
    }
  }

  Future<void> set(LibraryViewMode value) async {
    final previous = state.value ?? LibraryViewMode.grid;
    if (previous == value) return;
    state = AsyncData(value);
    try {
      await ref.read(libraryPreferencesProvider).writeViewMode(value);
    } catch (error) {
      if (ref.mounted) state = AsyncData(previous);
      rethrow;
    }
  }
}
