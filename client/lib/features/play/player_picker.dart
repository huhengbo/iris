part of 'play_launcher.dart';

/// 选择播放器（和版本）。手机上是底部弹层；宽屏（包括 TV）用居中对话框，
/// 遥控器操作不适合底部弹层。
Future<PlayChoice?> showPlayerPicker(
  BuildContext context, {
  required String subtitle,
  required List<ExternalPlayer> players,
  ExternalPlayer? current,
  List<MediaVersion> versions = const [],
}) => showPickerSheet<PlayChoice>(
  context,
  builder: (_) => _PlayerPicker(
    subtitle: subtitle,
    players: players,
    current: current,
    versions: versions,
  ),
);

class _PlayerPicker extends StatefulWidget {
  const _PlayerPicker({
    required this.subtitle,
    required this.players,
    required this.current,
    required this.versions,
  });

  final String subtitle;
  final List<ExternalPlayer> players;
  final ExternalPlayer? current;
  final List<MediaVersion> versions;

  @override
  State<_PlayerPicker> createState() => _PlayerPickerState();
}

class _PlayerPickerState extends State<_PlayerPicker> {
  late bool _remember = widget.current != null;

  /// 为空时由服务端选：上次用过的版本，没有则选最大的
  int? _versionId;

  void _pick(ExternalPlayer player) => Navigator.of(context).pop<PlayChoice>((
    player: player,
    versionId: _versionId,
    remember: _remember,
  ));

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final current = widget.current;
    final versions = widget.versions;
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        PickerHeader(title: '用什么播放', subtitle: widget.subtitle),
        if (versions.length > 1) ...[
          Padding(
            padding: const EdgeInsets.only(left: 4),
            child: Text('版本', style: textTheme.bodySmall),
          ),
          const SizedBox(height: 6),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              ChoiceChip(
                label: const Text('自动'),
                tooltip: '上次用过的版本，没有则选最大的',
                selected: _versionId == null,
                onSelected: (_) => setState(() => _versionId = null),
              ),
              for (final v in versions)
                ChoiceChip(
                  label: Text(v.label),
                  selected: _versionId == v.id,
                  onSelected: (_) => setState(() => _versionId = v.id),
                ),
            ],
          ),
          const SizedBox(height: 8),
        ],
        for (final player in widget.players)
          PickerOption(
            title: player.label,
            icon: player.icon,
            // 选中 = 记住的默认播放器
            selected: player == current,
            selectedLabel: '默认',
            // 遥控器一打开就能选：焦点在默认播放器上，没有默认时在第一个
            autofocus: player == (current ?? widget.players.first),
            onTap: () => _pick(player),
          ),
        if (widget.players.any((p) => p.rememberable)) ...[
          const SizedBox(height: 4),
          const Divider(),
          FocusRing(
            child: SwitchListTile(
              contentPadding: const EdgeInsets.symmetric(horizontal: 4),
              title: const Text('记住我的选择'),
              value: _remember,
              onChanged: (v) => setState(() => _remember = v),
            ),
          ),
        ],
      ],
    );
  }
}

/// 播放链接和字幕链接，复制到任意支持网络串流的播放器。
/// 复制要在用户点按里直接完成，Safari 才允许写剪贴板。
Future<void> showPlayLinkDialog(BuildContext context, PlayTicket ticket) {
  return showDialog<void>(
    context: context,
    builder: (context) => AlertDialog(
      title: const Text('播放链接'),
      content: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 480),
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                '${ticket.title}\n复制到支持网络串流的播放器即可播放，24 小时内有效。',
                style: Theme.of(context).textTheme.bodySmall,
              ),
              const SizedBox(height: 12),
              if (ticket.url case final url?)
                CopyRow(
                  label: '视频',
                  value: url,
                  tooltip: '复制视频链接',
                  autofocus: true,
                ),
              for (final s in ticket.subtitles)
                if (s.url case final url?)
                  CopyRow(
                    label: '字幕 · ${s.label}',
                    value: url,
                    tooltip: '复制字幕 · ${s.label}链接',
                  ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(context).pop(),
          child: const Text('关闭'),
        ),
      ],
    ),
  );
}
