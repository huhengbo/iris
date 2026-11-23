import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

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
