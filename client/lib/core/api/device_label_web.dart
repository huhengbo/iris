import 'package:web/web.dart' as web;

import 'user_agent.dart';

/// Web 端：设备名取浏览器和系统，平台记为 Web。
({String? name, String? platform}) deviceLabel() =>
    describeUserAgent(web.window.navigator.userAgent);
