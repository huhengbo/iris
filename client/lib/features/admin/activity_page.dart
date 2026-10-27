import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/format.dart';
import '../../core/providers.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/snack.dart';
import 'admin_widgets.dart';
import 'busy_icon.dart';
import 'maintenance_api.dart';

/// 播放历史：上面是统计，下面是按时间倒序的播放记录，分页浏览。
class ActivityPage extends ConsumerStatefulWidget {
  const ActivityPage({super.key});

  @override
  ConsumerState<ActivityPage> createState() => _ActivityPageState();
}

class _ActivityPageState extends ConsumerState<ActivityPage> {
  int _offset = 0;

  /// 正在请求停止的播放记录
  int? _stopping;

  void _refresh() {
    ref.invalidate(playStatsProvider);
    ref.invalidate(playHistoryProvider(_offset));
  }

  Future<void> _stop(PlayHistoryEntry entry) async {
    final confirmed = await showConfirmDialog(
      context,
      title: '停止「${entry.title}」的播放？',
      body: '${entry.username} 的播放器会在下次上报进度时停下，并提示管理员已请求停止。',
      action: '停止播放',
      destructive: true,
    );
    if (!confirmed || !mounted) return;
    setState(() => _stopping = entry.id);
    try {
      await stopPlaySession(ref.read(apiClientProvider), entry.id);
      if (mounted) showSnack(context, '已请求停止播放');
      ref.invalidate(playHistoryProvider(_offset));
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    }
    if (mounted) setState(() => _stopping = null);
  }

  @override
  Widget build(BuildContext context) {
    final history = ref.watch(playHistoryProvider(_offset));
    return Scaffold(
      appBar: AppBar(
        title: const Text('播放历史'),
        actions: [
          IconButton(
            tooltip: '刷新',
            onPressed: _refresh,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: AsyncView(
        value: history,
        onRetry: _refresh,
        builder: (page) => page.items.isEmpty && _offset == 0
            ? const EmptyState(
                icon: Icons.history,
                title: '还没有播放记录',
                message: '有人开始播放后，每次播放都会记在这里。',
              )
            : RefreshIndicator(
                onRefresh: () {
                  ref.invalidate(playStatsProvider);
                  return ref.refresh(playHistoryProvider(_offset).future);
                },
                child: AdminListWidth(
                  builder: (padding) => ListView(
                    padding: padding,
                    children: [
                      if (_offset == 0) const _Stats(),
                      for (final entry in page.items)
                        _HistoryTile(
                          entry: entry,
                          stopping: _stopping == entry.id,
                          // 一次只处理一个停止请求
                          onStop: entry.stoppable && _stopping == null
                              ? () => _stop(entry)
                              : null,
                        ),
                      Pager(
                        offset: _offset,
                        total: page.total,
                        onChanged: (offset) => setState(() => _offset = offset),
                      ),
                    ],
                  ),
                ),
              ),
      ),
    );
  }
}

/// 播放次数、观看时长和各播放方式的次数。加载失败时不显示，不影响下面的列表。
class _Stats extends ConsumerWidget {
  const _Stats();

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final stats = ref.watch(playStatsProvider).value;
    if (stats == null) return const SizedBox.shrink();
    final text = Theme.of(context).textTheme;
    final modes = [
      for (final (mode, count) in stats.byMode)
        '${playModeLabel(mode)} $count 次',
    ];
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        CinemaSpacing.gutter,
        CinemaSpacing.heading,
        CinemaSpacing.gutter,
        CinemaSpacing.heading,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        spacing: 8,
        children: [
          Wrap(
            spacing: CinemaSpacing.section,
            runSpacing: CinemaSpacing.heading,
            children: [
              _Stat(label: '播放次数', value: '${stats.plays}'),
              _Stat(
                label: '观看时长',
                value: formatDuration(Duration(seconds: stats.watchedSeconds)),
              ),
            ],
          ),
          if (modes.isNotEmpty)
            Text(
              modes.join(' · '),
              style: text.bodySmall?.copyWith(color: CinemaColors.text2),
            ),
        ],
      ),
    );
  }
}

class _Stat extends StatelessWidget {
  const _Stat({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(value, style: text.titleLarge),
        Text(label, style: text.bodySmall?.copyWith(color: CinemaColors.text2)),
      ],
    );
  }
}

class _HistoryTile extends StatelessWidget {
  const _HistoryTile({
    required this.entry,
    required this.stopping,
    required this.onStop,
  });

  final PlayHistoryEntry entry;
  final bool stopping;
  final VoidCallback? onStop;

  @override
  Widget build(BuildContext context) {
    final watched = entry.watchedSeconds;
    final client = playClientLabel(entry.client);
    final mode = playModeLabel(entry.mode);
    final detail = [
      entry.username,
      client,
      // 网页客户端的播放方式也叫网页，不重复写
      if (mode != client) mode,
      formatAgo(entry.createdAt),
      if (watched != null && watched > 0)
        '观看 ${formatDuration(Duration(seconds: watched))}',
    ];
    return FocusRing(
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(
          horizontal: CinemaSpacing.gutter,
        ),
        leading: const Icon(Icons.play_circle_outline),
        title: Text(entry.title, maxLines: 1, overflow: TextOverflow.ellipsis),
        subtitle: Text(detail.join(' · ')),
        trailing: !entry.stoppable
            ? null
            : stopping
            ? const BusyIcon()
            : IconButton(
                tooltip: '停止播放',
                onPressed: onStop,
                icon: const Icon(Icons.stop_circle_outlined),
              ),
      ),
    );
  }
}
