import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/format.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/focus_ring.dart';
import 'admin_widgets.dart';
import 'maintenance_api.dart';

enum _LogLevelFilter {
  all('全部'),
  error('错误'),
  warning('警告'),
  info('信息');

  const _LogLevelFilter(this.label);

  final String label;

  bool matches(ServerLogEntry entry) => switch (this) {
    _LogLevelFilter.all => true,
    _LogLevelFilter.error =>
      entry.level == 'error' || entry.level == 'critical',
    _LogLevelFilter.warning => entry.level == 'warning',
    _LogLevelFilter.info =>
      entry.level != 'error' &&
          entry.level != 'critical' &&
          entry.level != 'warning',
  };
}

/// 日志：服务进程最近的日志，新的在前。点一行看完整内容。
class LogsPage extends ConsumerStatefulWidget {
  const LogsPage({super.key});

  @override
  ConsumerState<LogsPage> createState() => _LogsPageState();
}

class _LogsPageState extends ConsumerState<LogsPage> {
  final _search = TextEditingController();
  String _query = '';
  _LogLevelFilter _level = _LogLevelFilter.all;

  @override
  void dispose() {
    _search.dispose();
    super.dispose();
  }

  void _clearFilters() {
    _search.clear();
    setState(() {
      _query = '';
      _level = _LogLevelFilter.all;
    });
  }

  bool _matches(ServerLogEntry entry) {
    if (!_level.matches(entry)) return false;
    if (_query.isEmpty) return true;
    final q = _query.toLowerCase();
    return entry.message.toLowerCase().contains(q) ||
        entry.module.toLowerCase().contains(q);
  }

  @override
  Widget build(BuildContext context) {
    final logs = ref.watch(serverLogsProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('日志'),
        actions: [
          IconButton(
            tooltip: '刷新',
            onPressed: () => ref.invalidate(serverLogsProvider),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: AsyncView(
        value: logs,
        onRetry: () => ref.invalidate(serverLogsProvider),
        builder: (items) {
          if (items.isEmpty) {
            return const EmptyState(
              icon: Icons.article_outlined,
              title: '暂无日志',
              message: '服务启动后的运行日志会显示在这里，重启后清空。',
            );
          }
          final filtered = [
            for (final entry in items)
              if (_matches(entry)) entry,
          ];
          return AdminListWidth(
            top: 8,
            maxWidth: adminGridWidth,
            builder: (padding) => Column(
              children: [
                Padding(
                  padding: EdgeInsets.fromLTRB(
                    CinemaSpacing.gutter,
                    8,
                    padding.right + CinemaSpacing.gutter,
                    8,
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      TextField(
                        controller: _search,
                        maxLength: 100,
                        decoration: InputDecoration(
                          prefixIcon: const Icon(Icons.search),
                          hintText: '搜索日志消息或模块',
                          counterText: '',
                          isDense: true,
                          suffixIcon: _query.isEmpty
                              ? null
                              : IconButton(
                                  tooltip: '清除搜索',
                                  onPressed: () {
                                    _search.clear();
                                    setState(() => _query = '');
                                  },
                                  icon: const Icon(Icons.close),
                                ),
                        ),
                        onChanged: (value) =>
                            setState(() => _query = value.trim()),
                      ),
                      const SizedBox(height: 8),
                      Wrap(
                        spacing: 8,
                        runSpacing: 4,
                        children: [
                          for (final level in _LogLevelFilter.values)
                            ChoiceChip(
                              label: Text(level.label),
                              selected: _level == level,
                              onSelected: (_) => setState(() => _level = level),
                            ),
                        ],
                      ),
                    ],
                  ),
                ),
                Expanded(
                  child: filtered.isEmpty
                      ? Padding(
                          padding: EdgeInsets.only(right: padding.right),
                          child: EmptyState(
                            icon: Icons.filter_alt_off_outlined,
                            title: '没有符合条件的日志',
                            action: OutlinedButton(
                              onPressed: _clearFilters,
                              child: const Text('清除筛选'),
                            ),
                          ),
                        )
                      : RefreshIndicator(
                          onRefresh: () async =>
                              ref.refresh(serverLogsProvider.future),
                          child: ListView.builder(
                            padding: padding,
                            itemCount: filtered.length,
                            itemBuilder: (context, index) =>
                                _LogTile(entry: filtered[index]),
                          ),
                        ),
                ),
              ],
            ),
          );
        },
      ),
    );
  }
}

/// 日志级别的图标和颜色：错误暖红，警告琥珀，其余次级文字色
(IconData, Color) _levelStyle(String level) => switch (level) {
  'error' || 'critical' => (Icons.error_outline, CinemaColors.error),
  'warning' => (Icons.warning_amber_rounded, CinemaColors.amber),
  _ => (Icons.notes, CinemaColors.text2),
};

/// 日志要精确到秒：「1月20日 08:05:09」
String _logTime(DateTime time) =>
    '${formatDateTime(time)}:${time.second.toString().padLeft(2, '0')}';

class _LogTile extends StatelessWidget {
  const _LogTile({required this.entry});

  final ServerLogEntry entry;

  void _open(BuildContext context) => showDialog<void>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text(entry.module.isEmpty ? '日志' : entry.module),
      content: SingleChildScrollView(child: SelectableText(entry.message)),
      actions: [
        TextButton(
          autofocus: true,
          onPressed: () => Navigator.pop(context),
          child: const Text('关闭'),
        ),
      ],
    ),
  );

  @override
  Widget build(BuildContext context) {
    final (icon, color) = _levelStyle(entry.level);
    final at = entry.at;
    return FocusRing(
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(
          horizontal: CinemaSpacing.gutter,
        ),
        leading: Icon(icon, color: color),
        title: Text(
          entry.message,
          maxLines: 3,
          overflow: TextOverflow.ellipsis,
        ),
        subtitle: Text(
          [
            ?(at == null ? null : _logTime(at)),
            entry.module,
          ].where((part) => part.isNotEmpty).join(' · '),
        ),
        onTap: () => _open(context),
      ),
    );
  }
}
