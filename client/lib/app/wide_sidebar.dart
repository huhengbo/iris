import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../core/api/api_error.dart';
import '../core/providers.dart';
import '../core/widgets/confirm_dialog.dart';
import '../core/widgets/snack.dart';
import '../features/admin/libraries_api.dart';
import '../features/admin/libraries_page.dart' show LibraryDialog;
import '../features/admin/library_advanced_api.dart';
import '../features/auth/auth_controller.dart';
import '../features/library/browse_api.dart';
import 'account_menu.dart';
import 'motion.dart';
import 'theme.dart';

/// 宽屏侧栏的宽度（Plex Web 的左栏）。首页、稍后观看和各资料库都从这里进。
const wideSidebarWidth = 240.0;

/// 宽屏左侧栏：首页、稍后观看、服务器名（分组标题）和当前用户可见的资料库。
/// 资料库行尾的「⋯」只有管理员有，操作和「管理后台 › 资料库」一样。
class WideSidebar extends ConsumerStatefulWidget {
  const WideSidebar({super.key, required this.currentPath});

  /// 地址里的路径，用来高亮当前所在的一项
  final String currentPath;

  @override
  ConsumerState<WideSidebar> createState() => WideSidebarState();
}

class WideSidebarState extends ConsumerState<WideSidebar> {
  /// 每一行自己的焦点节点：内容区最左边再按左键时，焦点要回到当前这一项
  final Map<String, FocusNode> _nodes = {};

  FocusNode _node(String id) =>
      _nodes.putIfAbsent(id, () => FocusNode(debugLabel: id));

  @override
  void dispose() {
    for (final node in _nodes.values) {
      node.dispose();
    }
    super.dispose();
  }

  /// 当前高亮那一项的键：手机上的「搜索」「我的」在宽屏没有侧栏项，返回 null。
  String? get _selectedKey {
    final path = widget.currentPath;
    if (path == '/') return 'home';
    if (path == '/watchlist') return 'watchlist';
    if (_libraryIdIn(path) case final id?) return 'library:$id';
    return null;
  }

  /// 内容区最左边再按左键时把焦点交给当前这一项，没有就交给第一项。
  void focusSelected() {
    final node = _nodes[_selectedKey] ?? _nodes.values.firstOrNull;
    node?.requestFocus();
  }

  @override
  Widget build(BuildContext context) {
    final path = widget.currentPath;
    final libraries = ref.watch(librariesProvider);
    final isAdmin = ref.watch(authControllerProvider).value?.isAdmin == true;
    final server = currentServerName(ref);
    final currentId = _libraryIdIn(path);
    return Container(
      width: wideSidebarWidth,
      color: CinemaColors.surface1,
      child: SafeArea(
        right: false,
        bottom: false,
        child: ListView(
          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 12),
          children: [
            _SideItem(
              focusNode: _node('home'),
              icon: Icons.home_outlined,
              selectedIcon: Icons.home,
              label: '首页',
              selected: path == '/',
              onTap: () => context.go('/'),
            ),
            _SideItem(
              focusNode: _node('watchlist'),
              icon: Icons.bookmark_border,
              selectedIcon: Icons.bookmark,
              label: '稍后观看',
              selected: path == '/watchlist',
              onTap: () => context.go('/watchlist'),
            ),
            _ServerTitle(server),
            // 刷新时保留上一次的列表，不闪回「正在加载」（AsyncLoading 也可能带着旧值）
            ...switch (libraries) {
              AsyncValue(hasValue: true, :final value?) => [
                for (final library in value)
                  _LibraryRow(
                    library: library,
                    selected: currentId == library.id,
                    isAdmin: isAdmin,
                    focusNode: _node('library:${library.id}'),
                  ),
                if (value.isEmpty) const _SideHint('没有可见的资料库'),
              ],
              AsyncError(:final error) => [
                _SideError(
                  message: ApiError.from(error).message,
                  onRetry: () => ref.invalidate(librariesProvider),
                ),
              ],
              _ => const [_SideLoading()],
            },
          ],
        ),
      ),
    );
  }
}

/// 路径里的资料库 ID：海报墙在首页栏是 /library/:id，在资料库栏是 /libraries/:id。
int? _libraryIdIn(String path) {
  final match = RegExp(r'^/(?:library|libraries)/(\d+)').firstMatch(path);
  return match == null ? null : int.tryParse(match.group(1)!);
}

/// 侧栏里的一行。选中项是琥珀底 + 琥珀图标 + 加粗文字；焦点叠一层暖白提亮，
/// 和选中区分开（DESIGN.md「交互状态」）。
class _SideItem extends StatefulWidget {
  const _SideItem({
    required this.focusNode,
    required this.icon,
    required this.label,
    required this.selected,
    required this.onTap,
    this.selectedIcon,
    this.trailing,
  });

  final FocusNode focusNode;
  final IconData icon;
  final IconData? selectedIcon;
  final String label;
  final bool selected;
  final VoidCallback onTap;

  /// 行尾的操作按钮，管理员在资料库行上才有
  final Widget? trailing;

  @override
  State<_SideItem> createState() => _SideItemState();
}

class _SideItemState extends State<_SideItem> {
  bool _focused = false;

  @override
  Widget build(BuildContext context) {
    final selected = widget.selected;
    final foreground = selected
        ? CinemaColors.amber
        : _focused
        ? CinemaColors.text0
        : CinemaColors.text1;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: FocusableActionDetector(
        focusNode: widget.focusNode,
        onShowFocusHighlight: (value) {
          if (value != _focused) setState(() => _focused = value);
        },
        actions: {
          ActivateIntent: CallbackAction<ActivateIntent>(
            onInvoke: (_) {
              widget.onTap();
              return null;
            },
          ),
        },
        child: Material(
          color: Colors.transparent,
          child: InkWell(
            borderRadius: BorderRadius.circular(CinemaRadius.control),
            onTap: widget.onTap,
            child: AnimatedContainer(
              duration: Motion.focus,
              padding: const EdgeInsets.fromLTRB(12, 9, 6, 9),
              decoration: BoxDecoration(
                color: _rowColor(selected: selected, focused: _focused),
                borderRadius: BorderRadius.circular(CinemaRadius.control),
              ),
              child: Row(
                children: [
                  Icon(
                    selected ? widget.selectedIcon ?? widget.icon : widget.icon,
                    size: 20,
                    color: foreground,
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Text(
                      widget.label,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: Theme.of(context).textTheme.bodyMedium!.copyWith(
                        color: foreground,
                        fontWeight: selected ? FontWeight.w700 : null,
                      ),
                    ),
                  ),
                  ?widget.trailing,
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  /// 行的底色。选中项本身就是琥珀淡底，焦点用暖白提亮叠上去。
  Color _rowColor({required bool selected, required bool focused}) {
    final base = selected ? CinemaColors.amberSoft : Colors.transparent;
    if (!focused) return base;
    return Color.alphaBlend(
      CinemaColors.highlight,
      Color.alphaBlend(base, CinemaColors.surface1),
    );
  }
}

/// 服务器名，作为资料库列表的分组标题；服务端信息还没回来时不占位置。
class _ServerTitle extends StatelessWidget {
  const _ServerTitle(this.name);

  final String? name;

  @override
  Widget build(BuildContext context) {
    final name = this.name;
    if (name == null || name.isEmpty) return const SizedBox(height: 8);
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, CinemaSpacing.section - 8, 12, 6),
      child: Semantics(
        header: true,
        child: Text(
          name,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: Theme.of(context).textTheme.labelLarge!
              .copyWith(color: CinemaColors.text2),
        ),
      ),
    );
  }
}

/// 资料库列表加载中：侧栏里只占一行，不用骨架屏。
class _SideLoading extends StatelessWidget {
  const _SideLoading();

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(12, 12, 12, 12),
    child: Row(
      children: [
        const SizedBox.square(
          dimension: 16,
          child: CircularProgressIndicator(strokeWidth: 2),
        ),
        const SizedBox(width: 12),
        Text(
          '正在加载资料库…',
          style: Theme.of(context).textTheme.bodySmall!
              .copyWith(color: CinemaColors.text2),
        ),
      ],
    ),
  );
}

/// 资料库列表加载失败：一行说明加「重试」，不占满整个侧栏。
class _SideError extends StatelessWidget {
  const _SideError({required this.message, required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(12, 4, 0, 4),
    child: Row(
      children: [
        Expanded(
          child: Text(
            message,
            maxLines: 3,
            overflow: TextOverflow.ellipsis,
            style: Theme.of(context).textTheme.bodySmall!
                .copyWith(color: CinemaColors.error),
          ),
        ),
        TextButton(onPressed: onRetry, child: const Text('重试')),
      ],
    ),
  );
}

class _SideHint extends StatelessWidget {
  const _SideHint(this.message);

  final String message;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(12, 12, 12, 4),
    child: Text(
      message,
      style: Theme.of(context).textTheme.bodySmall!
          .copyWith(color: CinemaColors.text2),
    ),
  );
}

/// 一个资料库：点击进海报墙，管理员行尾多一个「⋯」。
class _LibraryRow extends ConsumerStatefulWidget {
  const _LibraryRow({
    required this.library,
    required this.selected,
    required this.isAdmin,
    required this.focusNode,
  });

  final LibrarySummary library;
  final bool selected;
  final bool isAdmin;
  final FocusNode focusNode;

  @override
  ConsumerState<_LibraryRow> createState() => _LibraryRowState();
}

enum _LibraryAction { sync, refresh, edit, recommendations, delete }

class _LibraryRowState extends ConsumerState<_LibraryRow> {
  Future<void> _run(_LibraryAction action) async {
    final library = widget.library;
    final dio = ref.read(apiClientProvider);
    final router = GoRouter.maybeOf(context);
    try {
      switch (action) {
        case _LibraryAction.sync:
          await startSync(dio, libraryId: library.id);
          if (!mounted) return;
          showSnack(
            context,
            '已开始同步「${library.name}」',
            action: router == null
                ? null
                : SnackBarAction(
                    label: '查看',
                    onPressed: () => router.push('/settings/status/sync'),
                  ),
          );
        case _LibraryAction.refresh:
          await refreshLibraryMetadata(dio, library.id);
          if (!mounted) return;
          showSnack(context, '已开始刷新，进度见「后台任务」');
        case _LibraryAction.edit:
          await _edit();
        case _LibraryAction.recommendations:
          if (router != null) {
            router.push(
              '/settings/manage/libraries/${library.id}/recommendations'
              '?name=${Uri.encodeComponent(library.name)}',
            );
          }
        case _LibraryAction.delete:
          await _delete();
      }
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    }
  }

  /// 编辑对话框要整份资料库（目录、下载器），先按 ID 取管理端的列表。
  Future<void> _edit() async {
    final library = (await ref.read(adminLibrariesProvider.future))
        .where((item) => item.id == widget.library.id)
        .firstOrNull;
    if (!mounted) return;
    if (library == null) {
      showSnack(context, '资料库已不存在，刷新后再试');
      return;
    }
    final saved = await showDialog<bool>(
      context: context,
      builder: (_) => LibraryDialog(library: library),
    );
    if (saved != true) return;
    ref.invalidate(adminLibrariesProvider);
    ref.invalidate(librariesProvider);
    // 改了名字时推荐行的标题也跟着变
    ref.invalidate(libraryHubsProvider(library.id));
  }

  Future<void> _delete() async {
    final library = widget.library;
    final confirmed = await showConfirmDialog(
      context,
      title: '删除资料库「${library.name}」？',
      body: '资料库和它的文件记录会从本系统删除，存储上的文件不受影响。',
      action: '删除',
      destructive: true,
    );
    if (!confirmed || !mounted) return;
    await deleteLibrary(ref.read(apiClientProvider), library.id);
    ref.invalidate(adminLibrariesProvider);
    ref.invalidate(librariesProvider);
    if (mounted) showSnack(context, '已删除资料库「${library.name}」');
  }

  @override
  Widget build(BuildContext context) {
    final library = widget.library;
    return _SideItem(
      focusNode: widget.focusNode,
      icon: library.kind == MediaKind.series
          ? Icons.tv_outlined
          : Icons.movie_outlined,
      label: library.name,
      selected: widget.selected,
      onTap: () => context.go('/libraries/${library.id}'),
      trailing: widget.isAdmin
          ? PopupMenuButton<_LibraryAction>(
              tooltip: '管理 ${library.name}',
              icon: const Icon(Icons.more_horiz, size: 18),
              iconColor: CinemaColors.text2,
              padding: EdgeInsets.zero,
              splashRadius: 16,
              style: IconButton.styleFrom(
                minimumSize: const Size(32, 32),
                padding: EdgeInsets.zero,
                tapTargetSize: MaterialTapTargetSize.shrinkWrap,
              ),
              onSelected: _run,
              itemBuilder: (_) => const [
                PopupMenuItem(
                  value: _LibraryAction.sync,
                  child: Text('同步此资料库'),
                ),
                PopupMenuItem(
                  value: _LibraryAction.refresh,
                  child: Text('刷新元数据'),
                ),
                PopupMenuItem(value: _LibraryAction.edit, child: Text('编辑资料库')),
                PopupMenuItem(
                  value: _LibraryAction.recommendations,
                  child: Text('管理推荐'),
                ),
                PopupMenuItem(value: _LibraryAction.delete, child: Text('删除')),
              ],
            )
          : null,
    );
  }
}
