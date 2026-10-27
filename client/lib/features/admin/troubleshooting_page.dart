import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/section_title.dart';
import 'admin_widgets.dart';

/// 「管理 › 故障排查」：一页三个入口，分别打开日志、系统健康和备份与恢复。
/// 原来的日志、系统健康、备份与恢复三个页面原样复用，各自处理加载、空和错误状态。
class TroubleshootingPage extends StatelessWidget {
  const TroubleshootingPage({super.key});

  static const _entries = [
    (
      title: '系统健康',
      message: '数据库、目录和外部依赖的检查结果',
      icon: Icons.health_and_safety_outlined,
      path: '/settings/manage/troubleshooting/health',
    ),
    (
      title: '日志',
      message: '服务进程最近的运行日志，新的在前',
      icon: Icons.article_outlined,
      path: '/settings/manage/troubleshooting/logs',
    ),
    (
      title: '备份与恢复',
      message: '创建、恢复数据库备份，导出不含密钥的安全配置',
      icon: Icons.backup_outlined,
      path: '/settings/manage/troubleshooting/backups',
    ),
  ];

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('故障排查')),
      body: AdminListWidth(
        top: 8,
        builder: (padding) => ListView(
          padding: padding,
          children: [
            const SectionTitle('故障排查', first: true),
            for (final entry in _entries)
              FocusRing(
                child: ListTile(
                  contentPadding: const EdgeInsets.symmetric(
                    horizontal: CinemaSpacing.gutter,
                  ),
                  leading: Icon(entry.icon),
                  title: Text(entry.title),
                  subtitle: Text(entry.message),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => context.push(entry.path),
                ),
              ),
          ],
        ),
      ),
    );
  }
}
