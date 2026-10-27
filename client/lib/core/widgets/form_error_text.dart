import 'package:flutter/material.dart';

/// 表单下方的整体错误（服务端拒绝、登录失败等）：暖红图标 + 小字，和输入框
/// 下方的校验说明同一级，读屏会念出来。登录页、密码和切换用户对话框都用它。
class FormErrorText extends StatelessWidget {
  const FormErrorText(this.message, {super.key});

  final String message;

  @override
  Widget build(BuildContext context) {
    final error = Theme.of(context).colorScheme.error;
    return Semantics(
      liveRegion: true,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.error_outline, size: 18, color: error),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              message,
              style: Theme.of(context).textTheme.bodySmall!
                  .copyWith(color: error),
            ),
          ),
        ],
      ),
    );
  }
}
