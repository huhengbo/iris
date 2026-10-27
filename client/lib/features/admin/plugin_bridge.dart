import 'dart:convert';

import 'package:flutter/painting.dart';

import '../../app/theme.dart';

/// 插件页面和管理后台之间的消息协议，与后端 app/plugins/static/iris-plugin.js 对应。
///
/// 页面 → 宿主：iris:ready、iris:toast {message}、iris:refresh-token {requestId}
/// 宿主 → 页面：iris:init {pluginId, token, theme, locale}、iris:token {requestId, token}
///
/// 插件页面不受信任：消息只按白名单处理，令牌只发给这个插件自己的会话。
class PluginBridge {
  PluginBridge({
    required this.pluginId,
    required this.issueToken,
    required this.onToast,
    required this.onError,
  });

  static const toastMaxLength = 200;

  final String pluginId;

  /// 向后端申请这个插件的新令牌。
  final Future<String> Function() issueToken;
  final void Function(String message) onToast;
  final void Function(Object error) onError;

  void Function(Map<String, Object?> message)? _send;

  /// 由承载页面的视图（iframe 或 WebView）提供发送通道。
  void attach(void Function(Map<String, Object?> message) send) {
    _send = send;
  }

  void detach() {
    _send = null;
  }

  /// 处理页面发来的消息；Web 端是对象，Android 端是 JSON 字符串。
  Future<void> handle(Object? raw) async {
    final message = _decode(raw);
    if (message == null) return;
    switch (message['type']) {
      case 'iris:ready':
        final token = await _token();
        if (token == null) return;
        _send?.call({
          'type': 'iris:init',
          'pluginId': pluginId,
          'token': token,
          'theme': pluginTheme(),
          'locale': 'zh-CN',
        });
      case 'iris:toast':
        final text = message['message'];
        if (text is! String || text.trim().isEmpty) return;
        final trimmed = text.trim();
        onToast(
          trimmed.length > toastMaxLength
              ? '${trimmed.substring(0, toastMaxLength)}…'
              : trimmed,
        );
      case 'iris:refresh-token':
        final requestId = message['requestId'];
        if (requestId is! String || requestId.length > 64) return;
        final token = await _token();
        if (token == null) return;
        _send?.call({
          'type': 'iris:token',
          'requestId': requestId,
          'token': token,
        });
    }
  }

  Future<String?> _token() async {
    try {
      return await issueToken();
    } catch (error) {
      onError(error);
      return null;
    }
  }

  static Map<String, Object?>? _decode(Object? raw) {
    Object? value = raw;
    if (value is String) {
      try {
        value = jsonDecode(value);
      } on FormatException {
        return null;
      }
    }
    if (value is! Map) return null;
    final type = value['type'];
    if (type is! String) return null;
    return {
      for (final entry in value.entries)
        if (entry.key is String) entry.key as String: entry.value,
    };
  }
}

/// 页面 CSS 变量（--iris-*）用的主题色，与 iris-plugin.css 的变量名一致。
Map<String, String> pluginTheme() => {
  'bg': cssColor(CinemaColors.bg),
  'surface': cssColor(CinemaColors.surface1),
  'surface-high': cssColor(CinemaColors.surface2),
  'text': cssColor(CinemaColors.text0),
  'text-muted': cssColor(CinemaColors.text2),
  'accent': cssColor(CinemaColors.amber),
  'error': cssColor(CinemaColors.error),
  'success': cssColor(CinemaColors.success),
  'border': cssColor(CinemaColors.outline),
};

String cssColor(Color color) {
  int channel(double value) => (value * 255).round().clamp(0, 255);
  final alpha = color.a.toStringAsFixed(3).replaceFirst(RegExp(r'\.?0+$'), '');
  return 'rgba(${channel(color.r)}, ${channel(color.g)}, ${channel(color.b)}, $alpha)';
}
