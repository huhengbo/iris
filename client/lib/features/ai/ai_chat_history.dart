import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/format.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/confirm_dialog.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/snack.dart';
import 'ai_chat_api.dart';
import 'ai_chat_controller.dart';
import 'ai_chat_models.dart';

enum _MenuAction { newChat, history }

/// 对话窗口右上角的菜单：新对话、历史对话。
class AiChatMenuButton extends ConsumerWidget {
  const AiChatMenuButton({super.key, this.initialQuestion});

  final String? initialQuestion;

  @override
  Widget build(BuildContext context, WidgetRef ref) =>
      PopupMenuButton<_MenuAction>(
        tooltip: '更多',
        icon: const Icon(Icons.more_vert),
        onSelected: (action) {
          switch (action) {
            case _MenuAction.newChat:
              ref
                  .read(aiChatControllerProvider(initialQuestion).notifier)
                  .newChat();
            case _MenuAction.history:
              showAiHistory(context, initialQuestion);
          }
        },
        itemBuilder: (context) => const [
          PopupMenuItem(
            value: _MenuAction.newChat,
            child: ListTile(
              leading: Icon(Icons.add_comment_outlined),
              title: Text('新对话'),
              contentPadding: EdgeInsets.zero,
            ),
          ),
          PopupMenuItem(
            value: _MenuAction.history,
            child: ListTile(
              leading: Icon(Icons.history),
              title: Text('历史对话'),
              contentPadding: EdgeInsets.zero,
            ),
          ),
        ],
      );
}

/// 历史对话列表（底部面板）：点一行切换过去，右侧可删除（先确认）。
Future<void> showAiHistory(BuildContext context, String? initialQuestion) =>
    showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      constraints: BoxConstraints(
        maxHeight: MediaQuery.sizeOf(context).height * 0.7,
        maxWidth: 560,
      ),
      builder: (_) => _HistorySheet(initialQuestion: initialQuestion),
    );

class _HistorySheet extends ConsumerWidget {
  const _HistorySheet({this.initialQuestion});

  final String? initialQuestion;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final chats = ref.watch(aiChatListProvider);
    final current = ref.watch(aiChatControllerProvider(initialQuestion)).chatId;
    return SafeArea(
      child: AsyncView<List<AiChatSummary>>(
        value: chats,
        onRetry: () => ref.invalidate(aiChatListProvider),
        builder: (list) => list.isEmpty
            ? const SizedBox(
                height: 220,
                child: EmptyState(
                  icon: Icons.history,
                  title: '还没有历史对话',
                  message: '提问之后，对话会保存在这里',
                ),
              )
            : ListView.builder(
                shrinkWrap: true,
                itemCount: list.length,
                itemBuilder: (context, i) => _HistoryTile(
                  chat: list[i],
                  selected: list[i].id == current,
                  initialQuestion: initialQuestion,
                ),
              ),
      ),
    );
  }
}

class _HistoryTile extends ConsumerWidget {
  const _HistoryTile({
    required this.chat,
    required this.selected,
    required this.initialQuestion,
  });

  final AiChatSummary chat;
  final bool selected;
  final String? initialQuestion;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final title = chat.title.trim().isEmpty ? '新对话' : chat.title;
    return ListTile(
      selected: selected,
      selectedTileColor: CinemaColors.amberSoft,
      title: Text(title, maxLines: 1, overflow: TextOverflow.ellipsis),
      subtitle: Text(formatIsoAgo(chat.updatedAt)),
      trailing: IconButton(
        tooltip: '删除',
        icon: const Icon(Icons.delete_outline),
        onPressed: () => _delete(context, ref, title),
      ),
      onTap: () {
        Navigator.of(context).pop();
        ref
            .read(aiChatControllerProvider(initialQuestion).notifier)
            .openChat(chat.id);
      },
    );
  }

  Future<void> _delete(
    BuildContext context,
    WidgetRef ref,
    String title,
  ) async {
    final confirmed = await showConfirmDialog(
      context,
      title: '删除对话「$title」？',
      body: '删除后无法恢复。',
      action: '删除',
      destructive: true,
    );
    if (!confirmed || !context.mounted) return;
    try {
      await ref
          .read(aiChatControllerProvider(initialQuestion).notifier)
          .deleteChat(chat.id);
    } on Object catch (e) {
      if (context.mounted) showApiErrorSnack(context, e);
    }
  }
}
