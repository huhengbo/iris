import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/shell.dart' show wideLayoutBreakpoint;
import '../../app/theme.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/picker_sheet.dart';
import '../../core/widgets/snack.dart';
import '../auth/auth_controller.dart';
import '../library/browse_api.dart';
import '../me/preferences_api.dart';
import '../subtitles/subtitle_search_sheet.dart';
import '../subtitles/subtitles_api.dart';
import 'media_info_view.dart';

/// 有语言时显示语言名，片源没标语言才退回文件给的名字。在线字幕多标一个来源。
String _subtitleName(MediaSubtitle subtitle) {
  final name = languageName(subtitle.language) ?? subtitle.label;
  final suffix = subtitle.forced ? '（强制）' : '';
  return subtitle.isOnline ? '$name$suffix · 在线' : '$name$suffix';
}

/// 按钮行下方的版本、音频、字幕三个下拉，参考 Plex 的轨道选择。
/// 版本和字幕选完影响接下来播放；画面信息写在版本每一项的说明里，不再单独占一行。
/// 音频只能查看：音轨由内置播放器打开文件后自己选，这里选了不会改变播放。
class MediaStreams extends ConsumerWidget {
  const MediaStreams({
    super.key,
    required this.versions,
    required this.versionId,
    required this.subtitleId,
    required this.onVersion,
    required this.onSubtitle,
    this.onSubtitleAdded,
    this.autoVersion = true,
  });

  final List<MediaVersion> versions;
  final int? versionId;
  final int? subtitleId;
  final ValueChanged<int?> onVersion;
  final ValueChanged<int?> onSubtitle;

  /// 从「搜索字幕」下载成功后回调；调用方刷新详情并选中新字幕。
  final Future<void> Function(MediaSubtitle subtitle)? onSubtitleAdded;

  /// 版本里有没有「自动」。电影在详情页就定下版本，默认第一个（最大的）。
  final bool autoVersion;

  /// 搜索结果里代表「搜索字幕」的哨兵值，字幕 id 都是正数。
  static const _searchMore = -1;

  /// 窄到放不下一行三个下拉时改成上下排列。
  static const _rowBreakpoint = 520.0;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final picked = versions.where((v) => v.id == versionId).firstOrNull;
    // 没选时展示排在最前的版本：服务端多半也选它
    final version = picked ?? versions.first;
    final info = version.media;
    final audio = info?.audio ?? const <MediaAudio>[];
    final embedded = info?.subtitles.map(embeddedSubtitleSummary).join('、');
    final subtitles = version.subtitles;
    final preferred = languageName(ref.watch(subtitleLanguageProvider).value);
    final auto = preferred == null ? '自动' : '自动（$preferred）';
    final subtitle = subtitles.where((s) => s.id == subtitleId).firstOrNull;
    // 「搜索字幕」只在用户被允许、且至少有一个配置完整的字幕下载器时出现
    final canDownload =
        ref.watch(authControllerProvider).value?.canDownloadSubtitles ?? false;
    final canSearch =
        canDownload &&
        (ref.watch(subtitleProvidersProvider(version.id)).value?.usable ??
            false);
    final hasEmbedded = embedded != null && embedded.isNotEmpty;

    final currentAudio = audio.isEmpty
        ? null
        : audio.firstWhere((t) => t.isDefault, orElse: () => audio.first);
    // 宽屏用锚定的下拉菜单，手机用底部弹层：两边选项和顺序一致
    final versionChoices = versions.length == 1
        ? null
        : <Choice<int?>>[
            if (autoVersion) (value: null, label: '自动', subtitle: null),
            for (final v in versions)
              (
                value: v.id,
                label: v.label,
                // 画面信息并到每一项的说明里
                subtitle: v.media == null ? null : videoSummary(v.media!),
              ),
          ];
    final audioChoices = audio.length <= 1
        ? null
        : <Choice<int>>[
            for (final (i, track) in audio.indexed)
              (
                value: i,
                label: audioSummary(track),
                subtitle: track.isDefault ? '播放器默认' : null,
              ),
          ];
    final subtitleChoices = subtitles.isEmpty && !canSearch
        ? null
        : <Choice<int?>>[
            (value: null, label: auto, subtitle: '按「我的 › 设置」里的默认字幕语言'),
            for (final s in subtitles)
              (
                value: s.id,
                label: _subtitleName(s),
                subtitle: s.label == _subtitleName(s) ? null : s.label,
              ),
            if (canSearch)
              (value: _searchMore, label: '搜索字幕', subtitle: '从在线字幕下载器搜索'),
          ];

    final fields = [
      _StreamDropdown<int?>(
        label: '版本',
        // 只有一个版本时没有可选项，直接写它的名字，不显示「自动」
        value: versions.length == 1
            ? version.label
            : picked?.label ?? (autoVersion ? '自动' : version.label),
        current: autoVersion ? versionId : version.id,
        choices: versionChoices,
        onSelected: onVersion,
      ),
      // 没有上报过流信息时没有音轨可选，就不占位置
      if (currentAudio != null)
        _StreamDropdown<int>(
          label: '音频',
          value: audioSummary(currentAudio),
          current: audio.indexOf(currentAudio),
          choices: audioChoices,
          onSelected: (index) {
            // 播放接口只支持选字幕，音轨由播放器打开文件后自己选
            if (index != audio.indexOf(currentAudio)) {
              showSnack(context, '音轨在播放器里切换');
            }
          },
        ),
      // 没有外挂字幕时，只要还能搜在线字幕，也保留这一项
      if (subtitles.isNotEmpty || canSearch || hasEmbedded)
        _StreamDropdown<int?>(
          label: '字幕',
          value: subtitle != null
              ? _subtitleName(subtitle)
              : subtitles.isEmpty && hasEmbedded
              ? '内封 · $embedded'
              : auto,
          current: subtitle?.id,
          choices: subtitleChoices,
          onSelected: (id) async {
            if (id == _searchMore) {
              final result = await showSubtitleSearchSheet(
                context,
                versionId: version.id,
                title: version.label,
              );
              if (result == null || !context.mounted) return;
              await onSubtitleAdded?.call(result.subtitle);
              return;
            }
            onSubtitle(id);
          },
        ),
    ];
    return LayoutBuilder(
      builder: (context, constraints) {
        if (constraints.maxWidth < _rowBreakpoint) {
          return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              for (final (i, field) in fields.indexed) ...[
                if (i > 0) const SizedBox(height: 8),
                field,
              ],
            ],
          );
        }
        return Row(
          children: [
            for (final (i, field) in fields.indexed) ...[
              if (i > 0) const SizedBox(width: 12),
              Expanded(child: field),
            ],
          ],
        );
      },
    );
  }
}

/// 一个下拉样式的轨道选择：上面是标签，下面是当前值，右侧箭头；没有可选项时
/// 只展示、不带箭头，也不占焦点。
/// 宽屏点开是锚定在控件下方的下拉菜单；手机用全应用统一的选择弹层（底部弹层）。
class _StreamDropdown<T> extends StatelessWidget {
  const _StreamDropdown({
    required this.label,
    required this.value,
    required this.current,
    required this.choices,
    required this.onSelected,
  });

  final String label;
  final String value;

  /// 当前选中的值，菜单里带琥珀勾
  final T? current;

  /// 为空表示没有可选项，只展示
  final List<Choice<T>>? choices;
  final ValueChanged<T> onSelected;

  @override
  Widget build(BuildContext context) {
    final choices = this.choices;
    if (choices == null) return _field(context);
    if (MediaQuery.sizeOf(context).width < wideLayoutBreakpoint) {
      return FocusRing(
        radius: CinemaRadius.control,
        child: _field(
          context,
          onTap: () async {
            final choice = await showChoicePicker<T>(
              context,
              title: label,
              current: current,
              choices: choices,
            );
            if (choice != null) onSelected(choice.$1);
          },
        ),
      );
    }
    final textTheme = Theme.of(context).textTheme;
    return FocusRing(
      radius: CinemaRadius.control,
      child: PopupMenuButton<T>(
        tooltip: label,
        constraints: const BoxConstraints(minWidth: 240, maxWidth: 380),
        onSelected: onSelected,
        itemBuilder: (context) => [
          for (final choice in choices)
            PopupMenuItem<T>(
              value: choice.value,
              height: choice.subtitle == null ? 44 : 56,
              child: Row(
                children: [
                  SizedBox(
                    width: 28,
                    child: choice.value == current
                        ? const Icon(
                            Icons.check,
                            size: 18,
                            color: CinemaColors.amber,
                          )
                        : null,
                  ),
                  Expanded(
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          choice.label,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                        ),
                        if (choice.subtitle case final subtitle?)
                          Text(
                            subtitle,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: textTheme.bodySmall,
                          ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
        ],
        child: _field(context),
      ),
    );
  }

  Widget _field(BuildContext context, {VoidCallback? onTap}) {
    final textTheme = Theme.of(context).textTheme;
    return Material(
      color: CinemaColors.surface1,
      clipBehavior: Clip.antiAlias,
      borderRadius: BorderRadius.circular(CinemaRadius.control),
      child: InkWell(
        onTap: onTap,
        child: ConstrainedBox(
          constraints: const BoxConstraints(minHeight: 48),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(14, 8, 10, 8),
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        label,
                        style: textTheme.labelSmall!.copyWith(
                          color: CinemaColors.text2,
                        ),
                      ),
                      const SizedBox(height: 2),
                      Text(
                        value,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: textTheme.bodyMedium!.copyWith(
                          color: CinemaColors.text0,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ],
                  ),
                ),
                if (choices != null)
                  const Icon(
                    Icons.expand_more,
                    size: 20,
                    color: CinemaColors.text2,
                  )
                else
                  const SizedBox(width: 4),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
