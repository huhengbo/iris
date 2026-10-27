import 'package:flutter/material.dart';

/// 行尾操作进行中时代替图标按钮的小圈，和图标按钮占位一样大，行高不跳。
class BusyIcon extends StatelessWidget {
  const BusyIcon({super.key});

  @override
  Widget build(BuildContext context) => const Padding(
    padding: EdgeInsets.all(12),
    child: SizedBox.square(
      dimension: 20,
      child: CircularProgressIndicator(strokeWidth: 2),
    ),
  );
}
