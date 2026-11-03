import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/launch_splash.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/snack.dart';
import 'update_controller.dart';
import 'update_dialog.dart';
import 'update_models.dart';

/// 包在主界面外面：开机动画结束后在后台检查一次更新，有新版本就弹窗。
/// 间隔、开关和“跳过此版本”的判断在 [UpdateController.autoCheck] 里。
class UpdateAutoCheck extends ConsumerStatefulWidget {
  const UpdateAutoCheck({super.key, required this.child});

  final Widget child;

  @override
  ConsumerState<UpdateAutoCheck> createState() => _UpdateAutoCheckState();
}

class _UpdateAutoCheckState extends ConsumerState<UpdateAutoCheck> {
  @override
  void initState() {
    super.initState();
    if (!ref.read(updateSupportedProvider)) return;
    // 等首页加载完再查，不和首屏请求抢网络
    Future.delayed(
      LaunchSplash.duration + const Duration(seconds: 3),
      () async {
        if (!mounted) return;
        final available = await ref
            .read(updateControllerProvider.notifier)
            .autoCheck();
        if (available != null && mounted) {
          await showUpdateDialog(context, available);
        }
      },
    );
  }

  @override
  Widget build(BuildContext context) => widget.child;
}

/// 「关于」页的检查更新行，显示最近一次检查的结果。
class CheckUpdateTile extends ConsumerWidget {
  const CheckUpdateTile({super.key});

  Future<void> _check(BuildContext context, WidgetRef ref) async {
    final status = await ref.read(updateControllerProvider.notifier).check();
    if (!context.mounted) return;
    switch (status) {
      case UpdateAvailable():
        await showUpdateDialog(context, status);
      case UpdateUpToDate():
        showSnack(context, '已是最新版本');
      case UpdateFailed(:final message):
        showSnack(context, message);
      case UpdateIdle() || UpdateChecking():
        break;
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final status = ref.watch(updateControllerProvider);
    final checking = status is UpdateChecking;
    return FocusRing(
      child: ListTile(
        leading: const Icon(Icons.system_update_outlined),
        title: const Text('检查更新'),
        subtitle: Text(switch (status) {
          UpdateIdle() => '从 GitHub 获取最新版本',
          UpdateChecking() => '正在检查…',
          UpdateUpToDate() => '已是最新版本',
          UpdateAvailable(:final update) => '发现新版本 ${update.version}',
          UpdateFailed(:final message) => '检查失败：$message',
        }),
        trailing: checking
            ? const SizedBox.square(
                dimension: 20,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            : null,
        onTap: checking ? null : () => _check(context, ref),
      ),
    );
  }
}

/// 「本机 › 常规」里的自动检查开关。
class AutoCheckUpdateTile extends ConsumerWidget {
  const AutoCheckUpdateTile({super.key});

  Future<void> _toggle(BuildContext context, WidgetRef ref, bool value) async {
    try {
      await ref.read(updateSettingsProvider.notifier).setAutoCheck(value);
    } catch (_) {
      if (context.mounted) showSnack(context, '保存失败，请稍后再试');
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final enabled = ref.watch(updateSettingsProvider).value?.autoCheck ?? true;
    return FocusRing(
      child: ListTile(
        leading: const Icon(Icons.system_update_outlined),
        title: const Text('自动检查更新'),
        subtitle: const Text('启动时检查，每天最多一次'),
        // 整行可点，开关本身不再单独抢焦点
        trailing: ExcludeFocus(
          child: Switch(
            value: enabled,
            onChanged: (value) => _toggle(context, ref, value),
          ),
        ),
        onTap: () => _toggle(context, ref, !enabled),
      ),
    );
  }
}

/// 「本机 › 常规」里的下载加速前缀：国内访问 GitHub 慢时用。
class DownloadPrefixTile extends ConsumerWidget {
  const DownloadPrefixTile({super.key});

  Future<void> _edit(
    BuildContext context,
    WidgetRef ref,
    String initial,
  ) async {
    final value = await showDialog<String>(
      context: context,
      builder: (_) => _PrefixDialog(initial: initial),
    );
    if (value == null) return;
    try {
      await ref.read(updateSettingsProvider.notifier).setDownloadPrefix(value);
    } catch (_) {
      if (context.mounted) showSnack(context, '保存失败，请稍后再试');
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final prefix =
        ref.watch(updateSettingsProvider).value?.downloadPrefix ?? '';
    return FocusRing(
      child: ListTile(
        leading: const Icon(Icons.speed_outlined),
        title: const Text('下载加速前缀'),
        subtitle: Text(prefix.isEmpty ? '未设置，直接访问 GitHub' : prefix),
        onTap: () => _edit(context, ref, prefix),
      ),
    );
  }
}

/// 可以留空（表示不加速），所以不用 showTextPrompt。
class _PrefixDialog extends StatefulWidget {
  const _PrefixDialog({required this.initial});

  final String initial;

  @override
  State<_PrefixDialog> createState() => _PrefixDialogState();
}

class _PrefixDialogState extends State<_PrefixDialog> {
  // 由对话框自己释放：关闭动画期间输入框还在用
  late final _text = TextEditingController(text: widget.initial);
  String? _error;

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  void _submit() {
    final error = downloadPrefixError(_text.text);
    if (error != null) {
      setState(() => _error = error);
      return;
    }
    Navigator.pop(context, _text.text.trim());
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      // 输入法弹出时按钮不被挡住
      scrollable: true,
      title: const Text('下载加速前缀'),
      content: TextField(
        controller: _text,
        autofocus: true,
        keyboardType: TextInputType.url,
        decoration: InputDecoration(
          labelText: '前缀地址',
          hintText: 'https://加速域名/',
          helperText: '会拼在 GitHub 地址前面，留空表示直接访问',
          helperMaxLines: 2,
          errorText: _error,
        ),
        onChanged: (_) {
          if (_error != null) setState(() => _error = null);
        },
        onSubmitted: (_) => _submit(),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('取消'),
        ),
        FilledButton(onPressed: _submit, child: const Text('保存')),
      ],
    );
  }
}
