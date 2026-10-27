import 'package:flutter/material.dart';

import 'focus_ring.dart';

/// 项目内统一的下拉表单：保留 label/helper 语义，并让键盘、遥控器和触控
/// 入口都有一致的焦点环与最小触控高度。
class AppDropdownField<T> extends StatelessWidget {
  const AppDropdownField({
    super.key,
    required this.label,
    required this.value,
    required this.items,
    required this.onChanged,
    this.helperText,
    this.isExpanded = true,
  });

  final String label;
  final T? value;
  final List<DropdownMenuItem<T>> items;
  final ValueChanged<T?>? onChanged;
  final String? helperText;
  final bool isExpanded;

  @override
  Widget build(BuildContext context) => FocusRing(
    child: Semantics(
      container: true,
      child: DropdownButtonFormField<T>(
        initialValue: value,
        isExpanded: isExpanded,
        decoration: InputDecoration(labelText: label, helperText: helperText),
        items: items,
        onChanged: onChanged,
      ),
    ),
  );
}
