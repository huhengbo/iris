import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/format.dart';
import 'update_controller.dart';
import 'update_models.dart';
import 'update_platform.dart';

/// 发现新版本时的对话框：说明 → 下载（可取消）→ 调起系统安装器。
/// 默认焦点在主动作上，遥控器按确定就能一路装完。
Future<void> showUpdateDialog(
  BuildContext context,
  UpdateAvailable available,
) => showDialog<void>(
  context: context,
  barrierDismissible: false,
  builder: (_) => UpdateDialog(available: available),
);

enum _Phase { prompt, downloading, ready, failed }

class UpdateDialog extends ConsumerStatefulWidget {
  const UpdateDialog({super.key, required this.available});

  final UpdateAvailable available;

  @override
  ConsumerState<UpdateDialog> createState() => _UpdateDialogState();
}

class _UpdateDialogState extends ConsumerState<UpdateDialog> {
  _Phase _phase = _Phase.prompt;
  int _received = 0;
  int? _total;
  CancelToken? _cancel;
  String? _path;

  /// 失败原因，或安装前需要用户知道的说明（例如去允许“安装未知应用”）。
  String? _message;

  @override
  void dispose() {
    _cancel?.cancel();
    super.dispose();
  }

  Future<void> _download() async {
    final apk = widget.available.apk!;
    final cancel = CancelToken();
    setState(() {
      _phase = _Phase.downloading;
      _received = 0;
      _total = apk.size;
      _message = null;
      _cancel = cancel;
    });
    try {
      final settings = await ref.read(updateSettingsProvider.future);
      final path = await ref
          .read(updateServiceProvider)
          .download(
            widget.available.update,
            apk,
            prefix: settings.downloadPrefix,
            cancelToken: cancel,
            onProgress: (received, total) {
              if (!mounted) return;
              setState(() {
                _received = received;
                if (total > 0) _total = total;
              });
            },
          );
      if (!mounted) return;
      setState(() {
        _path = path;
        _phase = _Phase.ready;
      });
      await _install();
    } on DioException catch (error) {
      if (!mounted) return;
      // 用户点了取消：回到说明页，不算失败
      if (CancelToken.isCancel(error)) {
        setState(() => _phase = _Phase.prompt);
      } else {
        _fail('下载失败：${error.message}');
      }
    } on UpdateException catch (error) {
      _fail(error.message);
    } catch (error) {
      _fail('下载失败：$error');
    }
  }

  Future<void> _install() async {
    final path = _path;
    if (path == null) return;
    try {
      final outcome = await ref
          .read(updateServiceProvider)
          .platform
          .install(path);
      if (!mounted) return;
      setState(
        () => _message = switch (outcome) {
          InstallOutcome.needsPermission => '请在打开的设置页里允许光圈“安装未知应用”，返回后再点一次安装。',
          InstallOutcome.installing => '已打开系统安装界面。如果提示签名不一致或安装失败，需要先卸载旧版本再安装。',
        },
      );
    } on PlatformException catch (error) {
      if (!mounted) return;
      setState(
        () => _message = error.code == 'no_settings'
            ? '这台设备没有“安装未知应用”的设置页，请在系统设置的安全选项里允许“未知来源”后再点安装。'
            : '无法打开安装界面：${error.message ?? error.code}',
      );
    }
  }

  void _fail(String message) {
    if (!mounted) return;
    setState(() {
      _phase = _Phase.failed;
      _message = message;
    });
  }

  Future<void> _skip() async {
    final version = widget.available.update.version;
    try {
      await ref.read(updateControllerProvider.notifier).skip(version);
    } catch (_) {
      // 记不住跳过的版本只是下次还会提示，不影响关闭
    }
    if (mounted) Navigator.pop(context);
  }

  @override
  Widget build(BuildContext context) {
    final available = widget.available;
    final update = available.update;
    final apk = available.apk;
    final textTheme = Theme.of(context).textTheme;
    final secondary = textTheme.bodySmall!.copyWith(color: CinemaColors.text2);
    final total = _total;

    final content = <Widget>[
      Text(
        '当前版本 ${available.current}'
        '${apk == null ? '' : ' · 安装包 ${formatSize(apk.size)}'}',
        style: secondary,
      ),
      const SizedBox(height: 12),
      if (apk == null)
        const Text('发布里没有适合这台设备架构的安装包，请到 GitHub Releases 页面手动下载。')
      else if (_phase == _Phase.downloading) ...[
        LinearProgressIndicator(
          value: total == null || total == 0 ? null : _received / total,
        ),
        const SizedBox(height: 8),
        Text(
          '正在下载 ${formatSize(_received)}'
          '${total == null ? '' : ' / ${formatSize(total)}'}',
          style: secondary,
        ),
      ] else if (update.notes.trim().isNotEmpty)
        // 更新说明按纯文本显示；太长时在框里滚动
        ConstrainedBox(
          constraints: const BoxConstraints(maxHeight: 280),
          child: SingleChildScrollView(
            child: Text(plainNotes(update.notes), style: textTheme.bodyMedium),
          ),
        ),
      if (_message != null) ...[
        const SizedBox(height: 12),
        Text(
          _message!,
          style: textTheme.bodyMedium!.copyWith(
            color: _phase == _Phase.failed
                ? Theme.of(context).colorScheme.error
                : null,
          ),
        ),
      ],
    ];

    return AlertDialog(
      title: Text('发现新版本 ${update.version}'),
      content: SizedBox(
        width: 480,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: content,
        ),
      ),
      actions: switch ((apk, _phase)) {
        (null, _) => [
          FilledButton(
            autofocus: true,
            onPressed: () => Navigator.pop(context),
            child: const Text('关闭'),
          ),
        ],
        (_, _Phase.prompt) => [
          // min_version 保留，暂不启用：任何版本都可以跳过和稍后
          TextButton(onPressed: _skip, child: const Text('跳过此版本')),
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('稍后'),
          ),
          FilledButton(
            autofocus: true,
            onPressed: _download,
            child: const Text('立即更新'),
          ),
        ],
        (_, _Phase.downloading) => [
          FilledButton(
            autofocus: true,
            onPressed: () => _cancel?.cancel(),
            child: const Text('取消下载'),
          ),
        ],
        (_, _Phase.ready) => [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('关闭'),
          ),
          FilledButton(
            autofocus: true,
            onPressed: _install,
            child: const Text('安装'),
          ),
        ],
        (_, _Phase.failed) => [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('关闭'),
          ),
          FilledButton(
            autofocus: true,
            onPressed: _download,
            child: const Text('重试'),
          ),
        ],
      },
    );
  }
}
