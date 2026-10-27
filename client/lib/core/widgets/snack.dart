import 'package:flutter/material.dart';

import '../api/api_error.dart';

/// 操作结果的提示。先收起正在显示的一条，连续操作时不排队。
void showSnack(BuildContext context, String message, {SnackBarAction? action}) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(message), action: action));
}

/// 接口错误的统一提示：显示后端/客户端给出的错误信息。
void showApiErrorSnack(BuildContext context, Object error) =>
    showSnack(context, ApiError.from(error).message);
