part of 'users_page.dart';

/// 用户登录过的设备。撤销单个设备或退出全部设备前都先确认，进行中其他操作禁用。
class _SessionsDialog extends ConsumerStatefulWidget {
  const _SessionsDialog({required this.user});

  final AppUser user;

  @override
  ConsumerState<_SessionsDialog> createState() => _SessionsDialogState();
}

class _SessionsDialogState extends ConsumerState<_SessionsDialog> {
  /// 正在撤销的会话；为 0 表示正在退出全部设备
  int? _busy;

  Future<void> _revoke(Map<String, dynamic> session) async {
    if (_busy != null) return;
    final device = session['device_name'] as String? ?? '未知设备';
    final thirdParty = session['kind'] == 'emby';
    if (!await showConfirmDialog(
      context,
      title: '撤销「$device」的登录？',
      body: thirdParty ? '这台第三方客户端会立即退出登录，需要重新登录。' : '这台设备会立即退出登录，需要重新输入密码。',
      action: '撤销',
      destructive: true,
    )) {
      return;
    }
    if (!mounted) return;
    setState(() => _busy = session['id'] as int);
    try {
      await revokeUserSession(
        ref.read(apiClientProvider),
        widget.user.id,
        session['id'] as int,
      );
      final _ = await ref.refresh(userSessionsProvider(widget.user.id).future);
      if (mounted) showSnack(context, '已撤销「$device」的登录');
    } catch (e) {
      if (mounted) showApiErrorSnack(context, e);
    }
    if (mounted) setState(() => _busy = null);
  }

  Future<void> _revokeAll() async {
    if (_busy != null) return;
    final name = widget.user.username;
    if (!await showConfirmDialog(
      context,
      title: '让 $name 退出全部设备？',
      body: '所有设备都会立即退出登录，需要重新输入密码。',
      action: '退出',
      destructive: true,
    )) {
      return;
    }
    if (!mounted) return;
    setState(() => _busy = 0);
    try {
      await revokeAllUserSessions(ref.read(apiClientProvider), widget.user.id);
      if (!mounted) return;
      showSnack(context, '已让 $name 退出全部设备');
      Navigator.of(context).pop();
      return;
    } catch (e) {
      if (mounted) showApiErrorSnack(context, e);
    }
    if (mounted) setState(() => _busy = null);
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final sessions = ref.watch(userSessionsProvider(widget.user.id));
    final hasSessions = sessions.value?.isNotEmpty ?? false;
    return AlertDialog(
      title: Text('${widget.user.username} 的设备与会话'),
      // 加载圈和重试在可滚动区域里按内容高度显示，不把对话框撑满
      content: SizedBox(
        width: 520,
        child: SingleChildScrollView(
          child: AsyncView(
            value: sessions,
            onRetry: () => ref.invalidate(userSessionsProvider(widget.user.id)),
            builder: (list) => list.isEmpty
                ? Text(
                    '没有登录中的设备',
                    style: text.bodyMedium?.copyWith(color: CinemaColors.text2),
                  )
                : Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      for (final session in list)
                        ListTile(
                          contentPadding: EdgeInsets.zero,
                          title: Text(
                            session['device_name'] as String? ?? '未知设备',
                          ),
                          subtitle: Text(_sessionDetail(session)),
                          trailing: _busy == session['id']
                              ? const Padding(
                                  padding: EdgeInsets.all(12),
                                  child: SizedBox.square(
                                    dimension: 20,
                                    child: CircularProgressIndicator(
                                      strokeWidth: 2,
                                    ),
                                  ),
                                )
                              : IconButton(
                                  tooltip: '撤销',
                                  icon: const Icon(Icons.logout),
                                  onPressed: _busy == null
                                      ? () => _revoke(session)
                                      : null,
                                ),
                        ),
                    ],
                  ),
          ),
        ),
      ),
      actions: [
        TextButton(
          autofocus: true,
          onPressed: () => Navigator.pop(context),
          child: const Text('关闭'),
        ),
        FilledButton.tonal(
          onPressed: _busy == null && hasSessions ? _revokeAll : null,
          child: _busy == 0
              ? const SizedBox.square(
                  dimension: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Text('退出全部设备'),
        ),
      ],
    );
  }
}

/// 设备与会话的副标题：第三方客户端标记、客户端名、平台、版本、IP 和最近活动。
String _sessionDetail(Map<String, dynamic> session) {
  final seen = session['last_seen_at'] as String?;
  return [
    if (session['kind'] == 'emby') '第三方客户端',
    session['client_name'],
    session['platform'],
    session['client_version'],
    session['ip'],
    if (seen != null) '最近活动 ${formatIsoAgo(seen)}',
  ].whereType<String>().where((s) => s.isNotEmpty).join(' · ');
}
