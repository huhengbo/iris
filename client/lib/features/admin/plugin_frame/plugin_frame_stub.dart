import 'package:flutter/widgets.dart';

import '../plugin_bridge.dart';

Widget buildPluginFrame(Uri url, PluginBridge bridge) =>
    const Center(child: Text('当前平台不支持插件页面，请在网页端或手机上配置'));
