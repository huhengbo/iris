import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/widgets/snack.dart';
import '../library/browse_api.dart';
import 'watchlist_api.dart';

/// 详情页操作行里的「稍后观看」按钮：44 圆形 tonal，空心/实心书签。
class WatchlistToggleButton extends StatelessWidget {
  const WatchlistToggleButton({
    super.key,
    required this.watchlisted,
    required this.busy,
    required this.onPressed,
    this.autofocus = false,
  });

  final bool watchlisted;
  final bool busy;
  final VoidCallback onPressed;
  final bool autofocus;

  @override
  Widget build(BuildContext context) {
    return IconButton.filledTonal(
      autofocus: autofocus,
      tooltip: watchlisted ? '移出稍后观看' : '稍后观看',
      isSelected: watchlisted,
      iconSize: 24,
      style: IconButton.styleFrom(fixedSize: const Size.square(44)),
      // 忙碌时不禁用：禁用的按钮会丢掉遥控器焦点；busy 会挡住重复点击
      onPressed: busy ? () {} : onPressed,
      icon: const Icon(Icons.bookmark_border),
      selectedIcon: const Icon(Icons.bookmark, color: CinemaColors.amber),
    );
  }
}

/// 「稍后观看」的交互：先按点击结果乐观显示，请求失败时回滚并提示；
/// 成功后刷新详情、首页和稍后观看页，让各处看到同一份列表。
class WatchlistButton extends ConsumerStatefulWidget {
  const WatchlistButton({
    super.key,
    required this.id,
    required this.watchlisted,
    this.autofocus = false,
  });

  /// 电影或剧集的 ID
  final int id;
  final bool watchlisted;
  final bool autofocus;

  @override
  ConsumerState<WatchlistButton> createState() => _WatchlistButtonState();
}

class _WatchlistButtonState extends ConsumerState<WatchlistButton> {
  /// 点击后的目标值。等服务端数据追上来（详情刷新完成）再清掉，
  /// 避免刷新前后闪回旧状态。
  bool? _pending;
  bool _busy = false;

  bool get _watchlisted => _pending ?? widget.watchlisted;

  @override
  void didUpdateWidget(covariant WatchlistButton oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (_pending != null && widget.watchlisted == _pending) _pending = null;
  }

  Future<void> _toggle() async {
    if (_busy) return;
    final next = !_watchlisted;
    setState(() {
      _pending = next;
      _busy = true;
    });
    try {
      await ref
          .read(watchlistApiProvider)
          .setWatchlisted(widget.id, watchlisted: next);
      if (!mounted) return;
      // 首页的稍后观看行、列表页的筛选和详情里的状态都可能受影响
      ref
        ..invalidate(homeFeedProvider)
        ..invalidate(watchlistProvider)
        ..invalidate(libraryItemsProvider)
        ..invalidate(mediaDetailProvider(widget.id));
    } catch (error) {
      if (!mounted) return;
      setState(() => _pending = null);
      showApiErrorSnack(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return WatchlistToggleButton(
      watchlisted: _watchlisted,
      busy: _busy,
      autofocus: widget.autofocus,
      onPressed: _toggle,
    );
  }
}
