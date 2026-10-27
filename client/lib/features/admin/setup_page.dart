import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/focus_ring.dart';
import 'admin_widgets.dart';
import 'setup_api.dart';

class SetupPage extends ConsumerWidget {
  const SetupPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('配置向导'),
        actions: [
          IconButton(
            tooltip: '刷新',
            onPressed: () => ref.invalidate(setupStatusProvider),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: AsyncView(
        value: ref.watch(setupStatusProvider),
        onRetry: () => ref.invalidate(setupStatusProvider),
        builder: (status) => AdminListWidth(
          builder: (padding) => ListView(
            padding: padding,
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(
                  CinemaSpacing.gutter,
                  16,
                  CinemaSpacing.gutter,
                  12,
                ),
                child: Text(
                  status.complete
                      ? '基础配置已完成，可以继续使用管理后台。'
                      : '按顺序完成这些步骤即可开始同步媒体。',
                  style: Theme.of(context).textTheme.bodyLarge,
                ),
              ),
              for (var index = 0; index < status.steps.length; index++)
                _SetupTile(
                  step: status.steps[index],
                  number: index + 1,
                  onRetry: () => ref.invalidate(setupStatusProvider),
                ),
            ],
          ),
        ),
      ),
    );
  }
}

class _SetupTile extends StatefulWidget {
  const _SetupTile({
    required this.step,
    required this.number,
    required this.onRetry,
  });

  final SetupStep step;
  final int number;
  final VoidCallback onRetry;

  @override
  State<_SetupTile> createState() => _SetupTileState();
}

class _SetupTileState extends State<_SetupTile> {
  // 「稍后处理」只收起本次页面里的提示，不改服务端状态
  var _snoozed = false;

  @override
  Widget build(BuildContext context) {
    final step = widget.step;
    final number = widget.number;
    final tile = ListTile(
      contentPadding: const EdgeInsets.symmetric(
        horizontal: CinemaSpacing.gutter,
      ),
      // 序号是装饰，未完成的步骤用中性色；完成了才用成功色勾
      leading: CircleAvatar(
        backgroundColor: CinemaColors.surface3,
        foregroundColor: step.complete
            ? CinemaColors.success
            : step.failed
            ? Theme.of(context).colorScheme.error
            : CinemaColors.text1,
        child: step.complete
            ? const Icon(Icons.check)
            : step.failed
            ? const Icon(Icons.error_outline)
            : Text('$number'),
      ),
      title: Text(step.title),
      subtitle: Text(
        step.failed ? '需要处理：${step.message}' : step.message,
        style: step.failed
            ? TextStyle(color: Theme.of(context).colorScheme.error)
            : null,
      ),
      trailing: step.actionPath == null
          ? null
          : const Icon(Icons.chevron_right),
      onTap: step.actionPath == null
          ? null
          : () => context.push(step.actionPath!),
    );
    final body = step.actionPath == null ? tile : FocusRing(child: tile);
    if (!step.failed || _snoozed) return body;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        body,
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: CinemaSpacing.gutter),
          child: Wrap(
            spacing: 8,
            children: [
              TextButton.icon(
                onPressed: widget.onRetry,
                icon: const Icon(Icons.refresh),
                label: const Text('重试'),
              ),
              TextButton(
                onPressed: () => setState(() => _snoozed = true),
                child: const Text('稍后处理'),
              ),
            ],
          ),
        ),
      ],
    );
  }
}
