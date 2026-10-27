import '../../core/format.dart';

/// 一个资源来源的插件状态。只说明能不能用，凭据等细节在插件自己的设置页里维护。
class SourceStatus {
  const SourceStatus({
    required this.sourceId,
    required this.sourceName,
    required this.pluginId,
    required this.state,
    this.message,
    this.resumeAt,
    this.page,
  });

  factory SourceStatus.fromJson(Map<String, dynamic> json) => SourceStatus(
    sourceId: json['source_id'] as int,
    sourceName: json['source_name'] as String,
    pluginId: json['plugin_id'] as String,
    state: json['state'] as String,
    message: json['message'] as String?,
    resumeAt: _time(json['resume_at']),
    page: json['page'] as String?,
  );

  final int sourceId;
  final String sourceName;

  /// 来源由哪个资源插件提供
  final String pluginId;

  /// ok / pending / failed / limited / unavailable；unavailable 表示插件未运行
  final String state;
  final String? message;

  /// 限流恢复的时间
  final DateTime? resumeAt;

  /// 插件设置页的页面 id；为空时打开插件详情的第一页
  final String? page;

  bool get ok => state == 'ok';
  bool get pending => state == 'pending';
  bool get failed => state == 'failed';
  bool get limited => state == 'limited';

  /// 插件没有运行，来源暂时不可用
  bool get unavailable => state == 'unavailable';

  /// 不能正常使用，需要管理员处理
  bool get needsAttention => !ok;

  String get stateLabel => switch (state) {
    'ok' => '正常',
    'pending' => '待完成',
    'limited' => '请求已暂停',
    'failed' => '需要处理',
    'unavailable' => '插件未运行',
    _ => '状态未知',
  };

  /// 状态和原因合成一行；unavailable 只写「插件未运行」，不重复插件给的说明
  String get summary {
    if (unavailable) return '插件未运行';
    return [
      stateLabel,
      if (message case final message? when message.isNotEmpty) message,
      if (resumeAt case final resumeAt?) '预计 ${formatDateTime(resumeAt)} 恢复',
    ].join(' · ');
  }

  /// 插件详情里由 [page] 指定的设置页
  String get settingsPath {
    final path = '/settings/manage/plugins/$pluginId';
    return page == null || page!.isEmpty
        ? path
        : Uri(path: path, queryParameters: {'page': page!}).toString();
  }
}

DateTime? _time(Object? value) =>
    value is String ? DateTime.parse(value).toLocal() : null;
