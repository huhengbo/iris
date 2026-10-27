import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/providers.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/snack.dart';
import 'admin_widgets.dart';
import 'busy_icon.dart';
import 'maintenance_api.dart';

/// Webhook：把播放、同步和警报事件推送到外部地址。
class WebhooksPage extends ConsumerStatefulWidget {
  const WebhooksPage({super.key});

  @override
  ConsumerState<WebhooksPage> createState() => _WebhooksPageState();
}

class _WebhooksPageState extends ConsumerState<WebhooksPage> {
  /// 正在处理的 Webhook（测试、启停、删除），处理中该行的菜单换成小圈
  final _busy = <int>{};

  Future<void> _create() async {
    final created = await showDialog<bool>(
      context: context,
      builder: (_) => _CreateDialog(
        submit: (url, events) =>
            createWebhook(ref.read(apiClientProvider), url, events),
      ),
    );
    if (created != true || !mounted) return;
    showSnack(context, '已添加 Webhook');
    ref.invalidate(webhooksProvider);
  }

  Future<void> _guard(AdminWebhook hook, Future<void> Function() action) async {
    setState(() => _busy.add(hook.id));
    try {
      await action();
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    }
    if (mounted) setState(() => _busy.remove(hook.id));
  }

  Future<void> _test(AdminWebhook hook) => _guard(hook, () async {
    final delivery = await testWebhook(ref.read(apiClientProvider), hook.id);
    if (!mounted) return;
    final response = delivery.response;
    showSnack(context, switch (delivery.status) {
      'succeeded' => '测试投递成功${response == null ? '' : '：$response'}',
      'failed' => '测试投递失败：${response ?? '原因未知'}',
      _ => '测试投递已发出，对方还没有响应',
    });
  });

  Future<void> _toggle(AdminWebhook hook) => _guard(hook, () async {
    await setWebhookEnabled(ref.read(apiClientProvider), hook, !hook.enabled);
    ref.invalidate(webhooksProvider);
    await ref.read(webhooksProvider.future);
  });

  Future<void> _delete(AdminWebhook hook) async {
    final confirmed = await showConfirmDialog(
      context,
      title: '删除 Webhook？',
      body: '${hook.url} 将不再收到事件，投递记录也会一起删除。',
      action: '删除',
      destructive: true,
    );
    if (!confirmed || !mounted) return;
    await _guard(hook, () async {
      await deleteWebhook(ref.read(apiClientProvider), hook.id);
      if (mounted) showSnack(context, '已删除 Webhook');
      ref.invalidate(webhooksProvider);
      await ref.read(webhooksProvider.future);
    });
  }

  @override
  Widget build(BuildContext context) {
    final hooks = ref.watch(webhooksProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('Webhook'),
        actions: [
          IconButton(
            tooltip: '刷新',
            onPressed: () => ref.invalidate(webhooksProvider),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      floatingActionButton: FocusRing(
        radius: CinemaRadius.fab,
        child: FloatingActionButton.extended(
          onPressed: _create,
          icon: const Icon(Icons.add),
          label: const Text('新建 Webhook'),
        ),
      ),
      body: AsyncView(
        value: hooks,
        onRetry: () => ref.invalidate(webhooksProvider),
        builder: (items) => items.isEmpty
            ? const EmptyState(
                icon: Icons.webhook_outlined,
                title: '还没有 Webhook',
                message: '添加后，播放、同步和警报事件会推送到你指定的地址。',
              )
            : RefreshIndicator(
                onRefresh: () async => ref.refresh(webhooksProvider.future),
                child: AdminListWidth(
                  top: 8,
                  // 给悬浮按钮让出位置
                  bottom: 96,
                  builder: (padding) => ListView.builder(
                    padding: padding,
                    itemCount: items.length,
                    itemBuilder: (context, index) {
                      final hook = items[index];
                      return _WebhookTile(
                        hook: hook,
                        busy: _busy.contains(hook.id),
                        onTest: () => _test(hook),
                        onToggle: () => _toggle(hook),
                        onDelete: () => _delete(hook),
                      );
                    },
                  ),
                ),
              ),
      ),
    );
  }
}

class _WebhookTile extends StatelessWidget {
  const _WebhookTile({
    required this.hook,
    required this.busy,
    required this.onTest,
    required this.onToggle,
    required this.onDelete,
  });

  final AdminWebhook hook;
  final bool busy;
  final VoidCallback onTest;
  final VoidCallback onToggle;
  final VoidCallback onDelete;

  @override
  Widget build(BuildContext context) {
    // 行本身不可点，操作都在行尾菜单里，聚焦环描在菜单按钮上
    return ListTile(
      contentPadding: const EdgeInsets.only(
        left: CinemaSpacing.gutter,
        right: CinemaSpacing.gutter - 12,
      ),
      leading: Icon(
        Icons.webhook_outlined,
        color: hook.enabled ? null : CinemaColors.text2,
      ),
      title: Text(hook.url, maxLines: 1, overflow: TextOverflow.ellipsis),
      subtitle: Text(
        '${hook.enabled ? '已启用' : '已停用'} · ${hook.eventsLabel}',
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
      ),
      trailing: busy
          ? const BusyIcon()
          : FocusRing(
              child: PopupMenuButton<VoidCallback>(
                tooltip: '更多操作',
                onSelected: (action) => action(),
                itemBuilder: (_) => [
                  // 停用的 Webhook 服务端不投递
                  PopupMenuItem(
                    value: onTest,
                    enabled: hook.enabled,
                    child: const Text('发送测试'),
                  ),
                  PopupMenuItem(
                    value: onToggle,
                    child: Text(hook.enabled ? '停用' : '启用'),
                  ),
                  PopupMenuItem(value: onDelete, child: const Text('删除')),
                ],
              ),
            ),
    );
  }
}

/// 新建 Webhook：地址和要推送的事件。提交在对话框里进行，出错时留在对话框改。
class _CreateDialog extends StatefulWidget {
  const _CreateDialog({required this.submit});

  final Future<void> Function(String url, List<String> events) submit;

  @override
  State<_CreateDialog> createState() => _CreateDialogState();
}

class _CreateDialogState extends State<_CreateDialog> {
  // 由对话框自己释放：关闭动画期间输入框还在用
  final _url = TextEditingController();
  final _events = webhookEvents.keys.toSet();
  bool _saving = false;
  String? _urlError;
  String? _error;

  @override
  void dispose() {
    _url.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (_saving) return;
    final url = _url.text.trim();
    final uri = Uri.tryParse(url);
    final valid =
        uri != null &&
        (uri.scheme == 'http' || uri.scheme == 'https') &&
        uri.host.isNotEmpty;
    setState(() {
      _urlError = valid ? null : '请填写 http:// 或 https:// 开头的地址';
      _error = _events.isEmpty ? '至少选择一个事件' : null;
    });
    if (!valid || _events.isEmpty) return;
    setState(() => _saving = true);
    try {
      // 全选时订阅全部事件，以后新增的事件也会推送
      await widget.submit(
        url,
        _events.length == webhookEvents.length
            ? const ['*']
            : [
                for (final key in webhookEvents.keys)
                  if (_events.contains(key)) key,
              ],
      );
      if (mounted) Navigator.pop(context, true);
    } catch (error) {
      if (mounted) {
        setState(() {
          _saving = false;
          _error = ApiError.from(error).message;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return AlertDialog(
      // 输入法弹出时按钮不被挡住
      scrollable: true,
      title: const Text('新建 Webhook'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          TextField(
            controller: _url,
            autofocus: true,
            enabled: !_saving,
            keyboardType: TextInputType.url,
            decoration: InputDecoration(
              labelText: '推送地址',
              hintText: 'https://example.com/hook',
              errorText: _urlError,
            ),
            onSubmitted: (_) => _save(),
          ),
          const SizedBox(height: CinemaSpacing.heading),
          Text(
            '推送的事件',
            style: text.labelLarge?.copyWith(color: CinemaColors.text2),
          ),
          for (final MapEntry(:key, :value) in webhookEvents.entries)
            FocusRing(
              child: CheckboxListTile(
                contentPadding: EdgeInsets.zero,
                dense: true,
                value: _events.contains(key),
                onChanged: _saving
                    ? null
                    : (checked) => setState(
                        () => checked == true
                            ? _events.add(key)
                            : _events.remove(key),
                      ),
                title: Text(value),
              ),
            ),
          if (_error case final error?)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                error,
                style: text.bodySmall?.copyWith(color: CinemaColors.error),
              ),
            ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: _saving ? null : () => Navigator.pop(context, false),
          child: const Text('取消'),
        ),
        FilledButton(
          onPressed: _saving ? null : _save,
          child: Text(_saving ? '正在添加…' : '添加'),
        ),
      ],
    );
  }
}
