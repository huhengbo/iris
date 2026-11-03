import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/motion.dart';
import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/providers.dart';
import '../../core/widgets/ai_wait_indicator.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/notice.dart';
import '../../core/widgets/snack.dart';
import 'admin_widgets.dart';
import 'catalog_api.dart';
import 'item_actions.dart' show ItemAction;
import 'tasks_api.dart';
import 'metadata_picker.dart';

part 'pending_rows.dart';
part 'pending_suggestion.dart';
part 'pending_files_tab.dart';

/// 管理后台的待处理页：没匹配上的作品、没归入条目的视频和被忽略的附加内容，逐个手动指定。
class PendingPage extends ConsumerWidget {
  const PendingPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final summary = ref.watch(pendingSummaryProvider).value;
    String count(int? n) => n == null ? '' : ' $n';
    return DefaultTabController(
      length: 3,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('待处理'),
          bottom: TabBar(
            tabs: [
              Tab(text: '未匹配${count(summary?.unmatched)}'),
              Tab(text: '未归入${count(summary?.unplaced)}'),
              Tab(text: '已忽略${count(summary?.ignored)}'),
            ],
          ),
        ),
        body: Column(
          children: [
            if (summary != null && summary.waiting > 0) _Waiting(summary),
            const Expanded(
              child: TabBarView(
                children: [
                  _WorksTab(),
                  _FilesTab(status: 'unplaced'),
                  _FilesTab(status: 'ignored'),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _Waiting extends StatelessWidget {
  const _Waiting(this.summary);

  final PendingSummary summary;

  @override
  Widget build(BuildContext context) {
    final problem = summary.metadataProblem;
    final action = summary.metadataAction;
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        CinemaSpacing.gutter,
        12,
        CinemaSpacing.gutter,
        0,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          Notice(
            icon: Icons.pause_circle_outline,
            color: CinemaColors.amber,
            title: problem == null ? '有作品等待匹配' : '元数据插件暂时不能用',
            body: problem == null
                ? '${summary.waiting} 个作品上次请求元数据插件失败，下次同步时自动重试。'
                : '$problem。处理后同步才会匹配作品、下载海报，'
                      '现在有 ${summary.waiting} 个作品在等待。',
          ),
          if (action != null)
            TextButton(
              onPressed: () => context.push(action),
              child: const Text('去处理'),
            ),
        ],
      ),
    );
  }
}

/// 指定完成后刷新待处理的各个列表和计数。
void _invalidatePending(WidgetRef ref) {
  ref.invalidate(pendingSummaryProvider);
  ref.invalidate(unmatchedWorksProvider);
  ref.invalidate(pendingFilesProvider);
}

/// 列表页通用的指定流程：调用接口、提示结果并提供跳到条目的入口。
Future<void> _assign(
  BuildContext context,
  WidgetRef ref,
  MetadataPick pick,
  Future<int> Function() call,
) async {
  final router = GoRouter.maybeOf(context);
  try {
    final itemId = await call();
    _invalidatePending(ref);
    if (!context.mounted) return;
    showSnack(
      context,
      '已指定为「${pick.title}」',
      action: router == null
          ? null
          : SnackBarAction(
              label: '查看',
              onPressed: () => router.push('/settings/manage/items/$itemId'),
            ),
    );
  } catch (e) {
    if (context.mounted) showApiErrorSnack(context, e);
  }
}

/// 删除会连同来源上的文件一起删，先让管理员确认。
Future<bool> _confirmDelete(
  BuildContext context,
  String title,
  String message,
) async {
  final confirmed = await showConfirmDialog(
    context,
    title: title,
    body: message,
    action: ItemAction.delete.label,
    destructive: true,
  );
  return confirmed && context.mounted;
}

/// 删除待处理的作品或视频，连同来源上的文件；等后台任务结束再刷新列表。
Future<void> _deletePending(
  BuildContext context, {
  List<int> workIds = const [],
  List<int> mediaFileIds = const [],
}) async {
  // 任务可能在离开页面后才结束，刷新和提示不依赖当前页面
  final container = ProviderScope.containerOf(context, listen: false);
  final messenger = ScaffoldMessenger.of(context);
  void show(String text) {
    // 结果出来时直接替换掉「已提交」的提示
    if (messenger.mounted) {
      messenger
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(content: Text(text)));
    }
  }

  final dio = container.read(apiClientProvider);
  try {
    final taskId = await deletePending(
      dio,
      workIds: workIds,
      mediaFileIds: mediaFileIds,
    );
    show('已提交删除任务，正在清理来源上的文件');
    final task = taskId == null ? null : await waitAdminTask(dio, taskId);
    container
      ..invalidate(pendingSummaryProvider)
      ..invalidate(unmatchedWorksProvider)
      ..invalidate(pendingFilesProvider);
    if (task == null || task.active) return;
    show(
      task.status == 'succeeded'
          ? '已删除，来源上的文件已清理'
          : '删除没有完成：${task.error ?? '任务已取消'}。可以在后台任务里重试',
    );
  } catch (e) {
    show(ApiError.from(e).message);
  }
}

void _showBatchReport(
  BuildContext context,
  BatchAssignmentReport report, {
  required String action,
}) {
  final failure = report.results.firstWhere(
    (result) => !result.succeeded,
    orElse: () => const BatchAssignmentResult(id: 0),
  );
  final failureMessage = failure.errorMessage == null
      ? ''
      : '：${failure.errorMessage}';
  final message = report.failed == 0
      ? '$action完成：成功 ${report.succeeded} 项'
      : '$action完成：成功 ${report.succeeded} 项，失败 ${report.failed} 项'
            '${report.unprocessed == 0 ? '' : '，未处理 ${report.unprocessed} 项'}'
            '$failureMessage';
  showSnack(context, message);
}

/// 翻页列表的公共状态：当前偏移和正在指定的行。
mixin _PagedTab<T extends ConsumerStatefulWidget> on ConsumerState<T> {
  int offset = 0;
  final Set<int> busy = {};

  /// 指定完最后一页的最后一行后，退回上一页
  void backIfEmpty(bool empty) {
    if (!empty || offset == 0) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) setState(() => offset -= adminPageSize);
    });
  }

  Future<void> runBusy(int id, Future<void> Function() action) async {
    setState(() => busy.add(id));
    await action();
    if (mounted) setState(() => busy.remove(id));
  }
}

class _WorksTab extends ConsumerStatefulWidget {
  const _WorksTab();

  @override
  ConsumerState<_WorksTab> createState() => _WorksTabState();
}

class _WorksTabState extends ConsumerState<_WorksTab>
    with _PagedTab<_WorksTab> {
  final Set<int> selected = {};
  bool batchBusy = false;

  /// 有作品在等 AI 分析时定时刷新列表，建议出来就显示
  Timer? _poll;
  static const _pollEvery = Duration(seconds: 8);

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }

  void _syncPolling(bool needed) {
    if (needed && _poll == null) {
      _poll = Timer.periodic(_pollEvery, (_) {
        if (!mounted) return;
        ref
          ..invalidate(unmatchedWorksProvider)
          ..invalidate(pendingSummaryProvider);
      });
    } else if (!needed && _poll != null) {
      _poll!.cancel();
      _poll = null;
    }
  }

  Future<void> _adopt(PendingWork work) async {
    final suggestion = work.suggestion;
    if (suggestion == null) return;
    await runBusy(
      work.id,
      () => _assign(
        context,
        ref,
        MetadataPick(
          providerId: suggestion.providerId,
          remoteId: suggestion.remoteId ?? '',
          title: suggestion.title ?? '',
        ),
        () => adoptSuggestion(ref.read(apiClientProvider), work.id),
      ),
    );
  }

  Future<void> _suggestionAction(
    PendingWork work,
    Future<void> Function() call,
    String done,
  ) => runBusy(work.id, () async {
    try {
      await call();
      ref.invalidate(unmatchedWorksProvider);
      if (mounted) showSnack(context, done);
    } catch (e) {
      if (mounted) showApiErrorSnack(context, e);
    }
  });

  Future<void> _adoptBatch(List<PendingWork> works) async {
    if (works.isEmpty) return;
    setState(() => batchBusy = true);
    try {
      final report = await adoptSuggestionsBatch(
        ref.read(apiClientProvider),
        works.map((work) => work.id).toList(),
      );
      _invalidatePending(ref);
      if (mounted) {
        setState(() {
          batchBusy = false;
          selected
            ..clear()
            ..addAll(
              report.results
                  .where((result) => !result.succeeded)
                  .map((result) => result.id),
            );
        });
        _showBatchReport(context, report, action: '采用 AI 建议');
      }
    } catch (e) {
      if (mounted) {
        setState(() => batchBusy = false);
        showApiErrorSnack(context, e);
      }
    }
  }

  Future<void> _pick(PendingWork work) async {
    final pick = await pickMetadata(
      context,
      title: '指定「${work.title ?? work.path}」',
      kind: work.kind,
      libraryId: work.libraryId,
      initialQuery: work.title ?? '',
      suggestions: work.candidates,
    );
    if (pick == null || !mounted) return;
    await runBusy(
      work.id,
      () => _assign(
        context,
        ref,
        pick,
        () => assignWork(
          ref.read(apiClientProvider),
          work.id,
          pick.remoteId,
          providerId: pick.providerId,
        ),
      ),
    );
  }

  Future<void> _pickBatch(List<PendingWork> works) async {
    if (works.isEmpty) return;
    final first = works.first;
    final pick = await pickMetadata(
      context,
      title: '批量指定 ${works.length} 个作品',
      kind: first.kind,
      libraryId: first.libraryId,
      initialQuery: first.title ?? '',
      suggestions: first.candidates,
    );
    if (pick == null || !mounted) return;
    setState(() => batchBusy = true);
    try {
      final report = await assignWorksBatch(
        ref.read(apiClientProvider),
        works.map((work) => work.id).toList(),
        pick.remoteId,
        providerId: pick.providerId,
      );
      _invalidatePending(ref);
      if (mounted) {
        setState(() {
          batchBusy = false;
          selected
            ..clear()
            ..addAll(
              report.results
                  .where((result) => !result.succeeded)
                  .map((result) => result.id),
            );
        });
        _showBatchReport(context, report, action: '批量指定');
      }
    } catch (e) {
      if (mounted) {
        setState(() => batchBusy = false);
        showApiErrorSnack(context, e);
      }
    }
  }

  Future<void> _delete(PendingWork work, String title) async {
    final confirmed = await _confirmDelete(
      context,
      '删除「$title」？',
      '会删除来源上「${work.path}」所在的目录，视频、字幕等文件一起删除，操作不可恢复。',
    );
    if (!confirmed || !mounted) return;
    await runBusy(work.id, () => _deletePending(context, workIds: [work.id]));
  }

  Future<void> _deleteBatch(List<int> ids) async {
    if (ids.isEmpty) return;
    final confirmed = await _confirmDelete(
      context,
      '删除 ${ids.length} 个作品？',
      '会删除这些作品在来源上的目录或视频，目录里的字幕等文件一起删除，操作不可恢复。',
    );
    if (!confirmed || !mounted) return;
    setState(() => batchBusy = true);
    await _deletePending(context, workIds: ids);
    if (mounted) {
      setState(() {
        batchBusy = false;
        selected.removeAll(ids);
      });
    }
  }

  Future<void> _rematchBatch(List<PendingWork> works) async {
    if (works.isEmpty) return;
    setState(() => batchBusy = true);
    try {
      final report = await rematchWorksBatch(
        ref.read(apiClientProvider),
        works.map((work) => work.id).toList(),
      );
      _invalidatePending(ref);
      if (mounted) {
        setState(() {
          batchBusy = false;
          selected
            ..clear()
            ..addAll(
              report.results
                  .where((result) => !result.succeeded)
                  .map((result) => result.id),
            );
        });
        _showBatchReport(context, report, action: '批量重新匹配');
      }
    } catch (e) {
      if (mounted) {
        setState(() => batchBusy = false);
        showApiErrorSnack(context, e);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final provider = unmatchedWorksProvider(offset);
    final analysis = ref.watch(aiAnalysisEnabledProvider).value ?? false;
    return AsyncView(
      value: ref.watch(provider),
      onRetry: () => ref.invalidate(provider),
      builder: (page) {
        backIfEmpty(page.items.isEmpty);
        final waiting = analysis && page.items.any((w) => w.suggestion == null);
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (mounted) _syncPolling(waiting);
        });
        if (page.total == 0) {
          return const EmptyState(
            icon: Icons.check_circle_outline,
            title: '没有未匹配的作品',
            message: '同步时没能自动匹配的作品会列在这里，等你手动指定。',
          );
        }
        final pageIds = page.items.map((work) => work.id).toSet();
        final allSelected =
            pageIds.isNotEmpty && pageIds.every(selected.contains);
        return Column(
          children: [
            if (selected.isNotEmpty)
              _BatchToolbar(
                count: selected.length,
                allSelected: allSelected,
                busy: batchBusy,
                onSelectAll: () => setState(() {
                  if (allSelected) {
                    selected.removeAll(pageIds);
                  } else {
                    selected.addAll(pageIds);
                  }
                }),
                onClear: () => setState(selected.clear),
                onRematch: () => _rematchBatch(
                  page.items
                      .where((work) => selected.contains(work.id))
                      .toList(),
                ),
                onAssign: () => _pickBatch(
                  page.items
                      .where((work) => selected.contains(work.id))
                      .toList(),
                ),
                onDelete: () =>
                    _deleteBatch(pageIds.where(selected.contains).toList()),
                onSecondary:
                    page.items.any(
                      (w) =>
                          selected.contains(w.id) &&
                          w.suggestion?.adoptable == true,
                    )
                    ? () => _adoptBatch(
                        page.items
                            .where(
                              (w) =>
                                  selected.contains(w.id) &&
                                  w.suggestion?.adoptable == true,
                            )
                            .toList(),
                      )
                    : null,
                secondaryLabel: '采用 AI 建议',
              ),
            Expanded(
              child: ListView(
                padding: const EdgeInsets.only(bottom: 24),
                children: [
                  for (final work in page.items)
                    _WorkTile(
                      work: work,
                      busy: busy.contains(work.id),
                      selected: selected.contains(work.id),
                      onSelected: (value) => setState(() {
                        value
                            ? selected.add(work.id)
                            : selected.remove(work.id);
                      }),
                      onPick: () => _pick(work),
                      onDelete: (title) => _delete(work, title),
                      analyzing: analysis,
                      onAdopt: () => _adopt(work),
                      onIgnore: () => _suggestionAction(
                        work,
                        () => ignoreSuggestion(
                          ref.read(apiClientProvider),
                          work.id,
                        ),
                        '已忽略这条 AI 建议',
                      ),
                      onReanalyze: () => _suggestionAction(
                        work,
                        () => reanalyzeSuggestion(
                          ref.read(apiClientProvider),
                          work.id,
                        ),
                        '已重新排队分析',
                      ),
                    ),
                  Pager(
                    offset: offset,
                    total: page.total,
                    onChanged: (value) => setState(() {
                      selected.clear();
                      offset = value;
                    }),
                  ),
                ],
              ),
            ),
          ],
        );
      },
    );
  }
}
