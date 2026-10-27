import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/format.dart';
import '../../core/widgets/dropdown_field.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/notice.dart';
import '../../core/widgets/picker_sheet.dart';
import '../auth/auth_controller.dart';
import '../me/preferences_api.dart';
import 'subtitles_api.dart';

/// Plex 式的「搜索字幕」面板。默认语言取「我的 › 设置」里的偏好
/// （zh-CN / zh-TW 先换成 zh-Hans / zh-Hant），返回下载好的在线字幕；
/// 用户取消时返回 null。调用方在拿到结果后刷新详情或播放器字幕列表。
Future<SubtitleDownloadResult?> showSubtitleSearchSheet(
  BuildContext context, {
  required int versionId,
  String? title,
  String? initialLanguage,
}) => showDialog<SubtitleDownloadResult>(
  context: context,
  builder: (_) => Dialog(
    insetPadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 24),
    child: ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 560, maxHeight: 620),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 12),
        child: SubtitleSearchPanel(
          versionId: versionId,
          title: title,
          initialLanguage: initialLanguage,
        ),
      ),
    ),
  ),
);

enum _Gate { loading, ready, disallowed, noProvider, error }

/// 搜索面板本体。独立成公开组件，便于在管理员的「编辑字幕」里复用时保持
/// 同一套加载、失败、空状态和 D-pad 行为。
class SubtitleSearchPanel extends ConsumerStatefulWidget {
  const SubtitleSearchPanel({
    super.key,
    required this.versionId,
    this.title,
    this.initialLanguage,
    this.onDownloaded,
    this.onClose,
  });

  final int versionId;
  final String? title;
  final String? initialLanguage;

  /// 嵌在管理员的「编辑字幕」里时，用回调把结果交回去，不关闭路由。
  final ValueChanged<SubtitleDownloadResult>? onDownloaded;

  /// 嵌在别的对话框里时关闭自己；为空时关闭当前路由。
  final VoidCallback? onClose;

  @override
  ConsumerState<SubtitleSearchPanel> createState() =>
      _SubtitleSearchPanelState();
}

class _SubtitleSearchPanelState extends ConsumerState<SubtitleSearchPanel> {
  _Gate _gate = _Gate.loading;
  Object? _gateError;
  SubtitleProviders? _providers;
  String? _language;
  SubtitleSearchResults? _result;
  bool _searching = false;
  Object? _searchError;
  Object? _actionError;
  final _downloading = <String>{};

  @override
  void initState() {
    super.initState();
    unawaited(_bootstrap());
  }

  Future<void> _bootstrap() async {
    setState(() {
      _gate = _Gate.loading;
      _gateError = null;
      _result = null;
      _searchError = null;
      _actionError = null;
    });
    String? preferred;
    try {
      preferred = await ref.read(subtitleLanguageProvider.future);
    } on Object {
      // 偏好拿不到不影响搜索，用默认语言
    }
    if (!mounted) return;
    final language = _pickLanguage(preferred);
    setState(() => _language = language);
    try {
      final providers = await ref.read(
        subtitleProvidersProvider(widget.versionId).future,
      );
      if (!mounted) return;
      setState(() {
        _providers = providers;
        _gate = !providers.allowed
            ? _Gate.disallowed
            : providers.hasReady
            ? _Gate.ready
            : _Gate.noProvider;
      });
      if (providers.allowed && providers.hasReady) await _search(language);
    } catch (error) {
      if (!mounted) return;
      setState(() {
        _gate = _Gate.error;
        _gateError = error;
      });
    }
  }

  /// 合法语言码；偏好里认不出的值退回简体中文，避免下拉框值不在选项里。
  String _pickLanguage(String? preferred) {
    final initial = normalizeSubtitleLanguage(widget.initialLanguage);
    final value =
        initial ??
        normalizeSubtitleLanguage(preferred) ??
        subtitleLanguages.first;
    return subtitleLanguages.contains(value) ? value : subtitleLanguages.first;
  }

  String get _effectiveLanguage {
    final language = _language;
    return language != null && subtitleLanguages.contains(language)
        ? language
        : subtitleLanguages.first;
  }

  Future<void> _search(String language) async {
    if (_searching) return;
    setState(() {
      _searching = true;
      _searchError = null;
      _actionError = null;
    });
    try {
      final result = await ref
          .read(subtitlesApiProvider)
          .search(widget.versionId, language: language);
      if (mounted) setState(() => _result = result);
    } catch (error) {
      if (!mounted) return;
      if (ApiError.from(error).code == 'subtitle_download_not_allowed') {
        setState(() => _gate = _Gate.disallowed);
      } else {
        // 权限被拒时不再提供重试；其他错误保留结果区，由用户重试
        setState(() {
          _searchError = error;
          _result = null;
        });
      }
    } finally {
      if (mounted) setState(() => _searching = false);
    }
  }

  Future<void> _download(SubtitleCandidate candidate) async {
    if (_downloading.contains(candidate.key)) return;
    setState(() {
      _downloading.add(candidate.key);
      _actionError = null;
    });
    try {
      final result = await ref
          .read(subtitlesApiProvider)
          .download(
            widget.versionId,
            SubtitleDownloadRequest.fromCandidate(
              candidate,
              language: _effectiveLanguage,
            ),
          );
      if (!mounted) return;
      if (widget.onDownloaded case final onDownloaded?) {
        onDownloaded(result);
        return;
      }
      Navigator.of(context).pop(result);
    } catch (error) {
      if (mounted) {
        setState(() {
          _actionError = error;
          _downloading.remove(candidate.key);
        });
      }
    }
  }

  void _retryProviders() {
    ref.invalidate(subtitleProvidersProvider(widget.versionId));
    unawaited(_bootstrap());
  }

  @override
  Widget build(BuildContext context) {
    final admin = ref.watch(authControllerProvider).value?.isAdmin ?? false;
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        PickerHeader(
          title: '搜索字幕',
          subtitle: widget.title,
          onClose: widget.onClose,
        ),
        if (_gate == _Gate.ready)
          Padding(
            padding: const EdgeInsets.only(bottom: 8),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Expanded(
                  child: _LanguageField(
                    value: _effectiveLanguage,
                    enabled: _gate == _Gate.ready && !_searching,
                    onChanged: (value) {
                      setState(() => _language = value);
                      unawaited(_search(value));
                    },
                  ),
                ),
                const SizedBox(width: 12),
                Padding(
                  padding: const EdgeInsets.only(bottom: 4),
                  child: FilledButton.tonal(
                    onPressed: _gate == _Gate.ready && !_searching
                        ? () => unawaited(_search(_effectiveLanguage))
                        : null,
                    child: const Text('搜索'),
                  ),
                ),
              ],
            ),
          ),
        if (_actionError case final error?) _InlineError(error),
        Flexible(child: _content(admin)),
      ],
    );
  }

  Widget _content(bool admin) {
    switch (_gate) {
      case _Gate.loading:
        return const _Loading(label: '正在检查字幕下载器…');
      case _Gate.disallowed:
        return const _Message(
          icon: Icons.lock_outline,
          title: '不能下载在线字幕',
          body: '管理员关闭了「允许下载在线字幕」。需要的话请让管理员在用户管理里打开。',
        );
      case _Gate.noProvider:
        final reasons = [
          for (final provider in _providers?.providers ?? const [])
            if (provider.reason case final reason? when reason.isNotEmpty)
              '${provider.name}：$reason',
        ];
        return _Message(
          icon: Icons.extension_off_outlined,
          title: '没有可用的字幕下载器',
          body: [
            admin
                ? '请到「插件 › 射手网 assrt」填写 API Token。'
                : '请联系管理员在「插件 › 射手网 assrt」填写 API Token。',
            ...reasons,
          ].join('\n'),
          action: FilledButton.tonal(
            onPressed: _retryProviders,
            child: const Text('重新检查'),
          ),
        );
      case _Gate.error:
        return _ErrorWithRetry(
          message: ApiError.from(_gateError!).message,
          onRetry: _retryProviders,
        );
      case _Gate.ready:
        return _results(admin);
    }
  }

  Widget _results(bool admin) {
    if (_searching && _result == null) {
      return _Loading(
        label: '正在搜索「${subtitleLanguageName(_effectiveLanguage)}」字幕…',
      );
    }
    if (_searchError case final error?) {
      return _ErrorWithRetry(
        message: subtitleErrorMessage(error),
        onRetry: () => unawaited(_search(_effectiveLanguage)),
      );
    }
    final result = _result;
    if (result == null) return const SizedBox.shrink();
    if (result.allFailed) {
      return SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Notice(
              icon: Icons.cloud_off_rounded,
              color: CinemaColors.error,
              title: '所有字幕下载器都没有返回结果',
              body: result.failures
                  .map(
                    (p) =>
                        '${p.name}（${p.statusLabel}）：'
                        '${p.message ?? '没有说明'}',
                  )
                  .join('\n'),
            ),
            const SizedBox(height: 12),
            Align(
              alignment: Alignment.centerRight,
              child: FilledButton.tonal(
                autofocus: true,
                onPressed: () => unawaited(_search(_effectiveLanguage)),
                child: const Text('重试'),
              ),
            ),
          ],
        ),
      );
    }
    if (result.results.isEmpty) {
      return SizedBox(
        height: 260,
        child: EmptyState(
          icon: Icons.subtitles_off_outlined,
          title: '没有找到字幕',
          message: '可以换个语言再搜一次。',
          action: FilledButton.tonal(
            autofocus: true,
            onPressed: () => unawaited(_search(_effectiveLanguage)),
            child: const Text('重新搜索'),
          ),
        ),
      );
    }
    final failures = result.failures;
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (failures.isNotEmpty) ...[
          Notice(
            icon: Icons.warning_amber_rounded,
            color: CinemaColors.amber,
            title: '部分下载器没有返回结果',
            body: failures
                .map((p) => '${p.name}：${p.message ?? p.statusLabel}')
                .join('\n'),
          ),
          const SizedBox(height: 8),
        ],
        Flexible(
          child: ListView.separated(
            shrinkWrap: true,
            itemCount: result.results.length,
            separatorBuilder: (_, _) =>
                const Divider(height: 1, color: CinemaColors.line),
            itemBuilder: (context, index) {
              final candidate = result.results[index];
              return _CandidateTile(
                candidate: candidate,
                busy: _downloading.contains(candidate.key),
                autofocus: index == 0,
                onDownload: () => unawaited(_download(candidate)),
              );
            },
          ),
        ),
      ],
    );
  }
}

class _LanguageField extends StatelessWidget {
  const _LanguageField({
    required this.value,
    required this.enabled,
    required this.onChanged,
  });

  final String value;
  final bool enabled;
  final ValueChanged<String> onChanged;

  @override
  Widget build(BuildContext context) {
    return AppDropdownField<String>(
      label: '语言',
      value: value,
      items: [
        for (final code in subtitleLanguages)
          DropdownMenuItem(
            value: code,
            child: Text(subtitleLanguageName(code)),
          ),
      ],
      onChanged: enabled
          ? (value) {
              if (value != null) onChanged(value);
            }
          : null,
    );
  }
}

class _CandidateTile extends StatelessWidget {
  const _CandidateTile({
    required this.candidate,
    required this.busy,
    required this.autofocus,
    required this.onDownload,
  });

  final SubtitleCandidate candidate;
  final bool busy;
  final bool autofocus;
  final VoidCallback onDownload;

  @override
  Widget build(BuildContext context) {
    final meta = [
      candidate.providerName,
      if (candidate.language case final language? when language.isNotEmpty)
        subtitleLanguageName(language),
      if (candidate.format case final format? when format.isNotEmpty)
        format.toUpperCase(),
      if (candidate.forced) '强制',
      if (candidate.hearingImpaired) '听障',
      if (candidate.release case final release? when release.isNotEmpty)
        release,
      if (candidate.score case final score?) '评分 ${score.toStringAsFixed(1)}',
      if (candidate.downloads case final downloads?) '$downloads 次下载',
      if (candidate.uploadedAt case final uploadedAt?) formatDate(uploadedAt),
    ].join(' · ');
    return FocusRing(
      child: ListTile(
        autofocus: autofocus,
        enabled: !busy,
        minTileHeight: 60,
        contentPadding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
        leading: Container(
          width: 36,
          height: 36,
          decoration: BoxDecoration(
            color: CinemaColors.surface3,
            borderRadius: BorderRadius.circular(CinemaRadius.small),
          ),
          child: const Icon(
            Icons.subtitles_outlined,
            size: 20,
            color: CinemaColors.text1,
          ),
        ),
        title: Text(
          candidate.label.isEmpty ? '未命名字幕' : candidate.label,
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
          style: const TextStyle(
            color: CinemaColors.text0,
            fontWeight: FontWeight.w600,
          ),
        ),
        subtitle: meta.isEmpty
            ? null
            : Text(meta, maxLines: 2, overflow: TextOverflow.ellipsis),
        trailing: busy
            ? const SizedBox.square(
                dimension: 20,
                child: CircularProgressIndicator(strokeWidth: 2.4),
              )
            : candidate.downloaded
            ? const Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(Icons.check, color: CinemaColors.success, size: 18),
                  SizedBox(width: 4),
                  Text('已下载'),
                ],
              )
            : const Icon(Icons.download_rounded, color: CinemaColors.text2),
        onTap: busy ? null : onDownload,
      ),
    );
  }
}

class _Loading extends StatelessWidget {
  const _Loading({required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 180,
      child: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const CircularProgressIndicator(color: CinemaColors.amber),
            const SizedBox(height: 12),
            Semantics(liveRegion: true, child: Text(label)),
          ],
        ),
      ),
    );
  }
}

class _Message extends StatelessWidget {
  const _Message({
    required this.icon,
    required this.title,
    required this.body,
    this.action,
  });

  final IconData icon;
  final String title;
  final String body;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Notice(
            icon: icon,
            color: CinemaColors.text1,
            title: title,
            body: body,
          ),
          if (action != null) ...[
            const SizedBox(height: 12),
            Align(alignment: Alignment.centerRight, child: action),
          ],
        ],
      ),
    );
  }
}

class _ErrorWithRetry extends StatelessWidget {
  const _ErrorWithRetry({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Notice(
            icon: Icons.error_outline,
            color: CinemaColors.error,
            title: '没能完成',
            body: message,
          ),
          const SizedBox(height: 12),
          Align(
            alignment: Alignment.centerRight,
            child: FilledButton.tonal(
              autofocus: true,
              onPressed: onRetry,
              child: const Text('重试'),
            ),
          ),
        ],
      ),
    );
  }
}

class _InlineError extends StatelessWidget {
  const _InlineError(this.error);

  final Object error;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      liveRegion: true,
      child: Container(
        margin: const EdgeInsets.only(bottom: 8),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        decoration: BoxDecoration(
          color: CinemaColors.surface2,
          borderRadius: BorderRadius.circular(CinemaRadius.card),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Icon(
              Icons.error_outline,
              size: 20,
              color: CinemaColors.error,
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                subtitleErrorMessage(error),
                style: Theme.of(context).textTheme.bodyMedium
                    ?.copyWith(color: CinemaColors.error),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
