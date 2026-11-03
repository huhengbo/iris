import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import '../../core/server.dart';

/// 当前服务器的 AI 入口是否可用，取自 `/api/system/info`。读取失败时按不可用处理，
/// 不让 AI 的问题影响其他页面。管理员改了 AI 配置后 `ref.invalidate(serverInfoProvider)`。
final aiAvailabilityProvider = Provider<AiAvailability>(
  (ref) =>
      ref.watch(serverInfoProvider).asData?.value.ai ?? const AiAvailability(),
);
