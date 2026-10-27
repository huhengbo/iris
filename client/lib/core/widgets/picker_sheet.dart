import 'package:flutter/material.dart';

import '../../app/motion.dart';
import '../../app/shell.dart' show wideLayoutBreakpoint;
import '../../app/theme.dart';
import 'focus_ring.dart';

/// 选择类弹层：手机上是底部弹层（`emphasized` 上滑 320ms、收起 200ms），
/// 宽屏（≥ 840，包括 TV）是居中对话框，遥控器操作不适合底部弹层。
/// 播放器选择、选电视、版本和字幕、单选设置、加入播放列表都用它，
/// 内容用 [PickerHeader] + [PickerOption] 拼，见 DESIGN.md「弹层」。
Future<T?> showPickerSheet<T>(
  BuildContext context, {
  required WidgetBuilder builder,
}) {
  Widget body(BuildContext context) => SingleChildScrollView(
    child: Padding(
      padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
      child: builder(context),
    ),
  );
  if (MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint) {
    return showDialog<T>(
      context: context,
      builder: (context) => Dialog(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 420),
          child: Padding(
            padding: const EdgeInsets.only(top: 16),
            child: body(context),
          ),
        ),
      ),
    );
  }
  return showModalBottomSheet<T>(
    context: context,
    useSafeArea: true,
    isScrollControlled: true,
    sheetAnimationStyle: const AnimationStyle(
      duration: Motion.medium,
      reverseDuration: Motion.short,
      curve: Motion.emphasized,
    ),
    builder: body,
  );
}

/// 弹层头部：标题 + 一行副标题 + 关闭按钮。
class PickerHeader extends StatelessWidget {
  const PickerHeader({
    super.key,
    required this.title,
    this.subtitle,
    this.onClose,
  });

  final String title;
  final String? subtitle;

  /// 嵌在别的对话框里时只关掉面板；为空时关闭当前路由。
  final VoidCallback? onClose;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    return Padding(
      padding: const EdgeInsets.only(left: 4, bottom: 8),
      child: Row(
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Semantics(
                  header: true,
                  child: Text(
                    title,
                    style: textTheme.titleMedium!.copyWith(
                      color: CinemaColors.text0,
                    ),
                  ),
                ),
                if (subtitle case final subtitle?)
                  Text(
                    subtitle,
                    style: textTheme.bodySmall,
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
              ],
            ),
          ),
          IconButton(
            tooltip: '关闭',
            onPressed: onClose ?? () => Navigator.of(context).pop(),
            icon: const Icon(Icons.close),
          ),
        ],
      ),
    );
  }
}

/// 弹层里的一个选项：行高 52、圆角 12；选中 = surface3 底 + 琥珀勾（可带
/// 「默认」之类的小字），聚焦 = 琥珀环，两者在 TV 上分得清。
/// 有 [icon] 时左侧是 36 的圆角方块。
class PickerOption extends StatelessWidget {
  const PickerOption({
    super.key,
    required this.title,
    required this.onTap,
    this.subtitle,
    this.icon,
    this.selected = false,
    this.selectedLabel,
    this.autofocus = false,
    this.enabled = true,
  });

  final String title;
  final String? subtitle;
  final IconData? icon;
  final bool selected;

  /// 选中时勾前面的小字，如「默认」「当前」
  final String? selectedLabel;
  final bool autofocus;
  final bool enabled;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return FocusRing(
      child: ListTile(
        autofocus: autofocus,
        enabled: enabled,
        selected: selected,
        selectedColor: CinemaColors.text0,
        minTileHeight: 52,
        contentPadding: const EdgeInsets.symmetric(horizontal: 8),
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.all(Radius.circular(CinemaRadius.card)),
        ),
        tileColor: selected ? CinemaColors.surface3 : null,
        selectedTileColor: CinemaColors.surface3,
        leading: icon == null
            ? null
            : Container(
                width: 36,
                height: 36,
                decoration: BoxDecoration(
                  color: selected
                      ? CinemaColors.amberSoft
                      : CinemaColors.surface3,
                  borderRadius: BorderRadius.circular(CinemaRadius.small),
                ),
                child: Icon(
                  icon,
                  size: 20,
                  color: selected ? CinemaColors.amber : CinemaColors.text1,
                ),
              ),
        title: Text(
          title,
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
          style: TextStyle(
            color: selected ? CinemaColors.text0 : CinemaColors.text1,
            fontWeight: selected ? FontWeight.w700 : FontWeight.w500,
          ),
        ),
        subtitle: subtitle == null ? null : Text(subtitle!),
        trailing: selected
            ? Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  if (selectedLabel case final label?) ...[
                    Text(label, style: Theme.of(context).textTheme.bodySmall),
                    const SizedBox(width: 8),
                  ],
                  const Icon(Icons.check, color: CinemaColors.amber),
                ],
              )
            : null,
        onTap: onTap,
      ),
    );
  }
}

/// 一个单选项：值、标题和可选的副标题。
typedef Choice<T> = ({T value, String label, String? subtitle});

/// 单选：当前项带琥珀勾并获得默认焦点，没有当前项时焦点在第一项。返回记录，
/// 关掉弹层（null）和选了值为 null 的选项（如「每次询问」）区分得开。
Future<(T,)?> showChoicePicker<T>(
  BuildContext context, {
  required String title,
  String? subtitle,
  required List<Choice<T>> choices,
  required T? current,
}) {
  final hasCurrent = choices.any((c) => c.value == current);
  return showPickerSheet<(T,)>(
    context,
    builder: (context) => Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        PickerHeader(title: title, subtitle: subtitle),
        for (final (i, choice) in choices.indexed)
          PickerOption(
            title: choice.label,
            subtitle: choice.subtitle,
            selected: hasCurrent && choice.value == current,
            autofocus: hasCurrent ? choice.value == current : i == 0,
            onTap: () => Navigator.of(context).pop((choice.value,)),
          ),
      ],
    ),
  );
}
