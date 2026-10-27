part of 'libraries_page.dart';

/// 「高级」分页里的资料库行为开关。每项改了立即保存，成功或失败都提示；
/// 保存期间这一组控件全部禁用，避免连续修改的请求交错。
class _BehaviorSettings extends ConsumerStatefulWidget {
  const _BehaviorSettings({
    required this.libraryId,
    required this.values,
    required this.onSaved,
  });

  final int libraryId;
  final Map<String, String> values;
  final Future<void> Function() onSaved;

  @override
  ConsumerState<_BehaviorSettings> createState() => _BehaviorSettingsState();
}

const _behaviorSwitches = {
  'hide_seasons': '隐藏季层',
  'prefer_nfo': '优先读取 NFO',
  'auto_collections': '按 TMDB 系列自动建合集',
  'recycle_auto_empty': '同步后自动清空回收站',
};

class _BehaviorSettingsState extends ConsumerState<_BehaviorSettings> {
  /// 正在保存的设置项和它的新值，保存期间先按新值显示
  ({String key, String value})? _saving;

  String _value(String key, String fallback) {
    if (_saving case (key: final k, :final value) when k == key) return value;
    return widget.values[key] ?? fallback;
  }

  Future<void> _save(String key, String label, String value) async {
    if (_saving != null || _value(key, '') == value) return;
    setState(() => _saving = (key: key, value: value));
    try {
      await saveLibrarySetting(
        ref.read(apiClientProvider),
        widget.libraryId,
        key,
        value,
      );
      await widget.onSaved();
      if (mounted) showSnack(context, '已保存「$label」');
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    }
    if (mounted) setState(() => _saving = null);
  }

  @override
  Widget build(BuildContext context) {
    final busy = _saving != null;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
          child: Text('资料库行为', style: Theme.of(context).textTheme.titleSmall),
        ),
        for (final MapEntry(:key, value: label) in _behaviorSwitches.entries)
          FocusRing(
            child: SwitchListTile(
              title: Text(label),
              value: _value(key, 'false') == 'true',
              onChanged: busy ? null : (value) => _save(key, label, '$value'),
            ),
          ),
      ],
    );
  }
}

/// 「高级」分页里的下载器：元数据、图片、字幕三块有序列表，字幕下载语言和默认字幕语言。
class _DownloadersSection extends StatelessWidget {
  const _DownloadersSection({
    required this.id,
    required this.value,
    required this.onSaved,
    required this.subtitleLanguage,
    required this.onSubtitleLanguage,
  });

  final int id;
  final LibraryDownloaders value;
  final Future<void> Function() onSaved;
  final String? subtitleLanguage;
  final ValueChanged<String?>? onSubtitleLanguage;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const SectionTitle('元数据下载器'),
        LibraryDownloaderList(
          libraryId: id,
          role: DownloaderRole.metadata,
          items: value.metadata,
          onSaved: onSaved,
        ),
        const SectionTitle('图片获取器'),
        LibraryDownloaderList(
          libraryId: id,
          role: DownloaderRole.images,
          items: value.images,
          onSaved: onSaved,
        ),
        const SectionTitle('字幕下载器'),
        LibraryDownloaderList(
          libraryId: id,
          role: DownloaderRole.subtitles,
          items: value.subtitles,
          onSaved: onSaved,
        ),
        const SectionTitle('字幕'),
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 16),
          child: AppDropdownField<String?>(
            label: '默认字幕语言',
            value: subtitleLanguage,
            items: const [
              DropdownMenuItem<String?>(
                value: null,
                child: Text('跟随用户/服务器默认', overflow: TextOverflow.ellipsis),
              ),
              DropdownMenuItem(value: 'zh-CN', child: Text('简体中文')),
              DropdownMenuItem(value: 'zh-TW', child: Text('繁体中文')),
              DropdownMenuItem(value: 'en', child: Text('英语')),
              DropdownMenuItem(value: 'ja', child: Text('日语')),
            ],
            onChanged: onSubtitleLanguage,
          ),
        ),
        _SubtitleDownloadSettings(
          libraryId: id,
          value: value,
          onSaved: onSaved,
        ),
      ],
    );
  }
}

/// 自动下载字幕的语言和音轨判断。语言和开关各自立即保存，保存期间整组禁用。
const _subtitleDownloadLanguages = {
  'zh-Hans': '简体中文',
  'zh-Hant': '繁体中文',
  'en': '英语',
  'ja': '日语',
  'ko': '韩语',
};

class _SubtitleDownloadSettings extends ConsumerStatefulWidget {
  const _SubtitleDownloadSettings({
    required this.libraryId,
    required this.value,
    required this.onSaved,
  });

  final int libraryId;
  final LibraryDownloaders value;
  final Future<void> Function() onSaved;

  @override
  ConsumerState<_SubtitleDownloadSettings> createState() =>
      _SubtitleDownloadSettingsState();
}

class _SubtitleDownloadSettingsState
    extends ConsumerState<_SubtitleDownloadSettings> {
  late List<String> _languages = [...widget.value.subtitleLanguages];
  late bool _skipIfAudio = widget.value.subtitleSkipIfAudio;
  bool _saving = false;

  @override
  void didUpdateWidget(covariant _SubtitleDownloadSettings oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!_saving) {
      _languages = [...widget.value.subtitleLanguages];
      _skipIfAudio = widget.value.subtitleSkipIfAudio;
    }
  }

  Future<void> _save({List<String>? languages, bool? skipIfAudio}) async {
    if (_saving) return;
    final previousLanguages = _languages;
    final previousSkip = _skipIfAudio;
    setState(() {
      _saving = true;
      if (languages != null) _languages = languages;
      if (skipIfAudio != null) _skipIfAudio = skipIfAudio;
    });
    try {
      final result = await saveLibraryDownloaders(
        ref.read(apiClientProvider),
        widget.libraryId,
        subtitleLanguages: languages,
        subtitleSkipIfAudio: skipIfAudio,
      );
      if (!mounted) return;
      setState(() {
        _languages = result.subtitleLanguages;
        _skipIfAudio = result.subtitleSkipIfAudio;
      });
      await widget.onSaved();
      if (mounted) showSnack(context, '已保存字幕下载设置');
    } catch (error) {
      if (mounted) {
        setState(() {
          _languages = previousLanguages;
          _skipIfAudio = previousSkip;
        });
        showApiErrorSnack(context, error);
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  void _toggleLanguage(String code, bool selected) {
    final next = {..._languages};
    selected ? next.add(code) : next.remove(code);
    // 按固定顺序提交，读屏和比较都稳定
    _save(
      languages: [
        for (final option in _subtitleDownloadLanguages.keys)
          if (next.contains(option)) option,
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                '字幕下载语言',
                style: text.labelLarge?.copyWith(color: CinemaColors.text2),
              ),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final MapEntry(:key, :value)
                      in _subtitleDownloadLanguages.entries)
                    FocusRing(
                      child: FilterChip(
                        label: Text(value),
                        selected: _languages.contains(key),
                        onSelected: _saving
                            ? null
                            : (selected) => _toggleLanguage(key, selected),
                      ),
                    ),
                ],
              ),
              const SizedBox(height: 8),
              Text(
                '由计划任务『下载字幕』执行',
                style: text.bodySmall?.copyWith(color: CinemaColors.text2),
              ),
            ],
          ),
        ),
        FocusRing(
          child: SwitchListTile(
            title: const Text('音轨是该语言时跳过'),
            value: _skipIfAudio,
            onChanged: _saving ? null : (value) => _save(skipIfAudio: value),
          ),
        ),
      ],
    );
  }
}
