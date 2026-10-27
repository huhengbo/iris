import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/providers.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/focus_ring.dart';
import 'catalog_api.dart';
import 'libraries_api.dart' show libraryKindLabel;

/// 选中的元数据条目；剧集的单个文件还带季号和集号。
/// [providerId] 为空时（直接输入的 ID）由服务端按资料库选择的插件解释。
class MetadataPick {
  const MetadataPick({
    this.providerId,
    required this.remoteId,
    required this.title,
    this.season,
    this.episode,
  });

  final String? providerId;
  final String remoteId;
  final String title;
  final int? season;
  final int? episode;
}

/// 用资料库选择的元数据插件搜索并选择一个条目。[suggestions] 在搜索前列出（匹配候选、
/// 所在作品已匹配的条目）；[needEpisode] 为 true 时选中后再填写季号和集号。取消时返回 null。
Future<MetadataPick?> pickMetadata(
  BuildContext context, {
  required String title,
  required String kind,
  required int libraryId,
  String initialQuery = '',
  List<MetadataCandidate> suggestions = const [],
  bool needEpisode = false,
}) => showDialog<MetadataPick>(
  context: context,
  builder: (_) => MetadataPickerDialog(
    title: title,
    kind: kind,
    libraryId: libraryId,
    initialQuery: initialQuery,
    suggestions: suggestions,
    needEpisode: needEpisode,
  ),
);

class MetadataPickerDialog extends ConsumerStatefulWidget {
  const MetadataPickerDialog({
    super.key,
    required this.title,
    required this.kind,
    required this.libraryId,
    this.initialQuery = '',
    this.suggestions = const [],
    this.needEpisode = false,
  });

  final String title;
  final String kind;
  final int libraryId;
  final String initialQuery;
  final List<MetadataCandidate> suggestions;
  final bool needEpisode;

  @override
  ConsumerState<MetadataPickerDialog> createState() =>
      _MetadataPickerDialogState();
}

class _MetadataPickerDialogState extends ConsumerState<MetadataPickerDialog> {
  late final _query = TextEditingController(text: widget.initialQuery);
  final _season = TextEditingController(text: '1');
  final _episode = TextEditingController();

  /// null 表示还没搜索，显示 suggestions
  List<MetadataCandidate>? _results;
  bool _searching = false;
  String? _error;

  /// 剧集文件：已选中条目，等待填写季号和集号
  MetadataCandidate? _chosen;
  String? _episodeError;

  @override
  void dispose() {
    _query.dispose();
    _season.dispose();
    _episode.dispose();
    super.dispose();
  }

  Future<void> _search() async {
    final query = _query.text.trim();
    if (query.isEmpty || _searching) return;
    setState(() {
      _searching = true;
      _error = null;
    });
    try {
      final results = await searchMetadata(
        ref.read(apiClientProvider),
        widget.kind,
        query,
        libraryId: widget.libraryId,
      );
      if (mounted) setState(() => _results = results);
    } catch (e) {
      if (mounted) setState(() => _error = ApiError.from(e).message);
    }
    if (mounted) setState(() => _searching = false);
  }

  void _choose(MetadataCandidate candidate) {
    if (!widget.needEpisode) {
      Navigator.of(context).pop(
        MetadataPick(
          providerId: candidate.providerId,
          remoteId: candidate.remoteId,
          title: candidate.label,
        ),
      );
      return;
    }
    setState(() => _chosen = candidate);
  }

  void _confirmEpisode() {
    final season = int.tryParse(_season.text.trim());
    final episode = int.tryParse(_episode.text.trim());
    if (season == null || season < 0 || episode == null || episode < 1) {
      setState(() => _episodeError = '季号从 0 开始（0 是特别篇），集号从 1 开始');
      return;
    }
    final chosen = _chosen!;
    Navigator.of(context).pop(
      MetadataPick(
        providerId: chosen.providerId,
        remoteId: chosen.remoteId,
        title: chosen.label,
        season: season,
        episode: episode,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final chosen = _chosen;
    return AlertDialog(
      // 输入法占去半个屏幕时（TV、横屏）标题和输入框一起滚动，光标所在处保持可见
      scrollable: true,
      title: Text(widget.title),
      content: SizedBox(
        width: 520,
        child: chosen == null ? _searchView(context) : _episodeView(chosen),
      ),
      actions: [
        TextButton(
          onPressed: () => chosen == null
              ? Navigator.of(context).pop()
              : setState(() => _chosen = null),
          child: Text(chosen == null ? '取消' : '返回'),
        ),
        if (chosen != null)
          FilledButton(onPressed: _confirmEpisode, child: const Text('指定')),
      ],
    );
  }

  Widget _searchView(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final muted = text.bodyMedium?.copyWith(color: CinemaColors.text2);
    final query = _query.text.trim();
    // 纯数字按条目 ID 处理（TMDB 等插件的 ID 都是数字）
    final byId = RegExp(r'^\d+$').hasMatch(query) ? query : null;
    final results = _results ?? widget.suggestions;
    Widget body;
    if (_searching) {
      body = const Center(child: CircularProgressIndicator());
    } else if (_error != null) {
      body = ErrorRetry(message: _error!, onRetry: _search);
    } else if (results.isEmpty && byId == null) {
      body = Center(
        child: Text(
          _results == null ? '输入片名搜索，或直接输入条目 ID' : '没有找到，换个片名或直接输入条目 ID',
          style: muted,
          textAlign: TextAlign.center,
        ),
      );
    } else {
      body = ListView(
        children: [
          if (_results == null && results.isNotEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(
                CinemaSpacing.gutter,
                4,
                CinemaSpacing.gutter,
                4,
              ),
              child: Text('候选', style: text.labelMedium),
            ),
          if (byId != null)
            FocusRing(
              child: ListTile(
                leading: const Icon(Icons.tag),
                title: Text('使用条目 ID $byId'),
                onTap: () => _choose(
                  MetadataCandidate(remoteId: byId, title: '条目 ID $byId'),
                ),
              ),
            ),
          for (final candidate in results)
            FocusRing(
              child: ListTile(
                title: Text(candidate.label),
                trailing: candidate.score == null
                    ? null
                    : Text('匹配度 ${candidate.score}', style: text.labelMedium),
                subtitle: Text(
                  [
                    if (candidate.originalTitle != null &&
                        candidate.originalTitle != candidate.title)
                      candidate.originalTitle!,
                    'ID ${candidate.remoteId}',
                    if (candidate.reasons.isNotEmpty)
                      candidate.reasons.join('，'),
                    if (candidate.overview?.isNotEmpty ?? false)
                      candidate.overview!,
                  ].join(' · '),
                  maxLines: 3,
                  overflow: TextOverflow.ellipsis,
                ),
                onTap: () => _choose(candidate),
              ),
            ),
        ],
      );
    }
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        TextField(
          controller: _query,
          autofocus: true,
          maxLength: 100,
          textInputAction: TextInputAction.search,
          decoration: InputDecoration(
            labelText: '${libraryKindLabel(widget.kind)}名称或条目 ID',
            counterText: '',
            suffixIcon: IconButton(
              tooltip: '搜索',
              onPressed: _searching ? null : _search,
              icon: const Icon(Icons.search),
            ),
          ),
          onChanged: (_) => setState(() {}),
          onSubmitted: (_) => _search(),
        ),
        const SizedBox(height: 12),
        SizedBox(height: 320, child: body),
      ],
    );
  }

  Widget _episodeView(MetadataCandidate chosen) {
    final text = Theme.of(context).textTheme;
    final digits = [FilteringTextInputFormatter.digitsOnly];
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(chosen.label, style: text.titleMedium),
        const SizedBox(height: 16),
        Row(
          children: [
            Expanded(
              child: TextField(
                controller: _season,
                keyboardType: TextInputType.number,
                inputFormatters: digits,
                decoration: const InputDecoration(labelText: '季号'),
              ),
            ),
            const SizedBox(width: 12),
            Expanded(
              child: TextField(
                controller: _episode,
                autofocus: true,
                keyboardType: TextInputType.number,
                inputFormatters: digits,
                decoration: const InputDecoration(labelText: '集号'),
                onSubmitted: (_) => _confirmEpisode(),
              ),
            ),
          ],
        ),
        if (_episodeError != null) ...[
          const SizedBox(height: 8),
          Text(
            _episodeError!,
            style: text.bodySmall?.copyWith(color: CinemaColors.error),
          ),
        ],
      ],
    );
  }
}
