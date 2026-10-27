import 'package:flutter/material.dart';

import '../api/api_error.dart';

/// 一类错误的统一呈现：标题、图标、颜色和建议动作。
class ApiErrorStyle {
  const ApiErrorStyle({
    required this.title,
    required this.icon,
    required this.color,
    required this.hint,
  });

  final String title;
  final IconData icon;
  final Color color;

  /// 建议用户下一步怎么做
  final String hint;
}

/// ApiErrorKind -> 呈现。switch 穷尽，新增枚举值不补映射无法编译。
/// 管理员可以去管理页处理的问题，给管理员和普通用户不同的说明。
ApiErrorStyle apiErrorStyle(
  ApiErrorKind kind,
  ColorScheme scheme, {
  bool isAdmin = false,
}) {
  final error = scheme.error;
  final warn = scheme.tertiary;
  return switch (kind) {
    ApiErrorKind.network => ApiErrorStyle(
      title: '网络不可用',
      icon: Icons.wifi_off_rounded,
      color: warn,
      hint: '请检查网络或服务器地址后重试',
    ),
    ApiErrorKind.unauthorized => ApiErrorStyle(
      title: '登录已失效',
      icon: Icons.lock_outline,
      color: error,
      hint: '请重新登录',
    ),
    ApiErrorKind.forbidden => ApiErrorStyle(
      title: '没有权限',
      icon: Icons.block_outlined,
      color: error,
      hint: isAdmin ? '当前账号无权执行此操作，请检查账号权限设置' : '请联系管理员开通权限',
    ),
    ApiErrorKind.rateLimited => ApiErrorStyle(
      title: '请求过于频繁',
      icon: Icons.hourglass_top_rounded,
      color: warn,
      hint: '请稍后再试',
    ),
    ApiErrorKind.resourceUnavailable => ApiErrorStyle(
      title: '资源来源不可用',
      icon: Icons.extension_off_outlined,
      color: error,
      hint: isAdmin ? '请到「设置 › 资源来源」检查插件是否运行、账号是否有效' : '请联系管理员检查资源来源',
    ),
    ApiErrorKind.missingFile => ApiErrorStyle(
      title: '文件不可用',
      icon: Icons.video_file_outlined,
      color: warn,
      hint: isAdmin ? '网盘中的文件可能已被移动或删除，请检查后重新同步' : '文件可能已被移动或删除，请联系管理员处理',
    ),
    ApiErrorKind.notFound => ApiErrorStyle(
      title: '内容不存在',
      icon: Icons.search_off_rounded,
      color: warn,
      hint: '内容可能已被删除，请刷新后重试',
    ),
    ApiErrorKind.conflict => ApiErrorStyle(
      title: '操作冲突',
      icon: Icons.sync_problem_rounded,
      color: warn,
      hint: '数据已发生变化或任务正在运行，请稍后刷新再试',
    ),
    ApiErrorKind.validation => ApiErrorStyle(
      title: '输入无效',
      icon: Icons.rule_rounded,
      color: warn,
      hint: '请检查填写内容后重试',
    ),
    ApiErrorKind.server => ApiErrorStyle(
      title: '服务器出错',
      icon: Icons.cloud_off_rounded,
      color: error,
      hint: isAdmin ? '请查看服务端日志，稍后重试' : '请稍后重试，持续出现请联系管理员',
    ),
    ApiErrorKind.unknown => ApiErrorStyle(
      title: '出错了',
      icon: Icons.error_outline,
      color: error,
      hint: '请重试',
    ),
  };
}
