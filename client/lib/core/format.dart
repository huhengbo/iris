/// 文件大小，按 1024 进位。
String formatSize(int? bytes) {
  if (bytes == null) return '大小未知';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  var value = bytes.toDouble();
  var unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit++;
  }
  final digits = unit == 0 || value >= 100 ? 0 : 1;
  return '${value.toStringAsFixed(digits)} ${units[unit]}';
}

String formatMinutes(int minutes) {
  if (minutes < 60) return '$minutes 分钟';
  final rest = minutes % 60;
  return rest == 0 ? '${minutes ~/ 60} 小时' : '${minutes ~/ 60} 小时 $rest 分';
}

/// 播放时间，如 `1:02:03`、`4:05`。
String formatClock(Duration d) {
  final h = d.inHours;
  final m = d.inMinutes.remainder(60).toString().padLeft(h > 0 ? 2 : 1, '0');
  final s = d.inSeconds.remainder(60).toString().padLeft(2, '0');
  return h > 0 ? '$h:$m:$s' : '$m:$s';
}

/// 日期，今年的省略年份：「1月20日」「2008年1月20日」。
String formatDate(DateTime time, {DateTime? now}) {
  final current = now ?? DateTime.now();
  return time.year == current.year
      ? '${time.month}月${time.day}日'
      : '${time.year}年${time.month}月${time.day}日';
}

/// 日期加时间：「1月20日 08:05」。
String formatDateTime(DateTime time, {DateTime? now}) {
  String two(int v) => v.toString().padLeft(2, '0');
  return '${formatDate(time, now: now)} ${two(time.hour)}:${two(time.minute)}';
}

/// 「刚刚」「5 分钟前」「3 小时前」，超过一天显示日期时间。
String formatAgo(DateTime time, {DateTime? now}) {
  final elapsed = (now ?? DateTime.now()).difference(time);
  if (elapsed.inMinutes < 1) return '刚刚';
  if (elapsed.inHours < 1) return '${elapsed.inMinutes} 分钟前';
  if (elapsed.inDays < 1) return '${elapsed.inHours} 小时前';
  return formatDateTime(time, now: now);
}

/// 服务端给的 ISO 时间，解析不了时原样返回。
String formatIsoAgo(String? iso) {
  if (iso == null || iso.isEmpty) return '';
  final time = DateTime.tryParse(iso);
  return time == null ? iso : formatAgo(time.toLocal());
}

/// 观看时长等较长的时间：不到 1 分钟时为「不到 1 分钟」，其余同 [formatMinutes]。
String formatDuration(Duration d) =>
    d.inMinutes < 1 ? '不到 1 分钟' : formatMinutes(d.inMinutes);

/// 季集统一写成「第 1 季 第 3 集」。
String formatEpisode(int season, int episode) =>
    season == 0 ? '特别篇 第 $episode 集' : '第 $season 季 第 $episode 集';
