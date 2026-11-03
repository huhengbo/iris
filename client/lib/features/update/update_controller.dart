import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:package_info_plus/package_info_plus.dart';

import 'update_models.dart';
import 'update_platform.dart';
import 'update_preferences.dart';

/// 应用内升级只在 Android 上有：APK 从 GitHub Releases 下载（ADR 0031）。
final updateSupportedProvider = Provider<bool>(
  (ref) => !kIsWeb && defaultTargetPlatform == TargetPlatform.android,
);

final updatePreferencesProvider = Provider<UpdatePreferences>(
  (ref) => SharedPreferencesUpdatePreferences(),
);

final updateServiceProvider = Provider<UpdateService>(
  (ref) => UpdateService(
    dio: Dio(
      BaseOptions(
        connectTimeout: const Duration(seconds: 15),
        receiveTimeout: const Duration(seconds: 30),
      ),
    ),
    platform: MethodChannelUpdatePlatform(),
    currentVersion: () async => (await PackageInfo.fromPlatform()).version,
  ),
);

/// 给界面看的错误文字。
class UpdateException implements Exception {
  const UpdateException(this.message);

  final String message;

  @override
  String toString() => message;
}

/// 取清单、下载和校验安装包。网络走 GitHub，可在前面拼下载加速前缀。
class UpdateService {
  UpdateService({
    required this._dio,
    required this.platform,
    required this._currentVersion,
  });

  final Dio _dio;
  final UpdatePlatform platform;
  final Future<String> Function() _currentVersion;

  Future<String> currentVersion() => _currentVersion();

  Future<AppUpdate> fetchManifest(String prefix) async {
    final Response<String> response;
    try {
      response = await _dio.get<String>(
        withDownloadPrefix(prefix, updateManifestUrl),
        // GitHub 附件的类型是 application/octet-stream，自己解析
        options: Options(responseType: ResponseType.plain),
      );
    } on DioException catch (error) {
      throw UpdateException(describeNetworkError(error));
    }
    Object? json;
    try {
      json = jsonDecode(response.data ?? '');
    } on FormatException {
      json = null;
    }
    final update = AppUpdate.tryParse(json);
    if (update == null) throw const UpdateException('更新信息的格式不对');
    return update;
  }

  /// 按本机架构挑安装包，没有合适的返回空。
  Future<UpdateApk?> apkFor(AppUpdate update) async =>
      selectApk(update.apks, await platform.supportedAbis());

  /// 下载并校验，返回安装包路径。同一版本已经下载好且校验通过时直接复用。
  Future<String> download(
    AppUpdate update,
    UpdateApk apk, {
    required String prefix,
    ProgressCallback? onProgress,
    CancelToken? cancelToken,
  }) async {
    final path = await platform.prepareFile('iris-${update.version}.apk');
    if (await platform.sha256(path) == apk.sha256) return path;
    try {
      await _dio.download(
        withDownloadPrefix(prefix, apk.url),
        path,
        onReceiveProgress: onProgress,
        cancelToken: cancelToken,
      );
    } on DioException catch (error) {
      if (CancelToken.isCancel(error)) rethrow;
      throw UpdateException(describeNetworkError(error));
    }
    if (await platform.sha256(path) != apk.sha256) {
      await platform.clear();
      throw const UpdateException('安装包校验失败，可能没下载完整，请重试');
    }
    return path;
  }
}

/// 国内连 GitHub 经常超时或被重置，失败时顺带提示加速前缀在哪里设置。
String describeNetworkError(DioException error) {
  final status = error.response?.statusCode;
  if (status == 404) return '还没有可用的更新信息（404）';
  if (status != null) return '服务器返回 $status';
  return '连不上 GitHub。网络较慢时可以在「设置 › 本机 › 常规」填写下载加速前缀';
}

/// 一次检查的结果。
sealed class UpdateStatus {
  const UpdateStatus();
}

class UpdateIdle extends UpdateStatus {
  const UpdateIdle();
}

class UpdateChecking extends UpdateStatus {
  const UpdateChecking();
}

class UpdateUpToDate extends UpdateStatus {
  const UpdateUpToDate(this.current);

  final String current;
}

class UpdateAvailable extends UpdateStatus {
  const UpdateAvailable({
    required this.update,
    required this.apk,
    required this.current,
  });

  final AppUpdate update;

  /// 没有适合本机架构的安装包时为空。
  final UpdateApk? apk;

  final String current;
}

class UpdateFailed extends UpdateStatus {
  const UpdateFailed(this.message);

  final String message;
}

final updateControllerProvider =
    NotifierProvider<UpdateController, UpdateStatus>(UpdateController.new);

class UpdateController extends Notifier<UpdateStatus> {
  /// 每次启动只自动检查一次，回到主界面不会重复触发。
  bool _autoChecked = false;

  @override
  UpdateStatus build() => const UpdateIdle();

  /// 手动检查：不看间隔，也不管是否跳过过这个版本。
  Future<UpdateStatus> check() async {
    if (state is UpdateChecking) return state;
    state = const UpdateChecking();
    final result = await _check();
    if (ref.mounted) state = result;
    return result;
  }

  /// 启动时的自动检查。需要弹窗时返回新版本，否则返回空；失败不打扰用户。
  Future<UpdateAvailable?> autoCheck({DateTime? now}) async {
    if (_autoChecked || !ref.read(updateSupportedProvider)) return null;
    _autoChecked = true;
    final prefs = ref.read(updatePreferencesProvider);
    final at = now ?? DateTime.now();
    try {
      final due = autoCheckDue(
        now: at,
        lastCheckAt: await prefs.readLastCheckAt(),
        enabled: await prefs.readAutoCheck(),
      );
      if (!due) return null;
      // 失败也记一次，断网时不会每次启动都去请求
      await prefs.writeLastCheckAt(at);
    } catch (error) {
      debugPrint('读取升级设置失败：$error');
      return null;
    }
    if (state is UpdateChecking) return null;
    state = const UpdateChecking();
    final result = await _check();
    if (!ref.mounted) return null;
    state = result;
    if (result is! UpdateAvailable) return null;
    final skipped = await prefs.readSkippedVersion();
    return shouldAutoPrompt(result.update, skippedVersion: skipped)
        ? result
        : null;
  }

  Future<void> skip(String version) =>
      ref.read(updatePreferencesProvider).writeSkippedVersion(version);

  Future<UpdateStatus> _check() async {
    final service = ref.read(updateServiceProvider);
    try {
      final prefix = await ref
          .read(updatePreferencesProvider)
          .readDownloadPrefix();
      final current = await service.currentVersion();
      final update = await service.fetchManifest(prefix);
      final order = compareVersions(update.version, current);
      if (order == null) {
        return UpdateFailed('无法比较版本号：$current 和 ${update.version}');
      }
      if (order <= 0) return UpdateUpToDate(current);
      return UpdateAvailable(
        update: update,
        apk: await service.apkFor(update),
        current: current,
      );
    } on UpdateException catch (error) {
      return UpdateFailed(error.message);
    } catch (error) {
      debugPrint('检查更新失败：$error');
      return UpdateFailed('检查更新失败：$error');
    }
  }
}

/// 升级的本机设置：自动检查开关和下载加速前缀。
class UpdateSettings {
  const UpdateSettings({required this.autoCheck, required this.downloadPrefix});

  final bool autoCheck;
  final String downloadPrefix;
}

final updateSettingsProvider =
    AsyncNotifierProvider<UpdateSettingsNotifier, UpdateSettings>(
      UpdateSettingsNotifier.new,
    );

class UpdateSettingsNotifier extends AsyncNotifier<UpdateSettings> {
  @override
  Future<UpdateSettings> build() async {
    final prefs = ref.watch(updatePreferencesProvider);
    return UpdateSettings(
      autoCheck: await prefs.readAutoCheck(),
      downloadPrefix: await prefs.readDownloadPrefix(),
    );
  }

  Future<void> setAutoCheck(bool value) async {
    await ref.read(updatePreferencesProvider).writeAutoCheck(value);
    final current = state.value;
    if (ref.mounted && current != null) {
      state = AsyncData(
        UpdateSettings(
          autoCheck: value,
          downloadPrefix: current.downloadPrefix,
        ),
      );
    }
  }

  /// 调用前先用 [downloadPrefixError] 校验；这里按统一格式保存。
  Future<void> setDownloadPrefix(String raw) async {
    final value = normalizeDownloadPrefix(raw);
    await ref.read(updatePreferencesProvider).writeDownloadPrefix(value);
    final current = state.value;
    if (ref.mounted && current != null) {
      state = AsyncData(
        UpdateSettings(autoCheck: current.autoCheck, downloadPrefix: value),
      );
    }
  }
}
