import 'package:flutter/widgets.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../plugin_bridge.dart';
import 'plugin_frame_stub.dart'
    if (dart.library.io) 'plugin_frame_io.dart'
    if (dart.library.js_interop) 'plugin_frame_web.dart'
    as platform;

/// 在管理后台里承载插件页面：Web 用沙箱 iframe，Android 用 WebView。
/// [url] 是完整的页面地址，[bridge] 负责和页面交换令牌、主题和提示。
typedef PluginFrameBuilder = Widget Function(Uri url, PluginBridge bridge);

/// 测试里替换成不依赖平台视图的实现。
final pluginFrameProvider = Provider<PluginFrameBuilder>(
  (ref) => platform.buildPluginFrame,
);
