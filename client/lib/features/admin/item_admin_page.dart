import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/providers.dart';
import '../../core/format.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/section_title.dart';
import '../../core/widgets/snack.dart';
import '../library/art_image.dart';
import 'admin_widgets.dart';
import 'catalog_api.dart';
import 'item_actions.dart';
import 'libraries_api.dart' show libraryKindLabel;
import 'metadata_picker.dart';

part 'item_admin_dialogs.dart';
part 'item_admin_sections.dart';

/// 管理后台的单个条目：查看匹配到的元数据、作品目录和各个版本，
/// 刷新信息、重新匹配、重新读取文件，以及把版本拆出或改挂到别的条目。
class ItemAdminPage extends ConsumerStatefulWidget {
  const ItemAdminPage({super.key, required this.id});

  final int id;

  @override
  ConsumerState<ItemAdminPage> createState() => _ItemAdminPageState();
}

class _ItemAdminPageState extends ConsumerState<ItemAdminPage> {
  /// 正在进行的操作名称；进行中时其他操作不可用
  String? _busy;

  AdminItemDetail? get _detail => ref.read(adminItemProvider(widget.id)).value;

  /// 执行一个操作：成功后刷新本页和相关列表，条目已不存在时退回上一页。返回是否成功。
  Future<bool> _run(
    String name,
    Future<String?> Function() action, {
    bool reload = true,
  }) async {
    if (_busy != null) return false;
    setState(() => _busy = name);
    var ok = false;
    try {
      final message = await action();
      ref.invalidate(adminItemsProvider);
      ref.invalidate(pendingSummaryProvider);
      ref.invalidate(unmatchedWorksProvider);
      ref.invalidate(pendingFilesProvider);
      if (reload) {
        final _ = await ref.refresh(adminItemProvider(widget.id).future);
      }
      if (message != null && mounted) showSnack(context, message);
      ok = true;
    } catch (e) {
      final error = ApiError.from(e);
      if (error.code == 'not_found' && mounted) {
        showSnack(context, '条目已不存在');
        _leave();
      } else if (mounted) {
        showSnack(context, error.message);
      }
    }
    if (mounted) setState(() => _busy = null);
    return ok;
  }

  void _leave() {
    final router = GoRouter.maybeOf(context);
    if (router != null && router.canPop()) {
      router.pop();
    } else if (router != null) {
      router.go('/settings/manage/items');
    }
  }

  Future<bool> _confirm(String title, String body, String action) =>
      showConfirmDialog(context, title: title, body: body, action: action);

  Future<void> _refresh() => _run(ItemAction.refresh.label, () async {
    await refreshItem(ref.read(apiClientProvider), widget.id);
    ref.invalidate(itemMetadataProvider(widget.id));
    return refreshedMessage;
  });

  Future<void> _reenrich() => _run(ItemAction.reenrich.label, () async {
    final result = await reenrichItem(ref.read(apiClientProvider), widget.id);
    return reenrichMessage(result);
  });

  Future<void> _syncDirs() => _run(ItemAction.sync.label, () async {
    await syncItemDirs(ref.read(apiClientProvider), widget.id);
    return syncStartedMessage;
  }, reload: false);

  Future<void> _editMetadata(MetadataField metadata) async {
    final result = await showDialog<({String value, bool locked})>(
      context: context,
      builder: (context) => _MetadataEditDialog(metadata: metadata),
    );
    if (result == null || !mounted) return;
    await _run('保存元数据', () async {
      await updateItemMetadata(
        ref.read(apiClientProvider),
        widget.id,
        metadata.field,
        value: result.value.isEmpty ? null : result.value,
        locked: result.locked,
      );
      ref.invalidate(itemMetadataProvider(widget.id));
      return result.locked ? '${metadata.label}已保存并锁定' : '${metadata.label}已保存';
    });
  }

  Future<void> _unlockMetadata(MetadataField metadata) =>
      _run('解锁元数据', () async {
        await unlockItemMetadata(
          ref.read(apiClientProvider),
          widget.id,
          metadata.field,
        );
        ref.invalidate(itemMetadataProvider(widget.id));
        return '${metadata.label}已解锁，下次刷新会自动更新';
      });

  Future<void> _rematch(_Rematch mode) async {
    final detail = _detail;
    if (detail == null) return;
    MetadataPick? pick;
    if (mode == _Rematch.pick) {
      pick = await pickMetadata(
        context,
        title: '改为其他条目',
        kind: detail.item.kind,
        libraryId: detail.item.libraryId,
        initialQuery: detail.item.title,
      );
      if (pick == null) return;
    } else if (!await _confirm(
      '重新自动匹配？',
      '清除「${detail.item.title}」的匹配结果，按目录名重新搜索资料库选用的元数据插件。'
          '手动指定的结果也会被清除；没匹配上的作品会进入待处理。',
      '重新匹配',
    )) {
      return;
    }
    if (!mounted) return;
    int? next;
    final ok = await _run(ItemAction.rematch.label, reload: false, () async {
      next = await rematchItem(
        ref.read(apiClientProvider),
        widget.id,
        providerId: pick?.providerId,
        remoteId: pick?.remoteId,
      );
      return next == null
          ? '没有自动匹配上，作品已进入待处理'
          : pick == null
          ? '已重新匹配'
          : '已改为「${pick.title}」';
    });
    if (!ok || !mounted) return;
    final router = GoRouter.maybeOf(context);
    if (next == null) {
      _leave();
    } else if (next == widget.id) {
      ref.invalidate(adminItemProvider(widget.id));
    } else {
      router?.replace('/settings/manage/items/$next');
    }
  }

  Future<void> _reassign(ItemVersion version) async {
    final detail = _detail;
    if (detail == null) return;
    final item = detail.item;
    final pick = await pickMetadata(
      context,
      title: '改挂「${version.name}」',
      kind: item.kind,
      libraryId: item.libraryId,
      initialQuery: item.title,
      suggestions: [
        MetadataCandidate(
          providerId: item.providerId,
          remoteId: item.remoteId,
          title: item.title,
        ),
      ],
      needEpisode: item.kind == 'series',
    );
    if (pick == null || !mounted) return;
    await _run('改挂', () async {
      await assignFile(
        ref.read(apiClientProvider),
        version.mediaFileId,
        pick.remoteId,
        providerId: pick.providerId,
        season: pick.season,
        episode: pick.episode,
      );
      return '已改挂到「${pick.title}」';
    });
  }

  Future<void> _detach(ItemVersion version) async {
    if (!await _confirm(
      '拆出这个版本？',
      '「${version.name}」会从条目中移出，进入待处理的「未归入」，之后同步不会自动放回。',
      '拆出',
    )) {
      return;
    }
    if (!mounted) return;
    await _run('拆出', () async {
      await detachVersion(ref.read(apiClientProvider), version.id);
      return '已拆出，可在待处理中重新指定';
    });
  }

  @override
  Widget build(BuildContext context) {
    final provider = adminItemProvider(widget.id);
    final value = ref.watch(provider);
    final metadata = ref.watch(itemMetadataProvider(widget.id));
    return Scaffold(
      appBar: AppBar(
        title: Text(value.value?.item.title ?? '条目'),
        actions: [
          IconButton(
            tooltip: ItemAction.delete.label,
            icon: const Icon(Icons.delete_outline),
            onPressed: _busy == null && value.value != null
                ? () => _delete(value.value!.item.title)
                : null,
          ),
        ],
      ),
      body: AsyncView(
        value: value,
        onRetry: () => ref.invalidate(provider),
        builder: (detail) => ListView(
          padding: const EdgeInsets.fromLTRB(
            CinemaSpacing.gutter,
            8,
            CinemaSpacing.gutter,
            32,
          ),
          children: [
            Align(
              alignment: Alignment.topLeft,
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: adminContentWidth),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    _Header(detail: detail),
                    const SizedBox(height: 16),
                    _Actions(
                      busy: _busy,
                      onRefresh: _refresh,
                      onRematch: _rematch,
                      onReenrich: _reenrich,
                      onSyncDirs: _syncDirs,
                    ),
                    if (metadata.hasValue)
                      _MetadataSection(
                        fields: metadata.value!,
                        enabled: _busy == null,
                        onEdit: _editMetadata,
                        onUnlock: _unlockMetadata,
                      ),
                    _ArtworkSection(
                      enabled: _busy == null,
                      onSelect: _selectArtwork,
                    ),
                    if (metadata.isLoading) const LinearProgressIndicator(),
                    if (detail.overview?.isNotEmpty ?? false) ...[
                      const SizedBox(height: CinemaSpacing.heading),
                      Text(detail.overview!),
                    ],
                    if (detail.works.isNotEmpty) ...[
                      const SectionTitle('作品目录', inset: false),
                      for (final work in detail.works) _WorkRow(work: work),
                    ],
                    SectionTitle('版本 ${detail.versions.length}', inset: false),
                    for (final version in detail.versions)
                      _VersionRow(
                        version: version,
                        enabled: _busy == null,
                        onReassign: () => _reassign(version),
                        onDetach: () => _detach(version),
                      ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _delete(String title) async {
    if (_busy != null) return;
    if (!await confirmDeleteItem(context, title) || !mounted) return;
    setState(() => _busy = ItemAction.delete.label);
    final dio = ref.read(apiClientProvider);
    final deleted = await runDeleteTask(
      context,
      dio,
      () => deleteItem(dio, widget.id),
    );
    if (!mounted) return;
    setState(() => _busy = null);
    if (!deleted) return;
    ref
      ..invalidate(adminItemsProvider)
      ..invalidate(pendingSummaryProvider)
      ..invalidate(unmatchedWorksProvider)
      ..invalidate(pendingFilesProvider);
    _leave();
  }

  Future<void> _selectArtwork(String kind, String label) async {
    final dio = ref.read(apiClientProvider);
    try {
      final candidates = await artworkCandidates(dio, widget.id, kind);
      if (!mounted) return;
      final selection = await showDialog<({String source, String value})>(
        context: context,
        builder: (context) =>
            _ArtworkDialog(label: label, candidates: candidates),
      );
      if (selection == null || !mounted) return;
      await _run('保存$label', () async {
        await selectArtwork(
          dio,
          widget.id,
          kind,
          source: selection.source,
          value: selection.value,
        );
        return '$label已更新并锁定';
      });
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    }
  }
}

enum _Rematch { auto, pick }
