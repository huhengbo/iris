import 'package:flutter/material.dart';

import '../../app/theme.dart';
import 'libraries_api.dart';
import 'maintenance_api.dart';
import 'source_status.dart';
import 'summary_api.dart';

/// 需要处理的一件事，颜色语义同 `Notice`：琥珀＝暂停类，暖红＝需要处理。
/// 「状态 › 仪表盘」的待处理事项和设置菜单行尾的圆点共用。
class AdminHint {
  const AdminHint(this.message, this.color);

  final String message;
  final Color color;

  IconData get icon => switch (color) {
    CinemaColors.error => Icons.error_outline,
    CinemaColors.amber => Icons.warning_amber_rounded,
    _ => Icons.info_outline,
  };
}

/// 来源需要处理时的一行提示，文案用来源名称；没问题的来源不出现在待处理事项里。
AdminHint? sourceHint(SourceStatus source) {
  if (!source.needsAttention) return null;
  final color = source.limited
      ? CinemaColors.amber
      : source.pending
      ? CinemaColors.text2
      : CinemaColors.error;
  return AdminHint('${source.sourceName}：${source.summary}', color);
}

/// 所有需要处理的来源，顺序和接口返回的一致。
List<SourceStatus> sourcesNeedingAttention(AdminSummary summary) => [
  for (final source in summary.sources)
    if (source.needsAttention) source,
];

/// 没处理的警报：颜色按最严重的一条，错误暖红、警告琥珀、只有提示时为次级色
AdminHint? alertHint(List<AdminAlert> alerts) {
  if (alerts.isEmpty) return null;
  final levels = {for (final alert in alerts) alert.level};
  final color = levels.contains('error')
      ? CinemaColors.error
      : levels.contains('warning')
      ? CinemaColors.amber
      : CinemaColors.text2;
  return AdminHint('${alerts.length} 条警报待处理', color);
}

AdminHint? syncHint(SyncJob? latest, List<HeldDeletion> held) {
  if (latest != null &&
      latest.running &&
      latest.waitingReason == 'rate_limited') {
    return const AdminHint('来源限制了请求，同步已暂停，恢复后自动继续', CinemaColors.amber);
  }
  if (latest != null && latest.status == 'failed') {
    final reason = latest.error == null ? '' : '：${latest.error}';
    return AdminHint('上次同步失败$reason', CinemaColors.error);
  }
  if (held.isNotEmpty) {
    // 和同步页的状态提示一致：等待确认删除是需要处理的事，用暖红
    return AdminHint('${held.length} 个资料库有删除等待确认', CinemaColors.error);
  }
  if (latest != null && latest.running) {
    return const AdminHint('正在同步', CinemaColors.text2);
  }
  return null;
}

/// 仪表盘「待处理事项」：需要处理的事、新地址，以及是否要整页打开。
/// 设置项在宽屏切右栏，插件详情这类页面始终整页盖上来。
List<(String path, AdminHint hint, bool fullPage)> attentionItems(
  AdminSummary summary,
  List<AdminAlert> alerts,
) => [
  if (!summary.setupComplete)
    ('/settings/setup', const AdminHint('配置向导还没完成', CinemaColors.amber), false),
  for (final source in sourcesNeedingAttention(summary))
    if (sourceHint(source) case final hint?) (source.settingsPath, hint, true),
  if (syncHint(summary.sync, summary.heldDeletions) case final hint?)
    ('/settings/status/sync', hint, false),
  if (summary.pending > 0)
    (
      '/settings/manage/pending',
      AdminHint('${summary.pending} 项待处理', CinemaColors.amber),
      false,
    ),
  if (alertHint(alerts) case final hint?)
    ('/settings/status/alerts', hint, false),
];

/// 资源来源这一项的综合提示：按最严重的一个来源取颜色。
AdminHint? _sourcesHint(List<SourceStatus> sources) {
  if (sources.isEmpty) return null;
  final color = sources.any((source) => source.failed || source.unavailable)
      ? CinemaColors.error
      : sources.any((source) => source.limited)
      ? CinemaColors.amber
      : CinemaColors.text2;
  return AdminHint('${sources.length} 个来源需要处理', color);
}

/// 设置菜单行尾的圆点：有需要处理的事时提醒，详细说明只在仪表盘里写一次。
Map<String, Color> settingsEntryDots(
  AdminSummary? summary,
  List<AdminAlert> alerts,
) {
  final dots = <String, Color>{};
  if (summary != null) {
    for (final (path, hint) in [
      (
        '/settings/manage/sources',
        _sourcesHint(sourcesNeedingAttention(summary)),
      ),
      ('/settings/status/sync', syncHint(summary.sync, summary.heldDeletions)),
    ]) {
      if (hint != null && hint.color != CinemaColors.text2) {
        dots[path] = hint.color;
      }
    }
  }
  final hint = alertHint(alerts);
  if (hint != null && hint.color != CinemaColors.text2) {
    dots['/settings/status/alerts'] = hint.color;
  }
  if (summary == null) return dots;
  if (summary.pending > 0) {
    dots['/settings/manage/pending'] = CinemaColors.amber;
  }
  if (!summary.setupComplete) {
    dots['/settings/setup'] = CinemaColors.amber;
  }
  return dots;
}
