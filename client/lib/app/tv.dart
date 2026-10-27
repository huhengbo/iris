import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'theme.dart';

/// 是否运行在电视上。启动时由 [detectTelevision] 判断后在 main 里覆盖。
final televisionProvider = Provider<bool>((ref) => false);

const _device = MethodChannel('iris/device');

/// 问原生层是不是电视（Leanback 桌面启动的同一个 APK）。
/// 开发时用 `--dart-define=IRIS_TV=true` 强制进入 TV 模式。
Future<bool> detectTelevision() async {
  if (const bool.fromEnvironment('IRIS_TV')) return true;
  if (kIsWeb || defaultTargetPlatform != TargetPlatform.android) return false;
  try {
    return await _device.invokeMethod<bool>('isTelevision') ?? false;
  } on PlatformException {
    return false;
  }
}

/// TV 的尺寸约束，见 DESIGN.md「多端：Android TV」。
abstract final class TvLayout {
  /// 过扫描安全区：电视可能裁掉画面四周
  static const overscan = EdgeInsets.symmetric(horizontal: 48, vertical: 27);

  /// 隔着几米看，最小的字（12）也要放大到 14
  static const minTextScale = 14 / 12;
}

/// 进入 TV 模式前调用：遥控器没有触屏，从一开始就显示聚焦环。
void enterTelevisionMode() {
  FocusManager.instance.highlightStrategy =
      FocusHighlightStrategy.alwaysTraditional;
}

/// TV 模式下全应用的媒体参数：方向键导航、过扫描安全区当作系统边距
/// （SafeArea、AppBar、列表项都会让开）、放大字号。
/// 输入法弹出时底边被它盖住，不再留底部过扫描，把高度留给输入框。
MediaQueryData televisionMediaQuery(MediaQueryData data) => data.copyWith(
  navigationMode: NavigationMode.directional,
  padding:
      data.padding +
      (data.viewInsets.bottom > 0
          ? TvLayout.overscan.copyWith(bottom: 0)
          : TvLayout.overscan),
  textScaler: data.textScaler.clamp(minScaleFactor: TvLayout.minTextScale),
);

/// 给 `MaterialApp.builder` 用。
/// 输入框默认把上下键用来移动光标，遥控器就出不了输入框；
/// TV 上改成移动焦点（打字交给系统输入法）。这一层在文本编辑快捷键之下，优先生效。
Widget televisionBuilder(BuildContext context, Widget? child) => Shortcuts(
  shortcuts: const {
    SingleActivator(LogicalKeyboardKey.arrowUp): DirectionalFocusIntent(
      TraversalDirection.up,
      ignoreTextFields: false,
    ),
    SingleActivator(LogicalKeyboardKey.arrowDown): DirectionalFocusIntent(
      TraversalDirection.down,
      ignoreTextFields: false,
    ),
  },
  child: MediaQuery(
    data: televisionMediaQuery(MediaQuery.of(context)),
    child: child!,
  ),
);

/// 当前是否 TV 模式，界面里按需调整焦点和选项。
bool isTelevision(BuildContext context) =>
    MediaQuery.maybeNavigationModeOf(context) == NavigationMode.directional;

/// TV 首次进入时给出一次遥控器操作提示；提示不参与焦点遍历，不会抢走页面首焦点。
/// 放在 [televisionBuilder] 的 MediaQuery 里面，靠右下角让开过扫描安全区。
/// [enabled] 由 false 变 true 时（例如开机动画结束后）才开始计时显示，只显示一次。
class TvRemoteHint extends StatefulWidget {
  const TvRemoteHint({
    super.key,
    required this.child,
    required this.enabled,
    this.delay = Duration.zero,
  });

  final Widget child;
  final bool enabled;

  /// 启用后再等这么久才出现，留给开机动画张开。
  final Duration delay;

  static const visibleFor = Duration(seconds: 8);

  @override
  State<TvRemoteHint> createState() => _TvRemoteHintState();
}

class _TvRemoteHintState extends State<TvRemoteHint> {
  Timer? _timer;
  bool _scheduled = false;
  bool _visible = false;

  @override
  void initState() {
    super.initState();
    if (widget.enabled) _schedule();
  }

  @override
  void didUpdateWidget(TvRemoteHint oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.enabled && !_scheduled) _schedule();
  }

  void _schedule() {
    _scheduled = true;
    if (widget.delay == Duration.zero) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _show());
    } else {
      _timer = Timer(widget.delay, _show);
    }
  }

  void _show() {
    if (!mounted) return;
    setState(() => _visible = true);
    _timer = Timer(TvRemoteHint.visibleFor, _hide);
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  void _hide() {
    if (mounted) setState(() => _visible = false);
  }

  @override
  Widget build(BuildContext context) {
    // TV 模式下 padding 已经含过扫描安全区
    final padding = MediaQuery.paddingOf(context);
    final available = MediaQuery.sizeOf(context).width - padding.horizontal;
    return Stack(
      fit: StackFit.expand,
      children: [
        widget.child,
        if (widget.enabled && _visible)
          Positioned(
            right: padding.right,
            bottom: padding.bottom,
            // 在 Navigator 外面，没有 Material 祖先：自己给出主题字体
            child: Material(
              type: MaterialType.transparency,
              child: GestureDetector(
                onTap: _hide,
                child: Semantics(
                  button: true,
                  label: '关闭遥控器提示',
                  child: SizedBox(
                    width: available.clamp(220.0, 520.0),
                    child: DecoratedBox(
                      decoration: const BoxDecoration(
                        color: CinemaColors.surface2,
                        borderRadius: BorderRadius.all(
                          Radius.circular(CinemaRadius.card),
                        ),
                        border: Border.fromBorderSide(
                          BorderSide(color: CinemaColors.outline),
                        ),
                        boxShadow: CinemaShadows.lift,
                      ),
                      child: Padding(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 16,
                          vertical: 12,
                        ),
                        child: Text(
                          '遥控器：方向键移动 · 确认键选择 · 返回键退出',
                          style: Theme.of(context).textTheme.bodyMedium!
                              .copyWith(color: CinemaColors.text1),
                        ),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
      ],
    );
  }
}
