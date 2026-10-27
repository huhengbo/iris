import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import '../play/play_api.dart';
import 'webdav_api.dart';

final subtitleLanguageProvider = FutureProvider.autoDispose<String?>((ref) {
  return ref.watch(preferencesApiProvider).subtitleLanguage();
});

final preferencesApiProvider = Provider<PreferencesApi>(
  (ref) => PreferencesApi(ref.watch(apiClientProvider)),
);

/// 「我的 › 设置」用到的接口：播放偏好和 WebDAV 开关。
class PreferencesApi {
  const PreferencesApi(this._dio);

  final Dio _dio;

  Future<String?> subtitleLanguage() => getSubtitleLanguage(_dio);

  Future<void> setSubtitleLanguage(String? language) =>
      saveSubtitleLanguage(_dio, language);

  Future<WebDavInfo> setWebDav({required bool enabled}) =>
      saveWebDav(_dio, enabled: enabled);
}
