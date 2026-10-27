import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/widgets/snack.dart';
import '../library/browse_api.dart';
import 'favorites_api.dart';

/// 详情页操作行里的收藏按钮：44 圆形 tonal，空心/实心心形。
class FavoriteToggleButton extends StatelessWidget {
  const FavoriteToggleButton({
    super.key,
    required this.favorite,
    required this.busy,
    required this.onPressed,
    this.autofocus = false,
  });

  final bool favorite;
  final bool busy;
  final VoidCallback onPressed;
  final bool autofocus;

  @override
  Widget build(BuildContext context) {
    return IconButton.filledTonal(
      autofocus: autofocus,
      tooltip: favorite ? '取消收藏' : '收藏',
      isSelected: favorite,
      iconSize: 24,
      style: IconButton.styleFrom(fixedSize: const Size.square(44)),
      // 忙碌时不禁用：禁用的按钮会丢掉遥控器焦点；_busy 会挡住重复点击
      onPressed: busy ? () {} : onPressed,
      icon: const Icon(Icons.favorite_border),
      selectedIcon: const Icon(Icons.favorite, color: CinemaColors.amber),
    );
  }
}

/// 收藏按钮的交互：先按点击结果乐观显示，请求失败时回滚并提示；
/// 成功后刷新详情、首页、收藏页和海报墙，让各处看到同一份收藏。
class FavoriteButton extends ConsumerStatefulWidget {
  const FavoriteButton({
    super.key,
    required this.kind,
    required this.id,
    required this.favorite,
    this.itemId,
    this.seasonId,
    this.personId,
    this.autofocus = false,
  });

  final FavoriteKind kind;

  /// 收藏目标自己的 ID：电影/剧集、单集或人物
  final int id;
  final bool favorite;

  /// 单集所属的剧集，收藏单集后要刷新剧集详情
  final int? itemId;

  /// 单集所属的季，收藏单集后要刷新分集列表
  final int? seasonId;

  /// 人物页自己的 ID
  final int? personId;
  final bool autofocus;

  @override
  ConsumerState<FavoriteButton> createState() => _FavoriteButtonState();
}

class _FavoriteButtonState extends ConsumerState<FavoriteButton> {
  /// 点击后的目标值。等服务端数据追上来（列表刷新完成）再清掉，
  /// 避免刷新前后闪回旧状态。
  bool? _pending;
  bool _busy = false;

  bool get _favorite => _pending ?? widget.favorite;

  @override
  void didUpdateWidget(covariant FavoriteButton oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (_pending != null && widget.favorite == _pending) _pending = null;
  }

  Future<void> _toggle() async {
    if (_busy) return;
    final next = !_favorite;
    setState(() {
      _pending = next;
      _busy = true;
    });
    try {
      await ref
          .read(favoritesApiProvider)
          .setFavorite(widget.kind, widget.id, favorite: next);
      if (!mounted) return;
      _invalidate();
    } catch (error) {
      if (!mounted) return;
      setState(() => _pending = null);
      showApiErrorSnack(context, error);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _invalidate() {
    // 首页的「我的收藏」、收藏页和影视墙的已收藏筛选都可能受影响
    ref
      ..invalidate(homeFeedProvider)
      ..invalidate(favoritesProvider)
      ..invalidate(libraryItemsProvider);
    switch (widget.kind) {
      case FavoriteKind.movie || FavoriteKind.series:
        ref.invalidate(mediaDetailProvider(widget.itemId ?? widget.id));
      case FavoriteKind.episode:
        if (widget.itemId case final itemId?) {
          ref.invalidate(mediaDetailProvider(itemId));
        }
        if (widget.seasonId case final seasonId?) {
          ref.invalidate(seasonProvider(seasonId));
        }
      case FavoriteKind.person:
        ref.invalidate(personProvider(widget.personId ?? widget.id));
    }
  }

  @override
  Widget build(BuildContext context) {
    return FavoriteToggleButton(
      favorite: _favorite,
      busy: _busy,
      autofocus: widget.autofocus,
      onPressed: _toggle,
    );
  }
}
