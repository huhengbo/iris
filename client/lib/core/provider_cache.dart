import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

/// 保留最近一次结果一小段时间，让页面返回时直接显示旧内容，后台再刷新。
void keepProviderAlive(Ref ref, {Duration ttl = const Duration(minutes: 5)}) {
  final link = ref.keepAlive();
  final timer = Timer(ttl, link.close);
  ref.onDispose(timer.cancel);
  // 缓存期内重新进入页面：第一帧显示旧内容，同时后台静默刷新（刷新期间保留旧值，不出骨架）
  // 生命周期回调里不能改 provider，放到微任务里再失效
  ref.onResume(() {
    scheduleMicrotask(() {
      if (ref.mounted) ref.invalidateSelf();
    });
  });
}
