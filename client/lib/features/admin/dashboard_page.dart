import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/shell.dart' show wideLayoutBreakpoint;
import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/format.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/section_title.dart';
import '../auth/account_switcher.dart' show AccountAvatar;
import 'admin_widgets.dart';
import 'libraries_api.dart';
import 'library_advanced_api.dart';
import 'maintenance_api.dart';
import 'summary_api.dart';
import 'summary_hints.dart';

/// 「状态 › 仪表盘」：原管理后台首页的概况加上资料库统计。
/// 依次是待处理事项（含同步时间）、正在播放、最近播放和每个资料库的统计。
class DashboardPage extends ConsumerStatefulWidget {
  const DashboardPage({super.key});

  @override
  ConsumerState<DashboardPage> createState() => _DashboardPageState();
}

class _DashboardPageState extends ConsumerState<DashboardPage> {
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    // 正在播放靠进度上报判断，定时刷新才跟得上
    _timer = Timer.periodic(
      const Duration(seconds: 30),
      (_) => ref.invalidate(adminActivityProvider),
    );
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  void _refresh() {
    ref.invalidate(adminSummaryProvider);
    ref.invalidate(adminActivityProvider);
    ref.invalidate(activeAlertsProvider);
    ref.invalidate(adminLibrariesProvider);
  }

  /// 宽屏时仪表盘就在设置的右栏里，换一项设置直接切右栏；手机上一层层打开，
  /// 保留返回。没有路由时（组件单独使用）什么都不做。
  void _openEntry(String path) {
    final router = GoRouter.maybeOf(context);
    if (router == null) return;
    final wide = MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint;
    if (wide) {
      router.go(path);
    } else {
      router.push(path);
    }
  }

  /// 详情页（条目、插件、资料库统计）在两种布局下都是整页盖上来，保留返回。
  void _push(String path) => GoRouter.maybeOf(context)?.push(path);

  @override
  Widget build(BuildContext context) {
    final summary = ref.watch(adminSummaryProvider);
    final alerts =
        ref.watch(activeAlertsProvider).value ?? const <AdminAlert>[];
    final wide = MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint;
    return Scaffold(
      appBar: AppBar(
        title: const Text('仪表盘'),
        actions: [
          IconButton(
            tooltip: '刷新',
            onPressed: _refresh,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: AdminListWidth(
        top: 8,
        builder: (padding) => ListView(
          padding: padding,
          children: [
            const SectionTitle('待处理事项', first: true),
            if (summary.hasValue && summary.value != null)
              ..._attention(summary.value!, alerts)
            else if (summary.hasError)
              _ErrorRow(
                message: '概况加载失败',
                error: summary.error!,
                onRetry: () => ref.invalidate(adminSummaryProvider),
              )
            else
              const _HintText('正在读取概况…'),
            if (summary.value case final value?) _SyncTimes(value),
            const SectionTitle('正在播放'),
            _Activity(
              playing: true,
              recent: wide ? 10 : 5,
              onRetry: () => ref.invalidate(adminActivityProvider),
            ),
            const SectionTitle('最近播放'),
            _Activity(
              playing: false,
              recent: wide ? 10 : 5,
              onRetry: () => ref.invalidate(adminActivityProvider),
            ),
            const SectionTitle('资料库统计'),
            _LibraryStats(onOpen: _push),
          ],
        ),
      ),
    );
  }

  /// 待处理事项：没有时显示成功色的「一切正常」。
  List<Widget> _attention(AdminSummary summary, List<AdminAlert> alerts) {
    final items = attentionItems(summary, alerts);
    if (items.isEmpty) {
      return const [
        ListTile(
          contentPadding: EdgeInsets.symmetric(
            horizontal: CinemaSpacing.gutter,
          ),
          leading: Icon(
            Icons.check_circle_outline,
            color: CinemaColors.success,
          ),
          title: Text('一切正常'),
        ),
      ];
    }
    return [
      for (final (path, hint, fullPage) in items)
        FocusRing(
          child: ListTile(
            contentPadding: const EdgeInsets.symmetric(
              horizontal: CinemaSpacing.gutter,
            ),
            leading: Icon(hint.icon, color: hint.color),
            title: Text(
              hint.message,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
            ),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => fullPage ? _push(path) : _openEntry(path),
          ),
        ),
    ];
  }
}

/// 上次同步和下次定时同步的时间
class _SyncTimes extends StatelessWidget {
  const _SyncTimes(this.summary);

  final AdminSummary summary;

  @override
  Widget build(BuildContext context) {
    final last = summary.sync;
    final next = summary.nextSyncAt;
    final parts = [
      if (last == null)
        '还没有同步过'
      else if (!last.running)
        '上次同步 ${formatAgo(last.finishedAt ?? last.startedAt)}',
      if (next != null) '下次同步 ${formatDateTime(next)}',
    ];
    if (parts.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.fromLTRB(
        CinemaSpacing.gutter,
        4,
        CinemaSpacing.gutter,
        0,
      ),
      child: Text(
        parts.join(' · '),
        style: Theme.of(context).textTheme.bodySmall
            ?.copyWith(color: CinemaColors.text2),
      ),
    );
  }
}

/// 正在播放或最近播放。加载失败和加载中都单独说明，不影响别的区块。
class _Activity extends ConsumerWidget {
  const _Activity({
    required this.playing,
    required this.recent,
    required this.onRetry,
  });

  final bool playing;

  /// 最近播放最多显示几条
  final int recent;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final activity = ref.watch(adminActivityProvider);
    if (activity.hasError && !activity.hasValue) {
      return _ErrorRow(
        message: playing ? '正在播放加载失败' : '最近播放加载失败',
        error: activity.error!,
        onRetry: onRetry,
      );
    }
    final value = activity.value;
    if (value == null) {
      return _HintText(playing ? '正在读取正在播放…' : '正在读取最近播放…');
    }
    // 播放的作品在全局路由里打开，宽屏时也盖住整个设置
    void open(PlayActivity play) =>
        GoRouter.maybeOf(context)?.push('/item/${play.itemId}');
    if (playing) {
      if (value.nowPlaying.isEmpty) {
        return const _HintText('现在没有正在播放的内容');
      }
      return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          for (final play in value.nowPlaying)
            _PlayTile(play: play, playing: true, onTap: () => open(play)),
        ],
      );
    }
    if (value.recent.isEmpty) return const _HintText('还没有播放记录');
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final play in value.recent.take(recent))
          _PlayTile(play: play, playing: false, onTap: () => open(play)),
      ],
    );
  }
}

class _PlayTile extends StatelessWidget {
  const _PlayTile({
    required this.play,
    required this.playing,
    required this.onTap,
  });

  final PlayActivity play;
  final bool playing;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final position = play.position;
    final duration = play.duration;
    final hasProgress =
        playing && position != null && duration != null && duration > 0;
    final detail = playing
        ? [
            play.username,
            if (play.client != null) playClientLabel(play.client!),
            if (hasProgress)
              '${formatClock(Duration(seconds: position))} / '
                  '${formatClock(Duration(seconds: duration))}',
          ]
        : [
            play.username,
            if (play.client != null) playClientLabel(play.client!),
            formatAgo(play.at),
          ];
    return FocusRing(
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(
          horizontal: CinemaSpacing.gutter,
        ),
        leading: AccountAvatar(username: play.username, radius: 18),
        title: Text(play.label, maxLines: 1, overflow: TextOverflow.ellipsis),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(detail.join(' · ')),
            if (hasProgress)
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: LinearProgressIndicator(
                  value: (position / duration).clamp(0.0, 1.0),
                  minHeight: 3,
                  color: CinemaColors.amber,
                  backgroundColor: CinemaColors.surface3,
                ),
              ),
          ],
        ),
        onTap: onTap,
      ),
    );
  }
}

/// 每个资料库一行统计，数据来自原来资料库统计页的接口；点开还是那一页。
class _LibraryStats extends ConsumerWidget {
  const _LibraryStats({required this.onOpen});

  final void Function(String path) onOpen;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final libraries = ref.watch(adminLibrariesProvider);
    if (libraries.hasError && !libraries.hasValue) {
      return _ErrorRow(
        message: '资料库统计加载失败',
        error: libraries.error!,
        onRetry: () => ref.invalidate(adminLibrariesProvider),
      );
    }
    final value = libraries.value;
    if (value == null) return const _HintText('正在读取资料库…');
    if (value.isEmpty) return const _HintText('还没有资料库');
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        for (final library in value)
          _LibraryStatsRow(library: library, onTap: onOpen),
      ],
    );
  }
}

class _LibraryStatsRow extends ConsumerWidget {
  const _LibraryStatsRow({required this.library, required this.onTap});

  final AdminLibrary library;
  final void Function(String path) onTap;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final stats = ref.watch(libraryAdvancedStatsProvider(library.id));
    final detail = switch (stats) {
      AsyncValue(hasValue: true, :final value?) => [
        '条目 ${value['items'] ?? 0}',
        '电影 ${value['movies'] ?? 0}',
        '剧集 ${value['series'] ?? 0}',
        '单集 ${value['episodes'] ?? 0}',
        '未匹配 ${value['unmatched'] ?? 0}',
        '失效 ${value['missing'] ?? 0}',
      ].join(' · '),
      AsyncError() => '统计加载失败，点开重试',
      _ => '正在读取统计…',
    };
    return FocusRing(
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(
          horizontal: CinemaSpacing.gutter,
        ),
        leading: Icon(
          library.kind == 'series' ? Icons.tv_outlined : Icons.movie_outlined,
        ),
        title: Text(library.name, maxLines: 1, overflow: TextOverflow.ellipsis),
        subtitle: Text(detail, maxLines: 2, overflow: TextOverflow.ellipsis),
        trailing: const Icon(Icons.chevron_right),
        onTap: () => onTap(
          '/settings/manage/libraries/${library.id}'
          '?name=${Uri.encodeComponent(library.name)}',
        ),
      ),
    );
  }
}

/// 只占一行的小字说明：空数据和加载中用它，不占满整屏。
class _HintText extends StatelessWidget {
  const _HintText(this.message);

  final String message;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(
      horizontal: CinemaSpacing.gutter,
      vertical: 8,
    ),
    child: Text(
      message,
      style: Theme.of(context).textTheme.bodyMedium
          ?.copyWith(color: CinemaColors.text2),
    ),
  );
}

/// 一项数据没取到时只坏这一块，给原因和重试。
class _ErrorRow extends StatelessWidget {
  const _ErrorRow({
    required this.message,
    required this.error,
    required this.onRetry,
  });

  final String message;
  final Object error;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(
        horizontal: CinemaSpacing.gutter,
      ),
      leading: const Icon(Icons.cloud_off, color: CinemaColors.amber),
      title: Text(message),
      subtitle: Text(
        ApiError.from(error).message,
        style: text.bodySmall?.copyWith(color: CinemaColors.text2),
      ),
      trailing: TextButton(onPressed: onRetry, child: const Text('重试')),
    );
  }
}
