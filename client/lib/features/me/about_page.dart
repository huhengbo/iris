import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:package_info_plus/package_info_plus.dart';

import '../../app/launch_splash.dart';
import '../../app/theme.dart';

/// 客户端版本，如「0.1.0 (3)」；括号里是构建号，没有时只显示版本号。
final appVersionProvider = FutureProvider<String>((ref) async {
  final info = await PackageInfo.fromPlatform();
  return info.buildNumber.isEmpty
      ? info.version
      : '${info.version} (${info.buildNumber})';
});

/// 关于：目前只有标志和版本号，其他内容后续补充。
class AboutPage extends ConsumerWidget {
  const AboutPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final textTheme = Theme.of(context).textTheme;
    final version = ref.watch(appVersionProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('关于')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(
          CinemaSpacing.gutter,
          32,
          CinemaSpacing.gutter,
          32,
        ),
        children: [
          const Center(child: IrisMark(dimension: 64)),
          const SizedBox(height: 16),
          Text(
            '光圈',
            textAlign: TextAlign.center,
            style: textTheme.headlineMedium,
          ),
          const SizedBox(height: 4),
          Text(
            '家里的片库，点开就看',
            textAlign: TextAlign.center,
            style: textTheme.bodyMedium!.copyWith(color: CinemaColors.text2),
          ),
          const SizedBox(height: 24),
          Text(
            switch (version) {
              AsyncData(:final value) => '版本 $value',
              AsyncError() => '版本未知',
              _ => '',
            },
            textAlign: TextAlign.center,
            style: textTheme.bodySmall!.copyWith(color: CinemaColors.text2),
          ),
        ],
      ),
    );
  }
}
