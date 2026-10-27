import 'dart:js_interop';
import 'dart:ui_web' as ui_web;

import 'package:flutter/widgets.dart';
import 'package:web/web.dart' as web;

import '../plugin_bridge.dart';

Widget buildPluginFrame(Uri url, PluginBridge bridge) =>
    _WebPluginFrame(key: ValueKey(url), url: url, bridge: bridge);

int _nextViewId = 0;

class _WebPluginFrame extends StatefulWidget {
  const _WebPluginFrame({super.key, required this.url, required this.bridge});

  final Uri url;
  final PluginBridge bridge;

  @override
  State<_WebPluginFrame> createState() => _WebPluginFrameState();
}

class _WebPluginFrameState extends State<_WebPluginFrame> {
  late final String _viewType = 'iris-plugin-frame-${_nextViewId++}';
  late final web.HTMLIFrameElement _iframe;
  late final JSFunction _listener;

  @override
  void initState() {
    super.initState();
    _iframe = web.HTMLIFrameElement()
      // 不给 allow-same-origin：页面运行在不透明源，读不到管理后台的存储和令牌。
      // 服务端的 CSP sandbox 响应头是同样的限制，这里再加一层防止页面被单独打开时绕过
      ..setAttribute('sandbox', 'allow-scripts allow-forms')
      ..referrerPolicy = 'no-referrer'
      ..src = widget.url.toString();
    _iframe.style
      ..border = '0'
      ..width = '100%'
      ..height = '100%';
    ui_web.platformViewRegistry.registerViewFactory(
      _viewType,
      (int viewId) => _iframe,
    );
    _listener = ((web.MessageEvent event) {
      // 只处理来自这个 iframe 的消息
      final source = event.source;
      final frame = _iframe.contentWindow;
      if (source == null ||
          frame == null ||
          !source.strictEquals(frame).toDart) {
        return;
      }
      widget.bridge.handle(event.data.dartify());
    }).toJS;
    web.window.addEventListener('message', _listener);
    widget.bridge.attach((message) {
      // 沙箱页面是不透明源，无法指定目标源；消息只发给这个 iframe 的窗口
      _iframe.contentWindow?.postMessage(message.jsify(), '*'.toJS);
    });
  }

  @override
  void dispose() {
    widget.bridge.detach();
    web.window.removeEventListener('message', _listener);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => HtmlElementView(viewType: _viewType);
}
