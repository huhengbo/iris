import 'dart:convert';

import 'package:flutter/widgets.dart';
import 'package:webview_flutter/webview_flutter.dart';

import '../../../app/theme.dart';
import '../plugin_bridge.dart';

Widget buildPluginFrame(Uri url, PluginBridge bridge) {
  if (WebViewPlatform.instance == null) {
    return const Center(child: Text('当前平台不支持插件页面，请在网页端或手机上配置'));
  }
  return _WebViewPluginFrame(key: ValueKey(url), url: url, bridge: bridge);
}

/// 页面只能停留在这个插件的 ui 目录里，跳去别处时直接拦下。
String pluginPagePrefix(Uri page) {
  final path = page.path;
  return '${page.origin}${path.substring(0, path.indexOf('/ui/') + 4)}';
}

class _WebViewPluginFrame extends StatefulWidget {
  const _WebViewPluginFrame({
    super.key,
    required this.url,
    required this.bridge,
  });

  final Uri url;
  final PluginBridge bridge;

  @override
  State<_WebViewPluginFrame> createState() => _WebViewPluginFrameState();
}

class _WebViewPluginFrameState extends State<_WebViewPluginFrame> {
  late final WebViewController _controller;

  @override
  void initState() {
    super.initState();
    final allowed = pluginPagePrefix(widget.url);
    _controller = WebViewController()
      ..setJavaScriptMode(JavaScriptMode.unrestricted)
      ..setBackgroundColor(CinemaColors.bg)
      ..addJavaScriptChannel(
        'IrisHost',
        onMessageReceived: (message) => widget.bridge.handle(message.message),
      )
      ..setNavigationDelegate(
        NavigationDelegate(
          onNavigationRequest: (request) => request.url.startsWith(allowed)
              ? NavigationDecision.navigate
              : NavigationDecision.prevent,
        ),
      )
      ..loadRequest(widget.url);
    widget.bridge.attach((message) {
      // Android 上页面是顶层窗口，SDK 认 event.source === window.parent（即自身）
      _controller.runJavaScript(
        'window.postMessage(${jsonEncode(message)}, "*");',
      );
    });
  }

  @override
  void dispose() {
    widget.bridge.detach();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => WebViewWidget(controller: _controller);
}
