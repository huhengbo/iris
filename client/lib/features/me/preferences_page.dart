import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../app/theme.dart';
import '../../core/widgets/copy_row.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/picker_sheet.dart';
import '../../core/widgets/snack.dart';
import '../auth/auth_controller.dart';
import '../auth/password_dialog.dart';
import '../home/home_preferences.dart';
import '../play/play_api.dart';
import 'preferences_api.dart';
import 'webdav_api.dart';

/// 个人设置拆开后的共用行。第 ⑤ 批把「我的 › 设置」按
/// 账户（个人资料与密码 / 设备与会话）和本机（常规 / 播放器 / 字幕与音轨 / 关于）
/// 分成独立页面（me/settings_pages.dart、me/profile_page.dart），各页复用这里的行，
/// 业务逻辑不重写。

/// 首页是否显示「我的收藏」行，默认关闭。设置只存在本机，见 home_preferences.dart。
class HomeFavoritesTile extends ConsumerStatefulWidget {
  const HomeFavoritesTile({super.key});

  @override
  ConsumerState<HomeFavoritesTile> createState() => _HomeFavoritesTileState();
}

class _HomeFavoritesTileState extends ConsumerState<HomeFavoritesTile> {
  bool saving = false;

  Future<void> _toggle(bool enabled) async {
    if (saving) return;
    setState(() => saving = true);
    try {
      await ref.read(showHomeFavoritesProvider.notifier).set(enabled);
    } catch (_) {
      // 本机存储写不进去，开关已回到原来的状态
      if (mounted) showSnack(context, '保存失败，请稍后再试');
    } finally {
      if (mounted) setState(() => saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final snapshot = ref.watch(showHomeFavoritesProvider);
    final enabled = snapshot.value ?? false;
    return FocusRing(
      child: ListTile(
        leading: const Icon(Icons.favorite_border),
        title: const Text('首页显示我的收藏'),
        subtitle: const Text('打开后收藏行出现在稍后观看下面'),
        trailing: saving
            ? const SizedBox.square(
                dimension: 20,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            // 整行可点，开关本身不再单独抢焦点
            : ExcludeFocus(
                child: Switch(value: enabled, onChanged: _toggle),
              ),
        onTap: saving ? null : () => _toggle(!enabled),
      ),
    );
  }
}

/// 本机默认用哪个播放器。只有能调起播放器的平台才有这一项；设置只存在本机。
class DefaultPlayerTile extends ConsumerWidget {
  const DefaultPlayerTile({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final players = ref
        .watch(playPlatformProvider)
        .players
        .where((p) => p.rememberable)
        .toList();
    if (players.isEmpty) return const SizedBox.shrink();
    final current = ref.watch(defaultPlayerProvider).value;
    return FocusRing(
      child: ListTile(
        leading: const Icon(Icons.play_circle_outline),
        title: const Text('默认播放器'),
        subtitle: Text(current?.label ?? '每次询问'),
        trailing: const Icon(Icons.chevron_right),
        onTap: () async {
          final picked = await showChoicePicker<ExternalPlayer?>(
            context,
            title: '默认播放器',
            choices: [
              (value: null, label: '每次询问', subtitle: null),
              for (final p in players)
                (value: p, label: p.label, subtitle: null),
            ],
            current: current,
          );
          if (picked != null) {
            await ref.read(defaultPlayerProvider.notifier).set(picked.$1);
          }
        },
      ),
    );
  }
}

class SubtitleLanguageTile extends ConsumerWidget {
  const SubtitleLanguageTile({super.key});

  static const options = <String?, String>{
    null: '跟随服务器默认',
    'zh-CN': '简体中文',
    'zh-TW': '繁体中文',
    'en': '英语',
    'ja': '日语',
  };

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final snapshot = ref.watch(subtitleLanguageProvider);
    final loaded = snapshot.hasValue;
    return FocusRing(
      child: ListTile(
        leading: const Icon(Icons.subtitles_outlined),
        title: const Text('默认字幕语言'),
        subtitle: Text(
          loaded
              ? options[snapshot.value] ?? options[null]!
              : snapshot.hasError
              ? '暂时无法读取，点按重试'
              : '正在读取',
        ),
        trailing: const Icon(Icons.chevron_right),
        onTap: () async {
          if (!loaded) {
            ref.invalidate(subtitleLanguageProvider);
            return;
          }
          final picked = await showChoicePicker<String?>(
            context,
            title: '默认字幕语言',
            choices: [
              for (final MapEntry(:key, :value) in options.entries)
                (value: key, label: value, subtitle: null),
            ],
            current: snapshot.value,
          );
          if (picked == null || picked.$1 == snapshot.value) return;
          try {
            await ref
                .read(preferencesApiProvider)
                .setSubtitleLanguage(picked.$1);
            ref.invalidate(subtitleLanguageProvider);
          } catch (error) {
            if (context.mounted) {
              showApiErrorSnack(context, error);
            }
          }
        },
      ),
    );
  }
}

/// WebDAV 开关；开着时直接列出地址和用户名，可复制。
class WebDavSection extends ConsumerStatefulWidget {
  const WebDavSection({super.key});

  @override
  ConsumerState<WebDavSection> createState() => _WebDavSectionState();
}

class _WebDavSectionState extends ConsumerState<WebDavSection> {
  bool saving = false;

  Future<void> _toggle(bool enabled) async {
    if (saving) return;
    setState(() => saving = true);
    try {
      await ref.read(preferencesApiProvider).setWebDav(enabled: enabled);
      ref.invalidate(webDavProvider);
    } catch (error) {
      if (mounted) showApiErrorSnack(context, error);
    } finally {
      if (mounted) setState(() => saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final snapshot = ref.watch(webDavProvider);
    final info = snapshot.value;
    final loading = info == null && snapshot.isLoading;
    final textTheme = Theme.of(context).textTheme;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        FocusRing(
          child: ListTile(
            leading: const Icon(Icons.cloud_sync_outlined),
            title: const Text('WebDAV'),
            subtitle: Text(
              loading
                  ? '正在读取状态'
                  : info == null
                  ? '暂时无法读取状态'
                  : '在 Infuse 等播放器里直接浏览片库',
            ),
            trailing: loading
                ? const SizedBox.square(
                    dimension: 20,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : info == null
                ? IconButton(
                    tooltip: '重试',
                    onPressed: () => ref.invalidate(webDavProvider),
                    icon: const Icon(Icons.refresh),
                  )
                // 整行可点，开关本身不再单独抢焦点
                : ExcludeFocus(
                    child: Switch(
                      value: info.enabled,
                      onChanged: saving ? null : _toggle,
                    ),
                  ),
            onTap: info == null || saving ? null : () => _toggle(!info.enabled),
          ),
        ),
        if (info != null && info.enabled)
          Padding(
            // 和上面一行的标题对齐
            padding: const EdgeInsets.fromLTRB(56, 0, 12, 8),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                if (info.url case final url?)
                  CopyRow(label: '地址', value: url, tooltip: '复制地址')
                else
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 8),
                    child: Text(
                      '管理员还没有配置对外访问地址，暂时连不上',
                      style: textTheme.bodySmall!.copyWith(
                        color: CinemaColors.error,
                      ),
                    ),
                  ),
                CopyRow(label: '用户名', value: info.username, tooltip: '复制用户名'),
                const SizedBox(height: 4),
                Text('密码就是登录密码，修改登录密码后同步生效', style: textTheme.bodySmall),
              ],
            ),
          ),
      ],
    );
  }
}

/// 修改密码。账户页和设备页都会用到，单独放在这里。
class ChangePasswordTile extends ConsumerWidget {
  const ChangePasswordTile({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    return FocusRing(
      child: ListTile(
        leading: const Icon(Icons.lock_outline),
        title: const Text('修改密码'),
        trailing: const Icon(Icons.chevron_right),
        onTap: () async {
          final controller = ref.read(authControllerProvider.notifier);
          final changed = await showPasswordDialog(
            context,
            title: '修改密码',
            requireCurrent: true,
            onSubmit: (current, next) =>
                controller.changePassword(current!, next),
          );
          if (changed && context.mounted) {
            showSnack(context, '密码已修改，其他设备需要重新登录');
          }
        },
      ),
    );
  }
}
