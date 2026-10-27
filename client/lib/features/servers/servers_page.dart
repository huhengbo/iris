import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/motion.dart';
import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/servers.dart';
import '../auth/auth_controller.dart';

/// App 的服务器列表：添加、切换、删除（ADR 0017）。第一次打开 App 时停在这里。
class ServersPage extends ConsumerWidget {
  const ServersPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final list = ref.watch(serverListProvider).value ?? const ServerList();
    final add = PressScale(
      child: FilledButton.icon(
        autofocus: list.servers.isEmpty,
        icon: const Icon(Icons.add),
        label: const Text('添加服务器'),
        onPressed: () => _add(context, ref),
      ),
    );
    return Scaffold(
      appBar: AppBar(title: const Text('服务器')),
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 560),
            child: list.servers.isEmpty
                ? SingleChildScrollView(
                    child: EmptyState(
                      icon: Icons.dns_outlined,
                      title: '连接你的光圈服务器',
                      message:
                          '填写服务器地址，例如 http://192.168.1.10:8080。'
                          '可以保存多台服务器，随时切换。',
                      action: add,
                    ),
                  )
                : ListView(
                    // 行内文字仍在 gutter 上；选中行的 surface3 底左右各让出一点，
                    // 和选择弹层里的选项一样是圆角块
                    padding: const EdgeInsets.symmetric(
                      horizontal: CinemaSpacing.gutter - _rowInset,
                      vertical: 8,
                    ),
                    children: [
                      for (final server in list.servers)
                        _ServerTile(
                          server: server,
                          current: server.id == list.currentId,
                        ),
                      Padding(
                        padding: const EdgeInsets.fromLTRB(
                          _rowInset,
                          CinemaSpacing.section,
                          _rowInset,
                          8,
                        ),
                        child: add,
                      ),
                    ],
                  ),
          ),
        ),
      ),
    );
  }

  Future<void> _add(BuildContext context, WidgetRef ref) async {
    final server = await showDialog<SavedServer>(
      context: context,
      builder: (_) => const _AddServerDialog(),
    );
    if (server == null) return;
    await ref.read(serverListProvider.notifier).add(server);
    if (context.mounted) await _enter(context, ref);
  }
}

/// 切换后等新服务器的登录状态确定再进首页，避免先用旧账号请求新服务器。
Future<void> _enter(BuildContext context, WidgetRef ref) async {
  try {
    await ref.read(authControllerProvider.future);
  } catch (_) {
    // 连不上时由启动页显示错误和重试
  }
  if (context.mounted) context.go('/');
}

class _ServerTile extends ConsumerWidget {
  const _ServerTile({required this.server, required this.current});

  final SavedServer server;
  final bool current;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // 当前服务器和选择弹层的选中项一样：surface3 底 + 加粗标题 +「当前」琥珀勾
    return FocusRing(
      child: ListTile(
        autofocus: current,
        contentPadding: const EdgeInsets.symmetric(horizontal: _rowInset),
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.all(Radius.circular(CinemaRadius.card)),
        ),
        leading: Icon(
          Icons.dns_outlined,
          color: current ? CinemaColors.amber : null,
        ),
        title: Text(
          server.name,
          style: TextStyle(
            color: current ? CinemaColors.text0 : CinemaColors.text1,
            fontWeight: current ? FontWeight.w700 : FontWeight.w500,
          ),
        ),
        subtitle: Text(server.url),
        selected: current,
        selectedColor: CinemaColors.text0,
        selectedTileColor: CinemaColors.surface3,
        onTap: () async {
          if (!current) {
            await ref.read(serverListProvider.notifier).switchTo(server.id);
          }
          if (context.mounted) await _enter(context, ref);
        },
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (current) ...[
              Text('当前', style: Theme.of(context).textTheme.bodySmall),
              const SizedBox(width: 8),
              const Icon(Icons.check, color: CinemaColors.amber),
            ],
            PopupMenuButton<String>(
              tooltip: '更多',
              icon: const Icon(Icons.more_horiz),
              onSelected: (_) => _remove(context, ref),
              itemBuilder: (_) => const [
                PopupMenuItem(value: 'remove', child: Text('删除')),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _remove(BuildContext context, WidgetRef ref) async {
    final confirmed = await showConfirmDialog(
      context,
      title: '删除「${server.name}」？',
      body: '只从本机移除，本机保存的登录信息也会清除。服务器上的数据不受影响。',
      action: '删除',
      destructive: true,
    );
    if (confirmed) {
      await ref.read(serverListProvider.notifier).remove(server.id);
    }
  }
}

/// 行的左右内边距：列表让出 gutter 减去它，文字正好落在 gutter 上
const _rowInset = 8.0;

class _AddServerDialog extends ConsumerStatefulWidget {
  const _AddServerDialog();

  @override
  ConsumerState<_AddServerDialog> createState() => _AddServerDialogState();
}

class _AddServerDialogState extends ConsumerState<_AddServerDialog> {
  final _address = TextEditingController();
  bool _connecting = false;
  String? _error;

  @override
  void dispose() {
    _address.dispose();
    super.dispose();
  }

  Future<void> _connect() async {
    if (_connecting) return;
    final url = normalizeServerUrl(_address.text);
    if (url == null) {
      setState(() => _error = '请输入服务器地址，例如 http://192.168.1.10:8080');
      return;
    }
    setState(() {
      _connecting = true;
      _error = null;
    });
    try {
      final info = await ref.read(serverProbeProvider)(url);
      if (!mounted) return;
      Navigator.of(context)
          .pop(SavedServer(id: info.id, name: info.name, url: url));
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _connecting = false;
        _error = ApiError.from(e).message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      // 输入法占去半个屏幕时（TV、横屏）标题和输入框一起滚动，光标所在处保持可见
      scrollable: true,
      title: const Text('添加服务器'),
      content: SizedBox(
        width: 400,
        child: TextField(
          controller: _address,
          autofocus: true,
          enabled: !_connecting,
          keyboardType: TextInputType.url,
          autocorrect: false,
          textInputAction: TextInputAction.done,
          onSubmitted: (_) => _connect(),
          decoration: InputDecoration(
            labelText: '服务器地址',
            hintText: 'http://192.168.1.10:8080',
            errorText: _error,
            errorMaxLines: 3,
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: _connecting ? null : () => Navigator.of(context).pop(),
          child: const Text('取消'),
        ),
        FilledButton(
          onPressed: _connecting ? null : _connect,
          child: _connecting
              ? const SizedBox.square(
                  dimension: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Text('连接'),
        ),
      ],
    );
  }
}
