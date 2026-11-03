import 'package:shared_preferences/shared_preferences.dart';

/// 升级相关的本机设置。整台设备共用，不按服务器或用户区分。
abstract interface class UpdatePreferences {
  Future<bool> readAutoCheck();
  Future<void> writeAutoCheck(bool value);

  /// 下载加速前缀，没有设置时为空串。
  Future<String> readDownloadPrefix();
  Future<void> writeDownloadPrefix(String value);

  Future<DateTime?> readLastCheckAt();
  Future<void> writeLastCheckAt(DateTime value);

  /// 用户选了“跳过此版本”的版本号。
  Future<String?> readSkippedVersion();
  Future<void> writeSkippedVersion(String value);
}

class SharedPreferencesUpdatePreferences implements UpdatePreferences {
  final SharedPreferencesAsync _prefs = SharedPreferencesAsync();

  static const _autoCheck = 'update.auto_check';
  static const _downloadPrefix = 'update.download_prefix';
  static const _lastCheckAt = 'update.last_check_at';
  static const _skippedVersion = 'update.skipped_version';

  @override
  Future<bool> readAutoCheck() async =>
      await _prefs.getBool(_autoCheck) ?? true;

  @override
  Future<void> writeAutoCheck(bool value) => _prefs.setBool(_autoCheck, value);

  @override
  Future<String> readDownloadPrefix() async =>
      await _prefs.getString(_downloadPrefix) ?? '';

  @override
  Future<void> writeDownloadPrefix(String value) =>
      _prefs.setString(_downloadPrefix, value);

  @override
  Future<DateTime?> readLastCheckAt() async {
    final millis = await _prefs.getInt(_lastCheckAt);
    return millis == null ? null : DateTime.fromMillisecondsSinceEpoch(millis);
  }

  @override
  Future<void> writeLastCheckAt(DateTime value) =>
      _prefs.setInt(_lastCheckAt, value.millisecondsSinceEpoch);

  @override
  Future<String?> readSkippedVersion() => _prefs.getString(_skippedVersion);

  @override
  Future<void> writeSkippedVersion(String value) =>
      _prefs.setString(_skippedVersion, value);
}

/// 测试用：不碰本机存储。
class MemoryUpdatePreferences implements UpdatePreferences {
  MemoryUpdatePreferences({
    this.autoCheck = true,
    this.downloadPrefix = '',
    this.lastCheckAt,
    this.skippedVersion,
  });

  bool autoCheck;
  String downloadPrefix;
  DateTime? lastCheckAt;
  String? skippedVersion;

  @override
  Future<bool> readAutoCheck() async => autoCheck;

  @override
  Future<void> writeAutoCheck(bool value) async => autoCheck = value;

  @override
  Future<String> readDownloadPrefix() async => downloadPrefix;

  @override
  Future<void> writeDownloadPrefix(String value) async =>
      downloadPrefix = value;

  @override
  Future<DateTime?> readLastCheckAt() async => lastCheckAt;

  @override
  Future<void> writeLastCheckAt(DateTime value) async => lastCheckAt = value;

  @override
  Future<String?> readSkippedVersion() async => skippedVersion;

  @override
  Future<void> writeSkippedVersion(String value) async =>
      skippedVersion = value;
}
