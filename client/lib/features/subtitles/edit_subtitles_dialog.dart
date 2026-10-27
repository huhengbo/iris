import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/format.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/dropdown_field.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/picker_sheet.dart';
import '../../core/widgets/snack.dart';
import '../library/browse_api.dart';
import 'subtitle_search_sheet.dart';
import 'subtitles_api.dart';

/// 管理员的 Emby 式「编辑字幕」：按版本列出外挂 / 在线 / 内封字幕，
/// 可以删除和搜索下载。[onChanged] 在新增或删除后调用，让详情页刷新。
Future<void> showEditSubtitlesDialog(
  BuildContext context, {
  required String title,
  required List<MediaVersion> versions,
  int? initialVersionId,
  VoidCallback? onChanged,
}) => showDialog<void>(
  context: context,
  builder: (_) => EditSubtitlesDialog(
    title: title,
    versions: versions,
    initialVersionId: initialVersionId,
    onChanged: onChanged,
  ),
);

class EditSubtitlesDialog extends ConsumerStatefulWidget {
  const EditSubtitlesDialog({
    super.key,
    required this.title,
    required this.versions,
    this.initialVersionId,
    this.onChanged,
  });

  final String title;
  final List<MediaVersion> versions;
  final int? initialVersionId;
  final VoidCallback? onChanged;

  @override
  ConsumerState<EditSubtitlesDialog> createState() =>
      _EditSubtitlesDialogState();
}

class _EditSubtitlesDialogState extends ConsumerState<EditSubtitlesDialog> {
  late int _versionId;
  AdminVersionSubtitles? _data;
  bool _loading = true;
  Object? _error;
  int? _deleting;
  int _loadSeq = 0;

  /// 在对话框内嵌显示搜索面板，不另外弹一层
  bool _searchOpen = false;

  bool get _hasVersions => widget.versions.isNotEmpty;

  @override
  void initState() {
    super.initState();
    _versionId =
        widget.initialVersionId ?? widget.versions.firstOrNull?.id ?? 0;
    if (_hasVersions) _load();
  }

  Future<void> _load() async {
    if (!_hasVersions) return;
    final seq = ++_loadSeq;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final data = await ref.read(subtitlesApiProvider).adminList(_versionId);
      if (!mounted || seq != _loadSeq) return;
      setState(() {
        _data = data;
        _loading = false;
      });
    } catch (error) {
      if (!mounted || seq != _loadSeq) return;
      setState(() {
        _error = error;
        _loading = false;
      });
    }
  }

  void _selectVersion(int versionId) {
    if (versionId == _versionId) return;
    setState(() {
      _versionId = versionId;
      _data = null;
      _searchOpen = false;
    });
    _load();
  }

  void _onDownloaded(SubtitleDownloadResult result) {
    if (!mounted) return;
    setState(() => _searchOpen = false);
    showSnack(
      context,
      result.created
          ? '已下载「${result.subtitle.label}」'
          : '这个版本已有「${result.subtitle.label}」',
    );
    widget.onChanged?.call();
    unawaited(_load());
  }

  Future<void> _delete(AdminSubtitle subtitle) async {
    final online = subtitle.isOnline;
    final confirmed = await showConfirmDialog(
      context,
      title: online ? '删除这条在线字幕？' : '删除「${subtitle.label}」？',
      body: online ? '这条字幕会从服务器删除，不影响来源上的文件。' : '会从来源上删除这个字幕文件，操作不可恢复。',
      action: '删除',
      destructive: true,
    );
    if (!confirmed || !mounted) return;
    setState(() => _deleting = subtitle.id);
    try {
      await ref.read(subtitlesApiProvider).delete(subtitle.id);
      if (!mounted) return;
      showSnack(context, '已删除「${subtitle.label}」');
      widget.onChanged?.call();
      await _load();
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    } finally {
      if (mounted) setState(() => _deleting = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final providers = ref
        .watch(subtitleProvidersProvider(_hasVersions ? _versionId : null))
        .value;
    final canSearch = _hasVersions && (providers?.usable ?? false);
    return Dialog(
      insetPadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 24),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 680, maxHeight: 760),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 16, 16, 12),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              PickerHeader(title: '编辑字幕', subtitle: widget.title),
              if (widget.versions.length > 1) ...[
                _VersionField(
                  versions: widget.versions,
                  value: _versionId,
                  enabled: !_loading,
                  onChanged: _selectVersion,
                ),
                const SizedBox(height: 12),
              ],
              Row(
                children: [
                  Expanded(
                    child: Text(
                      _data?.version.filename ??
                          _data?.version.label ??
                          widget.versions.firstOrNull?.label ??
                          '',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodySmall,
                    ),
                  ),
                  const SizedBox(width: 8),
                  IconButton(
                    tooltip: '刷新',
                    onPressed: _loading ? null : _load,
                    icon: const Icon(Icons.refresh),
                  ),
                  const SizedBox(width: 4),
                  FilledButton.tonalIcon(
                    onPressed: canSearch && !_loading && !_searchOpen
                        ? () => setState(() => _searchOpen = true)
                        : null,
                    icon: const Icon(Icons.travel_explore),
                    label: const Text('搜索字幕'),
                  ),
                ],
              ),
              if (!_loading && !canSearch && !_searchOpen)
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Text(
                    '没有可用的字幕下载器。请到「插件 › 射手网 assrt」填写 API Token。',
                    style: Theme.of(context).textTheme.bodySmall
                        ?.copyWith(color: CinemaColors.text2),
                  ),
                ),
              Flexible(
                child: _searchOpen
                    ? SubtitleSearchPanel(
                        versionId: _versionId,
                        title:
                            _data?.version.filename ??
                            _data?.version.label ??
                            widget.title,
                        onClose: () => setState(() => _searchOpen = false),
                        onDownloaded: _onDownloaded,
                      )
                    : _body(),
              ),
              const SizedBox(height: 8),
              Align(
                alignment: Alignment.centerRight,
                child: TextButton(
                  onPressed: () => Navigator.of(context).pop(),
                  child: const Text('关闭'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _body() {
    if (!_hasVersions) {
      return const SizedBox(
        height: 220,
        child: EmptyState(
          icon: Icons.subtitles_off_outlined,
          title: '没有可管理的字幕',
          message: '这个条目还没有可播放的文件。',
        ),
      );
    }
    if (_loading) {
      return const SizedBox(
        height: 260,
        child: Center(
          child: CircularProgressIndicator(color: CinemaColors.amber),
        ),
      );
    }
    if (_error case final error?) {
      return ErrorRetry(message: ApiError.from(error).message, onRetry: _load);
    }
    final data = _data;
    if (data == null) return const SizedBox.shrink();
    return SingleChildScrollView(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _Section(
            title: '外挂字幕',
            count: data.external.length,
            empty: '没有外挂字幕',
            children: [
              for (final subtitle in data.external)
                _SubtitleRow(
                  subtitle: subtitle,
                  deleting: _deleting == subtitle.id,
                  onDelete: () => _delete(subtitle),
                ),
            ],
          ),
          _Section(
            title: '在线字幕',
            count: data.online.length,
            empty: '没有在线字幕',
            children: [
              for (final subtitle in data.online)
                _SubtitleRow(
                  subtitle: subtitle,
                  deleting: _deleting == subtitle.id,
                  onDelete: () => _delete(subtitle),
                ),
            ],
          ),
          _Section(
            title: '内封字幕',
            count: data.embedded?.length,
            children: switch (data.embedded) {
              null => const [_Hint('内置播放器播过一次后显示。')],
              [] => const [_Hint('没有内封字幕')],
              final embedded => [
                for (final subtitle in embedded)
                  _EmbeddedRow(subtitle: subtitle),
              ],
            },
          ),
          const SizedBox(height: 8),
          Text(
            '删除外挂字幕会删除来源上的文件；在线字幕只从服务器删除。',
            style: Theme.of(context).textTheme.bodySmall
                ?.copyWith(color: CinemaColors.text2),
          ),
        ],
      ),
    );
  }
}

class _VersionField extends StatelessWidget {
  const _VersionField({
    required this.versions,
    required this.value,
    required this.enabled,
    required this.onChanged,
  });

  final List<MediaVersion> versions;
  final int value;
  final bool enabled;
  final ValueChanged<int> onChanged;

  @override
  Widget build(BuildContext context) {
    return AppDropdownField<int>(
      label: '版本',
      value: value,
      items: [
        for (final version in versions)
          DropdownMenuItem(value: version.id, child: Text(version.label)),
      ],
      onChanged: enabled
          ? (value) {
              if (value != null) onChanged(value);
            }
          : null,
    );
  }
}

class _Section extends StatelessWidget {
  const _Section({
    required this.title,
    required this.children,
    this.count,
    this.empty,
  });

  final String title;
  final List<Widget> children;
  final int? count;
  final String? empty;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    return Padding(
      padding: const EdgeInsets.only(top: 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Text(
                title,
                style: textTheme.titleSmall?.copyWith(
                  color: CinemaColors.text0,
                ),
              ),
              if (count case final count?) ...[
                const SizedBox(width: 6),
                Text('$count', style: textTheme.bodySmall),
              ],
            ],
          ),
          const SizedBox(height: 4),
          if (children.isEmpty && empty != null)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 6),
              child: Text(empty!, style: textTheme.bodySmall),
            )
          else
            ...children,
        ],
      ),
    );
  }
}

class _Hint extends StatelessWidget {
  const _Hint(this.text);

  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Text(text, style: Theme.of(context).textTheme.bodySmall),
    );
  }
}

class _SubtitleRow extends StatelessWidget {
  const _SubtitleRow({
    required this.subtitle,
    required this.deleting,
    required this.onDelete,
  });

  final AdminSubtitle subtitle;
  final bool deleting;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    final notes = _notes(subtitle);
    final external = subtitle.external;
    final online = subtitle.online;
    final blocked = !subtitle.isOnline && !(external?.deletable ?? false);
    final blockReason = external?.deleteBlockedReason;
    final secondary = subtitle.isOnline
        ? [
            if (online?.providerName case final name? when name.isNotEmpty)
              name,
            if (online?.release case final release? when release.isNotEmpty)
              release,
            if (online?.size case final size?) formatSize(size),
            if (online?.createdAt case final createdAt?)
              '创建于 ${formatDate(createdAt)}',
            if (online?.createdBy case final user?) 'by ${user.username}',
          ].join(' · ')
        : external?.relPath ?? '';
    return FocusRing(
      child: ListTile(
        minTileHeight: 60,
        contentPadding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
        leading: Container(
          width: 36,
          height: 36,
          decoration: BoxDecoration(
            color: CinemaColors.surface3,
            borderRadius: BorderRadius.circular(CinemaRadius.small),
          ),
          child: Icon(
            subtitle.isOnline
                ? Icons.cloud_download_outlined
                : Icons.insert_drive_file_outlined,
            size: 20,
            color: CinemaColors.text1,
          ),
        ),
        title: Text(
          subtitle.label,
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(
            color: CinemaColors.text0,
            fontWeight: FontWeight.w600,
          ),
        ),
        subtitle: Text(
          [
            if (notes.isNotEmpty) notes,
            if (secondary.isNotEmpty) secondary,
            if (blocked && blockReason != null && blockReason.isNotEmpty)
              blockReason,
          ].join('\n'),
          maxLines: 3,
          overflow: TextOverflow.ellipsis,
        ),
        trailing: deleting
            ? const SizedBox.square(
                dimension: 20,
                child: CircularProgressIndicator(strokeWidth: 2.4),
              )
            : IconButton(
                tooltip: blocked ? (blockReason ?? '不能删除') : '删除',
                onPressed: blocked ? null : onDelete,
                icon: const Icon(Icons.delete_outline),
              ),
      ),
    );
  }
}

class _EmbeddedRow extends StatelessWidget {
  const _EmbeddedRow({required this.subtitle});

  final AdminEmbeddedSubtitle subtitle;

  @override
  Widget build(BuildContext context) {
    final notes = [
      subtitleLanguageName(subtitle.language),
      if (subtitle.title case final title? when title.isNotEmpty) title,
      if (subtitle.codec case final codec? when codec.isNotEmpty)
        codec.toUpperCase(),
      if (subtitle.forced) '强制',
      if (subtitle.isDefault) '默认',
    ].join(' · ');
    return FocusRing(
      child: ListTile(
        enabled: false,
        minTileHeight: 54,
        contentPadding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
        leading: const Icon(
          Icons.closed_caption_outlined,
          color: CinemaColors.text2,
        ),
        title: Text(
          subtitle.title?.isNotEmpty == true
              ? subtitle.title!
              : subtitleLanguageName(subtitle.language),
          style: const TextStyle(color: CinemaColors.text1),
        ),
        subtitle: Text(notes, maxLines: 2, overflow: TextOverflow.ellipsis),
      ),
    );
  }
}

String _notes(AdminSubtitle subtitle) => [
  subtitleLanguageName(subtitle.language),
  if (subtitle.format case final format? when format.isNotEmpty)
    format.toUpperCase(),
  if (subtitle.forced) '强制',
  if (subtitle.hearingImpaired) '听障',
  if (subtitle.isDefault) '默认',
].join(' · ');
