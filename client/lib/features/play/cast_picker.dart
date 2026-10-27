part of 'cast_page.dart';

/// 投到电视上放什么。
class CastArgs {
  const CastArgs({
    required this.device,
    required this.playableId,
    required this.title,
    required this.url,
    this.start,
    this.hasSubtitles = false,
  });

  factory CastArgs.fromTicket(CastDevice device, PlayTicket ticket) => CastArgs(
    device: device,
    playableId: ticket.playableId,
    title: ticket.title,
    // 投屏只在签发了对外地址时进行，没有时签发就已失败
    url: ticket.url!,
    start: ticket.position,
    hasSubtitles: ticket.subtitles.isNotEmpty,
  );

  final CastDevice device;
  final int playableId;
  final String title;

  /// 对外的播放跳转地址：电视自己请求，服务端按电视的 UA 取直链（ADR 0018）
  final String url;
  final Duration? start;

  /// 有外挂字幕时提示一句，DLNA 投不过去
  final bool hasSubtitles;
}

// ---- 选电视 ----

/// 搜索同一个 Wi-Fi 里的电视并选一台。手机上是底部弹层，宽屏用居中对话框。
Future<CastDevice?> showCastDevicePicker(BuildContext context) =>
    showPickerSheet<CastDevice>(context, builder: (_) => const _DevicePicker());

class _DevicePicker extends ConsumerStatefulWidget {
  const _DevicePicker();

  @override
  ConsumerState<_DevicePicker> createState() => _DevicePickerState();
}

class _DevicePickerState extends ConsumerState<_DevicePicker> {
  final _devices = <CastDevice>[];
  StreamSubscription<CastDevice>? _search;
  bool _searching = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _start();
  }

  @override
  void dispose() {
    unawaited(_search?.cancel());
    super.dispose();
  }

  void _start() {
    unawaited(_search?.cancel());
    setState(() {
      _devices.clear();
      _searching = true;
      _error = null;
    });
    _search = ref
        .read(castDiscoveryProvider)
        .search()
        .listen(
          (device) => setState(() => _devices.add(device)),
          onError: (Object e) => setState(
            () => _error = e is CastException ? e.message : '搜索时出了问题，请重新搜索',
          ),
          onDone: () => setState(() => _searching = false),
        );
  }

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final empty = !_searching && _devices.isEmpty;
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const PickerHeader(title: '投屏'),
        for (final (i, device) in _devices.indexed)
          PickerOption(
            title: device.name,
            icon: Icons.tv_rounded,
            autofocus: i == 0,
            onTap: () => Navigator.of(context).pop(device),
          ),
        if (_searching)
          Semantics(
            liveRegion: true,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(8, 12, 8, 12),
              child: Row(
                children: [
                  const SizedBox.square(
                    dimension: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
                  const SizedBox(width: 14),
                  Text('正在搜索设备…', style: textTheme.bodyMedium),
                ],
              ),
            ),
          ),
        if (empty)
          Semantics(
            liveRegion: true,
            child: EmptyState(
              icon: Icons.tv_off_rounded,
              title: _error == null ? '未发现可投屏的设备' : '搜索失败',
              message: _error ?? '请确认设备已开机，并与手机连接同一个 Wi-Fi',
            ),
          ),
        if (!_searching)
          Align(
            child: TextButton.icon(
              autofocus: _devices.isEmpty,
              onPressed: _start,
              icon: const Icon(Icons.refresh),
              label: const Text('重新搜索'),
            ),
          ),
      ],
    );
  }
}
