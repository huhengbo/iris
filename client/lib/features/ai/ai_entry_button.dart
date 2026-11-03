import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/shell.dart' show wideLayoutBreakpoint;
import '../../app/tv.dart';
import 'ai_availability.dart';
import 'ai_chat_drawer.dart';

/// 顶栏上的 AI 助手入口（光圈图标）。配置了对话模型才出现，电视没有。
/// 窄屏进全屏对话页，宽屏从右侧滑出抽屉。
class AiEntryButton extends ConsumerWidget {
  const AiEntryButton({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    if (!ref.watch(aiAvailabilityProvider).chat ||
        ref.watch(televisionProvider)) {
      return const SizedBox.shrink();
    }
    return IconButton(
      tooltip: 'AI 助手',
      onPressed: () => MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint
          ? showAiChatDrawer(context)
          : context.push('/ai'),
      icon: const Icon(Icons.auto_awesome_outlined),
    );
  }
}
