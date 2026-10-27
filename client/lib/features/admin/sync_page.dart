import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/providers.dart';
import '../../core/format.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/notice.dart';
import '../../core/widgets/section_title.dart';
import '../../core/widgets/snack.dart';
import 'admin_widgets.dart';
import 'libraries_api.dart';

part 'sync_sections.dart';
part 'sync_history.dart';

/// 有任务在跑时刷新当前任务的间隔
const syncPollInterval = Duration(seconds: 2);

/// 管理后台的同步页：当前任务、待确认的删除、手动同步、定时同步和历史记录。
class SyncPage extends ConsumerStatefulWidget {
  const SyncPage({super.key});

  @override
  ConsumerState<SyncPage> createState() => _SyncPageState();
}

class _SyncPageState extends ConsumerState<SyncPage> {
  Timer? _poll;
  bool _starting = false;

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  /// 有任务时定时刷新；任务结束的那一刻刷新历史和资料库统计
  void _watchJob(SyncJob? previous, SyncJob? next) {
    if (next != null) {
      _poll ??= Timer.periodic(
        syncPollInterval,
        (_) => ref.invalidate(currentSyncProvider),
      );
      return;
    }
    _poll?.cancel();
    _poll = null;
    if (previous != null) {
      ref.invalidate(syncJobsProvider);
      ref.invalidate(adminLibrariesProvider);
      ref.invalidate(syncScheduleProvider);
      ref.invalidate(heldDeletionsProvider);
    }
  }

  Future<void> _start() async {
    if (_starting) return;
    setState(() => _starting = true);
    try {
      await startSync(ref.read(apiClientProvider));
      ref.invalidate(currentSyncProvider);
    } catch (e) {
      if (mounted) showApiErrorSnack(context, e);
      ref.invalidate(currentSyncProvider);
    }
    if (mounted) setState(() => _starting = false);
  }

  Future<void> _refresh() async {
    ref.invalidate(syncJobsProvider);
    ref.invalidate(syncScheduleProvider);
    ref.invalidate(heldDeletionsProvider);
    final _ = await ref.refresh(currentSyncProvider.future);
  }

  @override
  Widget build(BuildContext context) {
    ref.listen(
      currentSyncProvider,
      (previous, next) => _watchJob(previous?.value, next.value),
    );
    final current = ref.watch(currentSyncProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('同步'),
        actions: [
          // 遥控器没法下拉刷新
          IconButton(
            tooltip: '刷新',
            onPressed: _refresh,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: AdminListWidth(
          bottom: 32,
          builder: (padding) => ListView(
            padding:
                padding +
                const EdgeInsets.symmetric(horizontal: CinemaSpacing.gutter),
            children: [
              const SectionTitle('当前任务', first: true, inset: false),
              const SizedBox(height: CinemaSpacing.heading - 4),
              // 轮询刷新或偶发失败时保留上一次的数据，不闪加载圈
              if (current.hasValue)
                current.value == null
                    ? _Idle(starting: _starting, onStart: _start)
                    : _RunningJob(job: current.value!)
              else if (current.hasError)
                ErrorRetry(
                  message: ApiError.from(current.error!).message,
                  onRetry: () => ref.invalidate(currentSyncProvider),
                )
              else
                const _Loading(),
              const _HeldDeletions(),
              const SizedBox(height: CinemaSpacing.section),
              const _AutoSyncSection(),
              const SectionTitle('历史记录', inset: false),
              const _History(),
            ],
          ),
        ),
      ),
    );
  }
}

class _Loading extends StatelessWidget {
  const _Loading();

  @override
  Widget build(BuildContext context) => const Padding(
    padding: EdgeInsets.symmetric(vertical: 24),
    child: Center(child: CircularProgressIndicator()),
  );
}

class _Idle extends StatelessWidget {
  const _Idle({required this.starting, required this.onStart});

  final bool starting;
  final VoidCallback onStart;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          '没有正在进行的同步。同步只读取资源来源，不会改动来源上的文件。',
          style: text.bodyMedium?.copyWith(color: CinemaColors.text2),
        ),
        const SizedBox(height: 16),
        FilledButton(
          onPressed: starting ? null : onStart,
          child: starting
              ? const SizedBox.square(
                  dimension: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Text('同步全部资料库'),
        ),
      ],
    );
  }
}

class _RunningJob extends StatelessWidget {
  const _RunningJob({required this.job});

  final SyncJob job;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final muted = text.bodySmall?.copyWith(color: CinemaColors.text2);
    final total = job.progressTotal;
    final unit = _phaseUnit(job.phase);
    final waiting = job.waitingAt(DateTime.now());
    return Semantics(
      container: true,
      liveRegion: true,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (waiting) ...[
            Notice(
              icon: Icons.pause_circle_outline,
              color: CinemaColors.amber,
              title: job.waitingReason == 'rate_limited'
                  ? '来源限制了请求，同步已暂停'
                  : '来源正忙，同步已暂停',
              body: job.waitingReason == 'rate_limited'
                  ? '预计 ${formatDateTime(job.waitingUntil!)} 自动继续，不需要重新登录。'
                  : '预计 ${formatDateTime(job.waitingUntil!)} 自动继续。',
            ),
            const SizedBox(height: 16),
          ],
          Row(
            children: [
              Expanded(
                child: Text(
                  '${job.title} · ${syncPhaseLabel(job.phase)}',
                  style: text.titleSmall,
                ),
              ),
              if (total > 0)
                Text('${job.progressDone} / $total $unit', style: muted),
            ],
          ),
          const SizedBox(height: 10),
          LinearProgressIndicator(
            value: total > 0 ? job.progressDone / total : null,
            semanticsLabel: '同步进度',
          ),
          const SizedBox(height: 8),
          Text(
            '${_triggerLabel(job.trigger)} · '
            '${formatAgo(job.startedAt)} 开始'
            '${_hasChanges(job.stats) ? ' · ${_statsLine(job.stats)}' : ''}',
            style: muted,
          ),
        ],
      ),
    );
  }
}
