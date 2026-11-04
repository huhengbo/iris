import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/core/widgets/picker_sheet.dart';
import 'package:iris/features/play/player_page.dart';
import 'package:media_kit/media_kit.dart';

/// 单独渲染控制层：真播放器要初始化 mpv，widget 测试里建不出来。
Future<({FocusNode play, FocusNode seek})> pumpControls(
  WidgetTester tester, {
  bool tv = false,
  bool visible = true,
  bool castable = false,
  bool playing = true,
  ValueChanged<Duration>? onSeekBy,
  ValueChanged<Duration>? onScrubBy,
  VoidCallback? onTogglePlay,
  VoidCallback? onClose,
  VoidCallback? onOpenSettings,
  VoidCallback? onRate,
  bool locked = false,
  bool episodic = false,
  VoidCallback? onPrevious,
  VoidCallback? onNext,
  VoidCallback? onEpisodes,
  VoidCallback? onToggleLock,
}) async {
  final playFocus = FocusNode(debugLabel: 'player.play');
  final seekFocus = FocusNode(debugLabel: 'player.seek');
  addTearDown(playFocus.dispose);
  addTearDown(seekFocus.dispose);
  await tester.pumpWidget(
    MaterialApp(
      theme: buildTheme(),
      home: Scaffold(
        body: PlayerControls(
          tv: tv,
          visible: visible,
          title: '三体 · 第 1 季 第 2 集',
          position: const Duration(minutes: 1),
          duration: const Duration(minutes: 45),
          playing: playing,
          castable: castable,
          rate: 1,
          fill: false,
          playFocus: playFocus,
          seekFocus: seekFocus,
          onClose: onClose ?? () {},
          onCast: () {},
          onTogglePlay: onTogglePlay ?? () {},
          onSeekBy: onSeekBy ?? (_) {},
          onScrubBy: onScrubBy ?? (_) {},
          onOpenSettings: onOpenSettings ?? () {},
          onScrubStart: () {},
          onScrub: (_) {},
          onScrubEnd: () {},
          onRate: onRate ?? () {},
          onAudio: () {},
          onSubtitles: () {},
          locked: locked,
          episodic: episodic,
          onPrevious: onPrevious,
          onNext: onNext,
          onEpisodes: onEpisodes,
          onRotate: tv ? null : () {},
          onToggleFill: tv ? null : () {},
          onToggleLock: tv ? null : (onToggleLock ?? () {}),
        ),
      ),
    ),
  );
  return (play: playFocus, seek: seekFocus);
}

void main() {
  test('双击左侧后退 10 秒、右侧前进 10 秒，中间不跳转', () {
    const width = 300.0;
    expect(doubleTapSeek(10, width), const Duration(seconds: -10));
    expect(doubleTapSeek(90, width), const Duration(seconds: -10));
    expect(doubleTapSeek(210, width), const Duration(seconds: 10));
    expect(doubleTapSeek(295, width), const Duration(seconds: 10));
    // 中间双击是播放暂停，不跳
    expect(doubleTapSeek(150, width), isNull);
  });

  test('剧集片名写成「剧名 · 第 N 季 第 N 集」，电影原样', () {
    PlayerArgs args(String title) =>
        PlayerArgs(playableId: 1, title: title, url: '');

    expect(args('三体 S01E02').displayTitle, '三体 · 第 1 季 第 2 集');
    expect(args('三体 S00E05').displayTitle, '三体 · 特别篇 第 5 集');
    expect(args('流浪地球').displayTitle, '流浪地球');
  });

  test('播放器里的时间写到时，不知道片长时写横线', () {
    expect(
      formatPlayerClock(const Duration(minutes: 3, seconds: 34)),
      '00:03:34',
    );
    expect(formatPlayerClock(const Duration(hours: 1, seconds: 5)), '01:00:05');
    expect(formatPlayerClock(const Duration(seconds: -1)), '--:--:--');
  });

  testWidgets('底栏是倍速、后退 10 秒 / 播放暂停 / 前进 10 秒、音轨、字幕', (tester) async {
    final jumps = <Duration>[];
    var toggles = 0;
    await pumpControls(
      tester,
      onSeekBy: jumps.add,
      onTogglePlay: () => toggles++,
    );

    await tester.tap(find.byTooltip('后退 10 秒'));
    await tester.pump();
    await tester.tap(find.byTooltip('前进 10 秒'));
    await tester.pump();
    expect(jumps, const [Duration(seconds: -10), Duration(seconds: 10)]);

    await tester.tap(find.byTooltip('暂停'));
    await tester.pump();
    expect(toggles, 1);

    // 按钮的边也算按钮，不能漏到画面的点按手势上
    final button = tester.getCenter(find.byTooltip('暂停'));
    await tester.tapAt(button + const Offset(0, 26));
    await tester.pump();
    expect(toggles, 2);

    expect(find.text('1.0x'), findsOneWidget);
    expect(find.byTooltip('音轨'), findsOneWidget);
    expect(find.byTooltip('字幕'), findsOneWidget);
    expect(find.byTooltip('旋转屏幕'), findsOneWidget);
    expect(find.byTooltip('画面填满屏幕'), findsOneWidget);
    expect(find.byTooltip('锁屏'), findsOneWidget);
    expect(find.byTooltip('返回'), findsOneWidget);
    expect(find.text('00:01:00'), findsOneWidget);
    expect(find.text('00:45:00'), findsOneWidget);
    // 电影没有上一集 / 下一集 / 选集
    expect(find.byTooltip('上一集'), findsNothing);
    expect(find.byTooltip('选集'), findsNothing);
  });

  testWidgets('剧集有上一集 / 下一集 / 选集，到头的一侧变灰不响应', (tester) async {
    var next = 0;
    var episodes = 0;
    await pumpControls(
      tester,
      episodic: true,
      onNext: () => next++,
      onEpisodes: () => episodes++,
    );
    await tester.tap(find.byTooltip('上一集'));
    await tester.tap(find.byTooltip('下一集'));
    await tester.tap(find.byTooltip('选集'));
    await tester.pump();
    expect(next, 1);
    expect(episodes, 1);
  });

  testWidgets('锁屏后只剩锁，其他按钮都不在', (tester) async {
    var unlocks = 0;
    await pumpControls(tester, locked: true, onToggleLock: () => unlocks++);
    expect(find.byTooltip('暂停'), findsNothing);
    expect(find.byTooltip('返回'), findsNothing);
    expect(find.byTooltip('播放设置'), findsNothing);
    await tester.tap(find.byTooltip('解锁'));
    await tester.pump();
    expect(unlocks, 1);
  });

  testWidgets('电视上没有旋转、画面比例和锁屏', (tester) async {
    await pumpControls(tester, tv: true);
    expect(find.byTooltip('旋转屏幕'), findsNothing);
    expect(find.byTooltip('画面填满屏幕'), findsNothing);
    expect(find.byTooltip('锁屏'), findsNothing);
  });

  testWidgets('控制层隐藏时按钮不响应点按，显示后恢复', (tester) async {
    var toggles = 0;
    await pumpControls(
      tester,
      visible: false,
      playing: false,
      onTogglePlay: () => toggles++,
    );
    final opacity = tester.widget<AnimatedOpacity>(
      find
          .ancestor(
            of: find.byTooltip('播放'),
            matching: find.byType(AnimatedOpacity),
          )
          .first,
    );
    expect(opacity.opacity, 0);
    await tester.tap(find.byTooltip('播放'), warnIfMissed: false);
    await tester.pump();
    expect(toggles, 0);

    await pumpControls(tester, playing: false, onTogglePlay: () => toggles++);
    await tester.tap(find.byTooltip('播放'));
    await tester.pump();
    expect(toggles, 1);
  });

  testWidgets('投屏按钮只在能投屏的平台上出现', (tester) async {
    await pumpControls(tester);
    expect(find.byTooltip('投屏到电视'), findsNothing);

    await pumpControls(tester, castable: true);
    expect(find.byTooltip('投屏到电视'), findsOneWidget);
  });

  testWidgets('遥控器聚焦播放按钮时确定键切换播放暂停', (tester) async {
    var toggles = 0;
    final nodes = await pumpControls(
      tester,
      tv: true,
      onTogglePlay: () => toggles++,
    );
    nodes.play.requestFocus();
    await tester.pump();
    expect(nodes.play.hasPrimaryFocus, isTrue);

    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.pump();
    expect(toggles, 1);
    expect(find.byTooltip('后退 10 秒'), findsOneWidget);
    expect(find.byTooltip('前进 10 秒'), findsOneWidget);
  });

  testWidgets('遥控器从播放按钮往左右到倍速，确定键打开速度', (tester) async {
    var opened = 0;
    final nodes = await pumpControls(tester, tv: true, onRate: () => opened++);
    nodes.play.requestFocus();
    await tester.pump();
    for (var i = 0; i < 2; i++) {
      await tester.sendKeyEvent(LogicalKeyboardKey.arrowLeft);
      await tester.pump();
    }
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.pump();
    expect(opened, 1);
  });

  testWidgets('遥控器从播放按钮往上到进度条，左右键按和按钮一致的秒数跳', (tester) async {
    final scrubs = <Duration>[];
    final nodes = await pumpControls(tester, tv: true, onScrubBy: scrubs.add);
    nodes.play.requestFocus();
    await tester.pump();
    await tester.sendKeyEvent(LogicalKeyboardKey.arrowUp);
    await tester.pump();
    expect(nodes.seek.hasFocus, isTrue);

    await tester.sendKeyEvent(LogicalKeyboardKey.arrowRight);
    await tester.pump();
    await tester.sendKeyEvent(LogicalKeyboardKey.arrowLeft);
    await tester.pump();
    expect(scrubs, const [Duration(seconds: 10), Duration(seconds: -10)]);
  });

  testWidgets('设置面板分音频、字幕、播放速度、画面，字幕入口叫「搜索字幕」', (tester) async {
    final calls = <String>[];
    var searched = 0;
    await tester.pumpWidget(
      MaterialApp(
        theme: buildTheme(),
        home: Scaffold(
          body: Builder(
            builder: (context) => TextButton(
              onPressed: () => showPlayerSettings(
                context,
                audios: const [
                  AudioTrack('a1', '国语', null),
                  AudioTrack('a2', '粤语', null),
                ],
                audioId: 'a1',
                subtitles: const [SubtitleTrack('s1', '简体中文', null)],
                subtitleId: 's1',
                rate: 1,
                fill: false,
                canSearchSubtitles: true,
                onAudio: (track) => calls.add('音轨 ${track.title}'),
                onSubtitle: (track) => calls.add('字幕 ${track?.title ?? '关闭'}'),
                onRate: (rate) => calls.add('速度 $rate'),
                onFill: (fill) => calls.add('画面 $fill'),
                onSearchSubtitles: () async => searched++,
              ),
              child: const Text('打开设置'),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('打开设置'));
    await tester.pumpAndSettle();

    expect(find.text('播放设置'), findsOneWidget);
    for (final section in ['音频', '字幕', '播放速度', '画面']) {
      expect(find.text(section), findsOneWidget);
    }
    expect(find.text('搜索字幕'), findsOneWidget);
    expect(find.textContaining('搜索更多'), findsNothing);

    // 选中即生效，面板不关，可以接着调别的
    await tester.tap(find.text('粤语'));
    await tester.pumpAndSettle();
    expect(calls, contains('音轨 粤语'));
    expect(find.text('播放设置'), findsOneWidget);

    // 字幕可以关掉，选中状态跟着变
    await tester.tap(find.text('关闭'));
    await tester.pumpAndSettle();
    expect(calls, contains('字幕 关闭'));
    expect(
      tester
          .widget<PickerOption>(find.widgetWithText(PickerOption, '关闭'))
          .selected,
      isTrue,
    );

    await tester.ensureVisible(find.text('1.5×'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('1.5×'));
    await tester.pumpAndSettle();
    expect(calls, contains('速度 1.5'));

    await tester.ensureVisible(find.text('填满屏幕'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('填满屏幕'));
    await tester.pumpAndSettle();
    expect(calls, contains('画面 true'));

    await tester.ensureVisible(find.text('搜索字幕'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('搜索字幕'));
    await tester.pumpAndSettle();
    expect(searched, 1);
    expect(find.text('播放设置'), findsNothing);
  });

  testWidgets('底栏按钮只打开对应的一段，标题就是段名', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: buildTheme(),
        home: Scaffold(
          body: Builder(
            builder: (context) => TextButton(
              onPressed: () => showPlayerSettings(
                context,
                only: PlayerSettingsSection.rate,
                audios: const [AudioTrack('a1', '国语', null)],
                audioId: 'a1',
                subtitles: const [],
                subtitleId: null,
                rate: 1,
                fill: false,
                canSearchSubtitles: false,
                onAudio: (_) {},
                onSubtitle: (_) {},
                onRate: (_) {},
                onFill: (_) {},
                onSearchSubtitles: () async {},
              ),
              child: const Text('打开'),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('打开'));
    await tester.pumpAndSettle();
    expect(find.text('播放速度'), findsOneWidget);
    expect(find.text('1.5×'), findsOneWidget);
    expect(find.text('国语'), findsNothing);
    expect(find.text('填满屏幕'), findsNothing);
  });
}
