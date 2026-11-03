import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/widgets/empty_state.dart';
import '../../core/widgets/section_title.dart';
import '../admin/admin_widgets.dart' show AdminListWidth;
import '../play/play_api.dart';
import '../update/update_controller.dart' show updateSupportedProvider;
import '../update/update_tiles.dart'
    show AutoCheckUpdateTile, DownloadPrefixTile;
import 'preferences_page.dart'
    show DefaultPlayerTile, HomeFavoritesTile, SubtitleLanguageTile;

/// 「本机 › 常规」：只影响这台设备的显示，不写服务器。
class LocalGeneralPage extends ConsumerWidget {
  const LocalGeneralPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return Scaffold(
      appBar: AppBar(title: const Text('常规')),
      body: AdminListWidth(
        bottom: 32,
        builder: (padding) => ListView(
          padding: padding,
          children: [
            const SectionTitle('首页', first: true),
            const HomeFavoritesTile(),
            // 应用内升级只在 Android 上有（ADR 0031）
            if (ref.watch(updateSupportedProvider)) ...const [
              SectionTitle('更新'),
              AutoCheckUpdateTile(),
              DownloadPrefixTile(),
              _Note('国内网络访问 GitHub 慢时填写加速前缀，检查更新和下载安装包都会经过它。'),
            ],
          ],
        ),
      ),
    );
  }
}

/// 「本机 › 播放器」：默认用哪个外部播放器。手机和平板上才有可选项。
class PlayerSettingsPage extends ConsumerWidget {
  const PlayerSettingsPage({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final players = ref
        .watch(playPlatformProvider)
        .players
        .where((p) => p.rememberable)
        .toList();
    return Scaffold(
      appBar: AppBar(title: const Text('播放器')),
      body: players.isEmpty
          ? const EmptyState(
              icon: Icons.play_circle_outline,
              title: '这个平台没有可选的播放器',
              message: '播放时直接用系统默认方式打开，不需要记默认播放器。',
            )
          : AdminListWidth(
              bottom: 32,
              builder: (padding) => ListView(
                padding: padding,
                children: const [
                  SectionTitle('播放', first: true),
                  DefaultPlayerTile(),
                  _Note('默认播放器只在这台设备上记住，换设备或换账号都不影响。'),
                ],
              ),
            ),
    );
  }
}

/// 「本机 › 字幕与音轨」：默认字幕语言。音轨跟随播放器里当次的选择。
class SubtitleSettingsPage extends StatelessWidget {
  const SubtitleSettingsPage({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('字幕与音轨')),
      body: AdminListWidth(
        bottom: 32,
        builder: (padding) => ListView(
          padding: padding,
          children: const [
            SectionTitle('字幕', first: true),
            SubtitleLanguageTile(),
            _Note('播放时默认优先选中这种语言的字幕；没有对应语言时用服务器或播放器的选择。音轨在播放器里当次选择。'),
          ],
        ),
      ),
    );
  }
}

/// 设置行下面的说明文字，和行标题左侧图标对齐。
class _Note extends StatelessWidget {
  const _Note(this.message);

  final String message;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(56, 4, CinemaSpacing.gutter, 8),
    child: Text(
      message,
      style: Theme.of(context).textTheme.bodySmall!
          .copyWith(color: CinemaColors.text2),
    ),
  );
}
