// 更新清单和版本号。只放纯 Dart 逻辑，不碰网络和平台接口，方便单元测试。

/// GitHub Releases 的清单地址。发布流水线把 update.json 作为 latest release 的附件上传。
const updateManifestUrl =
    'https://github.com/huhengbo/iris/releases/latest/download/update.json';

/// 一天的自动检查间隔。
const updateCheckInterval = Duration(hours: 24);

/// 某个 ABI 的安装包信息。
class UpdateApk {
  const UpdateApk({
    required this.url,
    required this.size,
    required this.sha256,
  });

  final String url;

  /// 清单里没写或类型不对时为空，界面上显示「大小未知」。
  final int? size;

  /// 小写 hex。
  final String sha256;
}

/// update.json 解析后的结果。
class AppUpdate {
  const AppUpdate({
    required this.version,
    required this.versionCode,
    required this.notes,
    required this.minVersion,
    required this.apks,
  });

  final String version;

  /// 只做记录；split-per-abi 会往 versionCode 上加 ABI 编号，不能用来比较新旧。
  final int? versionCode;

  /// Markdown 原文，界面上按纯文本显示。
  final String notes;

  /// 保留，暂不启用：原本用于低于该版本时强制更新，目前不据此拦截。
  final String? minVersion;

  /// ABI 到安装包的映射，无效条目在解析时已丢弃。
  final Map<String, UpdateApk> apks;

  /// 解析清单。整体结构或版本号不合法时返回 null；单个 ABI 条目坏掉只跳过该条，
  /// 不抛异常，避免一份写坏的清单让客户端崩溃。
  static AppUpdate? tryParse(Object? json) {
    if (json is! Map<String, Object?>) return null;
    final version = json['version'];
    if (version is! String || version.trim().isEmpty) return null;

    final apks = <String, UpdateApk>{};
    final rawApks = json['apks'];
    if (rawApks is Map<String, Object?>) {
      for (final entry in rawApks.entries) {
        final abi = entry.key;
        final value = entry.value;
        if (abi.isEmpty || value is! Map<String, Object?>) continue;
        final url = value['url'];
        final sha256 = value['sha256'];
        if (url is! String ||
            url.isEmpty ||
            sha256 is! String ||
            sha256.isEmpty) {
          continue;
        }
        final size = value['size'];
        apks[abi] = UpdateApk(
          url: url,
          size: size is int && size >= 0 ? size : null,
          sha256: sha256.toLowerCase(),
        );
      }
    }

    final notes = json['notes'];
    final versionCode = json['version_code'];
    final minVersion = json['min_version'];
    return AppUpdate(
      version: version.trim(),
      versionCode: versionCode is int ? versionCode : null,
      notes: notes is String ? notes : '',
      minVersion: minVersion is String && minVersion.isNotEmpty
          ? minVersion
          : null,
      apks: Map.unmodifiable(apks),
    );
  }
}

final _digits = RegExp(r'^[0-9]+$');

/// 把 `x.y.z` 拆成数字。不是恰好三段数字时返回 null。
List<int>? parseVersion(String value) {
  final parts = value.trim().split('.');
  if (parts.length != 3) return null;
  final numbers = <int>[];
  for (final part in parts) {
    if (!_digits.hasMatch(part)) return null;
    final number = int.tryParse(part);
    if (number == null) return null;
    numbers.add(number);
  }
  return numbers;
}

/// 逐段按数字比较版本号：大于返回 1，相等 0，小于 -1；任一版本号不合法时返回 null。
int? compareVersions(String left, String right) {
  final a = parseVersion(left);
  final b = parseVersion(right);
  if (a == null || b == null) return null;
  for (var i = 0; i < 3; i++) {
    if (a[i] != b[i]) return a[i] > b[i] ? 1 : -1;
  }
  return 0;
}

/// 按设备支持的 ABI 顺序挑第一个清单里有的安装包；都没有时返回 null。
UpdateApk? selectApk(Map<String, UpdateApk> apks, List<String> supportedAbis) {
  for (final abi in supportedAbis) {
    final apk = apks[abi];
    if (apk != null) return apk;
  }
  return null;
}

/// 是否到了自动检查的时间。从没检查过、或系统时间被往回调过时都检查一次；
/// 失败也算一次，避免断网时每次启动都发请求。
bool autoCheckDue({
  required DateTime now,
  required DateTime? lastCheckAt,
  required bool enabled,
}) {
  if (!enabled) return false;
  if (lastCheckAt == null) return true;
  if (now.isBefore(lastCheckAt)) return true;
  return now.difference(lastCheckAt) >= updateCheckInterval;
}

/// 自动检查时是否弹窗。用户跳过过的版本仍然更新状态，只是不再主动打扰。
bool shouldAutoPrompt(AppUpdate update, {required String? skippedVersion}) =>
    skippedVersion != update.version;

final _httpPrefix = RegExp(r'^https?://\S+$', caseSensitive: false);

/// 校验下载加速前缀。空串合法（表示不加速），其他情况必须以 http(s):// 开头。
/// 返回给界面显示的错误文字，合法时返回 null。
String? downloadPrefixError(String raw) {
  final value = raw.trim();
  if (value.isEmpty) return null;
  if (!_httpPrefix.hasMatch(value)) {
    return '需要以 http:// 或 https:// 开头';
  }
  return null;
}

/// 保存和拼接前统一格式：去掉首尾空白、只补末尾的 `/`，不动路径。
/// 不合法时按空串处理（界面不会保存非法值，这里兜底旧数据）。
String normalizeDownloadPrefix(String raw) {
  final value = raw.trim();
  if (value.isEmpty || !_httpPrefix.hasMatch(value)) return '';
  return value.endsWith('/') ? value : '$value/';
}

/// 把加速前缀拼在 GitHub 原始地址前；没有前缀时原样返回。
String withDownloadPrefix(String prefix, String url) {
  final value = normalizeDownloadPrefix(prefix);
  return value.isEmpty ? url : '$value$url';
}

final _heading = RegExp(r'^#{1,6}\s+', multiLine: true);
final _bold = RegExp(r'\*\*(.+?)\*\*');
final _code = RegExp(r'`([^`]+)`');
final _blankLines = RegExp(r'\n{3,}');

/// 更新说明是 CHANGELOG 的 Markdown，对话框里按纯文本显示：去掉标题井号、加粗和行内代码的标记，
/// 列表的 `- ` 保留。
String plainNotes(String markdown) => markdown
    .replaceAll(_heading, '')
    .replaceAllMapped(_bold, (m) => m[1]!)
    .replaceAllMapped(_code, (m) => m[1]!)
    .replaceAll(_blankLines, '\n\n')
    .trim();
