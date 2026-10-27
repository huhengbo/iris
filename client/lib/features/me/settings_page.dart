import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/account_menu.dart' show currentServerName;
import '../../app/shell.dart' show wideLayoutBreakpoint;
import '../../app/theme.dart';
import '../../app/tv.dart' show televisionProvider;
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/section_title.dart';
import '../admin/activity_page.dart';
import '../admin/alerts_page.dart';
import '../admin/dashboard_page.dart';
import '../admin/items_page.dart';
import '../admin/libraries_page.dart';
import '../admin/maintenance_api.dart' show AdminAlert, activeAlertsProvider;
import '../admin/pending_page.dart';
import '../admin/plugins_page.dart';
import '../admin/recycle_bin_page.dart';
import '../admin/resources_page.dart';
import '../admin/schedules_page.dart';
import '../admin/settings_page.dart';
import '../admin/setup_page.dart';
import '../admin/summary_api.dart';
import '../admin/summary_hints.dart';
import '../admin/sync_page.dart';
import '../admin/tasks_page.dart';
import '../admin/troubleshooting_page.dart';
import '../admin/users_page.dart';
import '../admin/webhooks_page.dart';
import '../auth/auth_controller.dart';
import 'about_page.dart';
import 'devices_page.dart';
import 'profile_page.dart';
import 'settings_pages.dart';

/// 设置里的一项。宽屏在右栏打开，手机和 TV 是单独一页；
/// 页面自己的 AppBar 写标题，所以这里不再包一层。
class SettingsEntry {
  const SettingsEntry({
    required this.path,
    required this.title,
    required this.icon,
    required this.page,
  });

  final String path;
  final String title;
  final IconData icon;
  final Widget page;
}

/// 左栏的一组。管理员看到的服务器、管理、状态三组前面先画服务器名。
class SettingsMenuGroup {
  const SettingsMenuGroup(this.title, this.entries, {this.server = false});

  final String title;
  final List<SettingsEntry> entries;

  /// 服务器相关的第一组：组前面画「── 服务器「光圈」──」
  final bool server;
}

/// 配置向导：没配置完时出现在菜单最上面，完成后不再显示，但路由一直在
/// （服务端的 action_path 还会指过来，页面自己会说明已经完成）。
const setupSettingsEntry = SettingsEntry(
  path: '/settings/setup',
  title: '配置向导',
  icon: Icons.checklist_outlined,
  page: SetupPage(),
);

/// 账户：每个用户都有。
const _accountEntries = <SettingsEntry>[
  SettingsEntry(
    path: '/settings/account/profile',
    title: '个人资料与密码',
    icon: Icons.person_outline,
    page: AccountProfilePage(),
  ),
  SettingsEntry(
    path: '/settings/account/devices',
    title: '设备与会话',
    icon: Icons.devices_other,
    page: DevicesPage(),
  ),
];

/// 本机：只影响这台设备。
const _localEntries = <SettingsEntry>[
  SettingsEntry(
    path: '/settings/local/general',
    title: '常规',
    icon: Icons.tune,
    page: LocalGeneralPage(),
  ),
  SettingsEntry(
    path: '/settings/local/player',
    title: '播放器',
    icon: Icons.play_circle_outline,
    page: PlayerSettingsPage(),
  ),
  SettingsEntry(
    path: '/settings/local/subtitles',
    title: '字幕与音轨',
    icon: Icons.subtitles_outlined,
    page: SubtitleSettingsPage(),
  ),
  SettingsEntry(
    path: '/settings/local/about',
    title: '关于',
    icon: Icons.info_outline,
    page: AboutPage(),
  ),
];

/// 服务器：只有管理员能看到，名称在组标题上方单独画。
const _serverEntries = <SettingsEntry>[
  SettingsEntry(
    path: '/settings/server/general',
    title: '常规',
    icon: Icons.dns_outlined,
    page: ServerGeneralPage(),
  ),
  SettingsEntry(
    path: '/settings/server/remote-access',
    title: '远程访问',
    icon: Icons.public,
    page: RemoteAccessPage(),
  ),
  SettingsEntry(
    path: '/settings/server/libraries',
    title: '资料库',
    icon: Icons.video_library_outlined,
    page: LibraryDefaultsPage(),
  ),
  SettingsEntry(
    path: '/settings/server/network',
    title: '网络',
    icon: Icons.lan_outlined,
    page: PluginProxyPage(),
  ),
  SettingsEntry(
    path: '/settings/server/schedules',
    title: '计划任务',
    icon: Icons.schedule_outlined,
    page: SchedulesPage(),
  ),
  SettingsEntry(
    path: '/settings/server/webhooks',
    title: 'Webhook',
    icon: Icons.webhook_outlined,
    page: WebhooksPage(),
  ),
];

/// 管理：资料库、来源、用户、插件、待处理、条目、回收站和故障排查。
const _manageEntries = <SettingsEntry>[
  SettingsEntry(
    path: '/settings/manage/libraries',
    title: '资料库',
    icon: Icons.video_library_outlined,
    page: LibrariesPage(),
  ),
  SettingsEntry(
    path: '/settings/manage/sources',
    title: '资源来源',
    icon: Icons.storage_outlined,
    page: ResourcesPage(),
  ),
  SettingsEntry(
    path: '/settings/manage/users',
    title: '用户',
    icon: Icons.group_outlined,
    page: UsersPage(),
  ),
  SettingsEntry(
    path: '/settings/manage/plugins',
    title: '插件',
    icon: Icons.extension_outlined,
    page: PluginsPage(),
  ),
  SettingsEntry(
    path: '/settings/manage/pending',
    title: '待处理',
    icon: Icons.pending_actions_outlined,
    page: PendingPage(),
  ),
  SettingsEntry(
    path: '/settings/manage/items',
    title: '条目',
    icon: Icons.movie_outlined,
    page: ItemsPage(),
  ),
  SettingsEntry(
    path: '/settings/manage/recycle-bin',
    title: '回收站',
    icon: Icons.delete_outline,
    page: RecycleBinPage(),
  ),
  SettingsEntry(
    path: '/settings/manage/troubleshooting',
    title: '故障排查',
    icon: Icons.build_outlined,
    page: TroubleshootingPage(),
  ),
];

/// 状态：仪表盘、警报、同步、后台任务和播放历史。
const _statusEntries = <SettingsEntry>[
  SettingsEntry(
    path: '/settings/status/dashboard',
    title: '仪表盘',
    icon: Icons.dashboard_outlined,
    page: DashboardPage(),
  ),
  SettingsEntry(
    path: '/settings/status/alerts',
    title: '警报',
    icon: Icons.notifications_outlined,
    page: AlertsPage(),
  ),
  SettingsEntry(
    path: '/settings/status/sync',
    title: '同步',
    icon: Icons.sync,
    page: SyncPage(),
  ),
  SettingsEntry(
    path: '/settings/status/tasks',
    title: '后台任务',
    icon: Icons.list_alt_outlined,
    page: TasksPage(),
  ),
  SettingsEntry(
    path: '/settings/status/activity',
    title: '播放历史',
    icon: Icons.history,
    page: ActivityPage(),
  ),
];

/// 全部分组（管理员视角）。普通用户和 TV 只用前两组。
const settingsGroups = <SettingsMenuGroup>[
  SettingsMenuGroup('账户', _accountEntries),
  SettingsMenuGroup('本机', _localEntries),
  SettingsMenuGroup('设置', _serverEntries, server: true),
  SettingsMenuGroup('管理', _manageEntries),
  SettingsMenuGroup('状态', _statusEntries),
];

/// 当前角色能看到的分组：普通用户只有账户和本机；TV 同普通用户，不提供管理。
List<SettingsMenuGroup> settingsGroupsFor({required bool admin}) =>
    admin ? settingsGroups : settingsGroups.take(2).toList(growable: false);

/// 路由表用：所有设置项（含配置向导）。
List<SettingsEntry> allSettingsEntries() => [
  setupSettingsEntry,
  for (final group in settingsGroups) ...group.entries,
];

/// 地址对应的设置项；不是设置项时返回 null（详情页由路由直接打开）。
SettingsEntry? settingsEntryFor(String path) {
  for (final entry in allSettingsEntries()) {
    if (entry.path == path) return entry;
  }
  return null;
}

/// 「设置」：宽屏是左栏分组菜单加右栏内容的单页，手机和 TV 是分组列表逐层进入。
///
/// 宽屏打开某一项用 replace：设置整体只占一页，返回直接离开设置；
/// 手机用 push，返回回到分组列表。
class SettingsPage extends ConsumerWidget {
  const SettingsPage({super.key, this.location});

  /// 当前选中项的新地址；为空时是设置总览（手机显示分组列表）
  final String? location;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final tv = ref.watch(televisionProvider);
    final user = ref.watch(authControllerProvider).value;
    // TV 不提供管理和插件配置，即使登录的是管理员也只显示个人部分
    final admin = user?.isAdmin == true && !tv;
    final wide =
        !tv && MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint;
    final summary = admin ? ref.watch(adminSummaryProvider).value : null;
    final alerts = admin
        ? ref.watch(activeAlertsProvider).value ?? const <AdminAlert>[]
        : const <AdminAlert>[];
    final groups = settingsGroupsFor(admin: admin);
    final setupIncomplete = summary?.setupComplete == false;
    final dots = admin
        ? settingsEntryDots(summary, alerts)
        : const <String, Color>{};
    final current = location == null ? null : settingsEntryFor(location!);
    // 宽屏打开设置时默认选中账户，和右栏显示的个人资料页一致
    final selected = location ?? '/settings/account/profile';

    void open(SettingsEntry entry) {
      if (wide) {
        context.replace(entry.path);
      } else {
        context.push(entry.path);
      }
    }

    final menu = _SettingsMenu(
      groups: groups,
      // 手机的总览没有选中项；宽屏默认选中账户，和右栏显示的一致
      selected: wide ? selected : location,
      wide: wide,
      showSetup: setupIncomplete,
      dots: dots,
      serverName: admin ? currentServerName(ref) : null,
      onOpen: open,
    );

    if (!wide) {
      if (location == null) {
        return Scaffold(appBar: _settingsBar(context), body: menu);
      }
      return current?.page ?? _MissingPage(path: location!);
    }

    final entry = current ?? settingsEntryFor(selected)!;
    return Scaffold(
      body: Row(
        children: [
          SizedBox(
            width: 300,
            child: Scaffold(appBar: _settingsBar(context), body: menu),
          ),
          const VerticalDivider(width: 1),
          // 右栏自己一个导航栈：页面不显示返回按钮，提示条也只盖住右栏。
          // 页面里再进下一层仍走全局路由（整页打开并保留返回）。
          Expanded(
            child: ScaffoldMessenger(
              child: Navigator(
                key: ValueKey(entry.path),
                onGenerateRoute: (_) =>
                    MaterialPageRoute<void>(builder: (_) => entry.page),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// 分组菜单。宽屏和手机上内容一样，只是点按行为和行尾箭头不同。
/// 设置的标题栏。从地址直接打开时没有上一页，左上角给一个回首页的按钮，
/// 不然宽屏和 Web 上只能靠浏览器后退离开设置。
AppBar _settingsBar(BuildContext context) => AppBar(
  title: const Text('设置'),
  leading: Navigator.of(context).canPop()
      ? null
      : IconButton(
          icon: const Icon(Icons.close_rounded),
          tooltip: '关闭设置',
          onPressed: () => context.go('/'),
        ),
);

class _SettingsMenu extends StatelessWidget {
  const _SettingsMenu({
    required this.groups,
    required this.selected,
    required this.wide,
    required this.showSetup,
    required this.dots,
    required this.serverName,
    required this.onOpen,
  });

  final List<SettingsMenuGroup> groups;
  final String? selected;
  final bool wide;
  final bool showSetup;
  final Map<String, Color> dots;
  final String? serverName;
  final void Function(SettingsEntry entry) onOpen;

  @override
  Widget build(BuildContext context) => ListView(
    padding: const EdgeInsets.only(bottom: 24),
    children: [
      if (showSetup)
        _EntryTile(
          entry: setupSettingsEntry,
          selected: selected == setupSettingsEntry.path,
          chevron: !wide,
          dot: dots[setupSettingsEntry.path],
          onTap: () => onOpen(setupSettingsEntry),
        ),
      for (final (index, group) in groups.indexed) ...[
        if (group.server) _ServerHeading(serverName),
        SectionTitle(
          group.title,
          first: (index == 0 && !showSetup) || group.server,
        ),
        for (final entry in group.entries)
          _EntryTile(
            entry: entry,
            selected: selected == entry.path,
            chevron: !wide,
            dot: dots[entry.path],
            onTap: () => onOpen(entry),
          ),
      ],
    ],
  );
}

class _EntryTile extends StatelessWidget {
  const _EntryTile({
    required this.entry,
    required this.selected,
    required this.chevron,
    required this.dot,
    required this.onTap,
  });

  final SettingsEntry entry;
  final bool selected;
  final bool chevron;
  final Color? dot;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final trailing = [
      if (dot != null)
        Container(
          width: 8,
          height: 8,
          decoration: BoxDecoration(color: dot, shape: BoxShape.circle),
        ),
      if (chevron) const Icon(Icons.chevron_right),
    ];
    return FocusRing(
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(
          horizontal: CinemaSpacing.gutter,
        ),
        selected: selected,
        selectedTileColor: CinemaColors.surface2,
        leading: Icon(entry.icon),
        title: Text(entry.title),
        trailing: trailing.isEmpty
            ? null
            : Row(
                mainAxisSize: MainAxisSize.min,
                spacing: 8,
                children: trailing,
              ),
        onTap: onTap,
      ),
    );
  }
}

/// 服务器分组前的分隔线：── 服务器「光圈」──
class _ServerHeading extends StatelessWidget {
  const _ServerHeading(this.name);

  final String? name;

  @override
  Widget build(BuildContext context) {
    final label = name == null || name!.isEmpty ? '服务器' : '服务器「$name」';
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        CinemaSpacing.gutter,
        CinemaSpacing.section,
        CinemaSpacing.gutter,
        0,
      ),
      child: Row(
        children: [
          const Expanded(child: Divider()),
          const SizedBox(width: 10),
          Flexible(
            flex: 4,
            child: Text(
              label,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.labelLarge!
                  .copyWith(color: CinemaColors.text2),
            ),
          ),
          const SizedBox(width: 10),
          const Expanded(child: Divider()),
        ],
      ),
    );
  }
}

/// 地址里是一个不存在的设置项（正常路由不会走到这里，防止手改地址时白屏）。
class _MissingPage extends StatelessWidget {
  const _MissingPage({required this.path});

  final String path;

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('设置')),
    body: Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Text(
          '没有这个设置项：$path',
          textAlign: TextAlign.center,
          style: Theme.of(context).textTheme.bodyMedium!
              .copyWith(color: CinemaColors.text2),
        ),
      ),
    ),
  );
}
