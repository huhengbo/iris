import 'package:flutter/material.dart';

import '../../core/network_status.dart';

typedef CellularCheck = ({bool allowed, bool cellular});

/// 播放前检查一次当前网络；Wi-Fi 不打扰，移动数据必须明确确认。
Future<CellularCheck> checkCellularPlayback(
  BuildContext context,
  NetworkStatusSource source, {
  required bool duringPlayback,
}) async {
  final kind = await source.current().timeout(
    const Duration(milliseconds: 500),
    onTimeout: () => NetworkKind.other,
  );
  if (!context.mounted) return (allowed: false, cellular: false);
  if (!kind.isMobile) return (allowed: true, cellular: false);
  final allowed = await confirmCellularPlayback(
    context,
    duringPlayback: duringPlayback,
  );
  return (allowed: allowed, cellular: true);
}

/// 移动数据确认。花流量的一方不当默认焦点：遥控器或键盘连按确定时停在「取消」
/// （播放中切过来时是「保持暂停」）上，不会误耗流量。
Future<bool> confirmCellularPlayback(
  BuildContext context, {
  required bool duringPlayback,
}) async {
  final allowed = await showDialog<bool>(
    context: context,
    barrierDismissible: false,
    builder: (context) => AlertDialog(
      title: Text(duringPlayback ? '已切换到移动数据' : '正在使用移动数据'),
      content: Text(
        duringPlayback
            ? 'Wi-Fi 连接已断开，继续播放会消耗移动数据。'
            : '当前不是 Wi-Fi，播放视频可能消耗较多流量。要继续吗？',
      ),
      actions: [
        TextButton(
          autofocus: true,
          onPressed: () => Navigator.pop(context, false),
          child: Text(duringPlayback ? '保持暂停' : '取消'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(context, true),
          child: const Text('继续播放'),
        ),
      ],
    ),
  );
  return allowed ?? false;
}
