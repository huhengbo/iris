import 'package:flutter/material.dart';

import 'motion.dart';

// 皮肤文件：颜色、阴影、圆角、间距和字号阶梯都只在这里定义，页面只引用这些令牌
// 和 textTheme。换皮肤改这个文件（动效在 motion.dart），页面不用动。

/// 影院暗场色板，角色说明见 DESIGN.md「色板角色」。
abstract final class CinemaColors {
  static const bg = Color(0xFF120E0B);
  static const surface1 = Color(0xFF1B1611);
  static const surface2 = Color(0xFF241D17);
  static const surface3 = Color(0xFF30271F);

  static const text0 = Color(0xF0FFF6EA);
  static const text1 = Color(0xDBFFF4E6);
  static const text2 = Color(0x99FFECD6);
  static const line = Color(0x12FFECD6);

  static const amber = Color(0xFFE9A548);
  static const amberInk = Color(0xFF1E1408);
  static const amberSoft = Color(0x26E9A548);

  /// 生效中的胶囊的边框，配 amberSoft 底。
  static const amberLine = Color(0x66E9A548);

  /// 暖白 13% 叠在背景上的实色，用于次按钮。
  static const ghost = Color(0xFF312C28);
  static const error = Color(0xFFF2877A);
  static const success = Color(0xFF8FCB9B);

  /// 叠在海报和剧照上的深色圆底（返回按钮、播放图标）。
  static const scrim = Color(0x940E0B08);

  /// 浅色反色底：SnackBar、Tooltip。
  static const inverse = Color(0xFFF3E7D8);

  /// 暖白 24%：进度条没走到的部分，也用于未选中的页码点、图片占位图标。
  static const track = Color(0x3DFFECD6);

  /// 骨架屏微光的高光。
  static const shimmer = Color(0xFF3A3027);

  /// 侧栏的焦点提亮：选中项已是琥珀淡底，焦点改用暖白区分。
  static const highlight = Color(0x38FFECD6);

  /// 二维码的白底：扫码要高对比，是唯一允许的纯白。
  static const paper = Color(0xFFFFFFFF);

  // 以下只在本文件映射到 Material 组件，页面不直接用

  /// 深琥珀：悬浮按钮的底（primaryContainer）。
  static const amberDeep = Color(0xFF322515);

  /// 浅底上的琥珀：SnackBar 的动作文字（inversePrimary）。
  static const amberOnInverse = Color(0xFF8A5A14);

  /// 文字选区。
  static const selection = Color(0x59E9A548);

  /// 次要强调（secondary），目前只有 Material 内部用到。
  static const secondary = Color(0xFFD9C7B0);
  static const onError = Color(0xFF2A0F0B);

  /// 描边按钮的边。
  static const outline = Color(0x33FFECD6);

  /// 悬停叠层。
  static const hover = Color(0x0FFFECD6);

  /// 开关打开时的圆点。
  static const switchThumb = Color(0xFFFFF6EA);

  /// 银幕阴影。
  static const shadowDeep = Color(0xEB000000);
  static const shadowDeeper = Color(0xF2000000);
  static const shadowNear = Color(0x61000000);
  static const shadowNearer = Color(0x66000000);
}

/// 圆角档位，见 DESIGN.md「形状与深度」。
abstract final class CinemaRadius {
  /// 按钮、输入框、版本分段
  static const control = 14.0;

  /// 卡片、剧照缩略图、可点的行、菜单、SnackBar、状态提示
  static const card = 12.0;
  static const poster = 10.0;

  /// 小元素：管理列表缩略图、图标方块、Tooltip
  static const small = 8.0;

  /// 更小的元素：评分角标、骨架文字条
  static const tiny = 4.0;
  static const sheet = 24.0;
  static const dialog = 20.0;
  static const fab = 16.0;

  /// 胶囊、页码点、进度条：全圆
  static const pill = 999.0;
}

/// 间距，见 DESIGN.md「布局」。
abstract final class CinemaSpacing {
  /// 页面左右边距：手机 20，宽屏 32
  static const gutter = 20.0;
  static const gutterWide = 32.0;
  static double gutterFor({required bool wide}) => wide ? gutterWide : gutter;

  /// 区块之间
  static const section = 28.0;

  /// 区块标题与内容之间
  static const heading = 12.0;
}

/// 只有海报和剧照有阴影，模拟银幕光，见 DESIGN.md「形状与深度」。
abstract final class CinemaShadows {
  static const lift = [
    BoxShadow(
      offset: Offset(0, 14),
      blurRadius: 24,
      spreadRadius: -10,
      color: CinemaColors.shadowDeep,
    ),
    BoxShadow(
      offset: Offset(0, 3),
      blurRadius: 8,
      color: CinemaColors.shadowNear,
    ),
  ];
  static const focused = [
    BoxShadow(
      offset: Offset(0, 18),
      blurRadius: 26,
      spreadRadius: -10,
      color: CinemaColors.shadowDeeper,
    ),
    BoxShadow(
      offset: Offset(0, 4),
      blurRadius: 10,
      color: CinemaColors.shadowNearer,
    ),
  ];
}

const _radius = BorderRadius.all(Radius.circular(CinemaRadius.control));

/// 键盘和遥控器聚焦时的描边：Android TV 只能靠焦点操作，聚焦必须一眼可见。
/// 主题配不出描边的 ListTile、InkWell 用 FocusRing。
BorderSide? _focusSide(Set<WidgetState> states, Color color) =>
    states.contains(WidgetState.focused)
    ? BorderSide(color: color, width: 2)
    : null;

/// 影院暗场：暖黑底，海报是视觉中心，只有一个琥珀色强调。
ThemeData buildTheme() {
  const scheme = ColorScheme(
    brightness: Brightness.dark,
    primary: CinemaColors.amber,
    onPrimary: CinemaColors.amberInk,
    primaryContainer: CinemaColors.amberDeep,
    onPrimaryContainer: CinemaColors.amber,
    secondary: CinemaColors.secondary,
    onSecondary: CinemaColors.amberInk,
    secondaryContainer: CinemaColors.ghost,
    onSecondaryContainer: CinemaColors.text0,
    error: CinemaColors.error,
    onError: CinemaColors.onError,
    surface: CinemaColors.bg,
    onSurface: CinemaColors.text1,
    onSurfaceVariant: CinemaColors.text2,
    surfaceContainerLowest: CinemaColors.bg,
    surfaceContainerLow: CinemaColors.surface1,
    surfaceContainer: CinemaColors.surface1,
    surfaceContainerHigh: CinemaColors.surface2,
    surfaceContainerHighest: CinemaColors.surface3,
    outline: CinemaColors.outline,
    outlineVariant: CinemaColors.line,
    inverseSurface: CinemaColors.inverse,
    onInverseSurface: CinemaColors.bg,
    inversePrimary: CinemaColors.amberOnInverse,
    surfaceTint: Colors.transparent,
  );

  final base = ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    // Web CanvasKit 不依赖运行时从 Google Fonts 下载中文字体；西文仍优先使用平台字体。
    fontFamilyFallback: const ['Noto Sans CJK SC'],
  );
  // 在默认字体上合并字号阶梯；字距统一归零，中文不需要拉开
  final merged = base.textTheme
      .merge(
        const TextTheme(
          headlineMedium: TextStyle(
            fontSize: 28,
            height: 36 / 28,
            fontWeight: FontWeight.w700,
            letterSpacing: 0,
          ),
          titleLarge: TextStyle(
            fontSize: 22,
            height: 30 / 22,
            fontWeight: FontWeight.w700,
            letterSpacing: 0,
          ),
          titleMedium: TextStyle(
            fontSize: 17,
            height: 24 / 17,
            fontWeight: FontWeight.w600,
            letterSpacing: 0,
          ),
          titleSmall: TextStyle(
            fontSize: 15,
            height: 22 / 15,
            fontWeight: FontWeight.w600,
            letterSpacing: 0,
          ),
          bodyLarge: TextStyle(fontSize: 16, height: 24 / 16, letterSpacing: 0),
          bodyMedium: TextStyle(
            fontSize: 15,
            height: 23 / 15,
            letterSpacing: 0,
          ),
          bodySmall: TextStyle(fontSize: 13, height: 20 / 13, letterSpacing: 0),
          labelLarge: TextStyle(
            fontSize: 15,
            fontWeight: FontWeight.w600,
            letterSpacing: 0,
          ),
          labelMedium: TextStyle(
            fontSize: 13,
            fontWeight: FontWeight.w500,
            letterSpacing: 0,
          ),
          labelSmall: TextStyle(
            fontSize: 12,
            fontWeight: FontWeight.w500,
            letterSpacing: 0,
          ),
        ),
      )
      .apply(bodyColor: CinemaColors.text1, displayColor: CinemaColors.text0);
  // 小字只用于元数据和说明，走次级色
  final text = merged.copyWith(
    bodySmall: merged.bodySmall!.copyWith(color: CinemaColors.text2),
  );

  final buttonShape = WidgetStateProperty.all(
    const RoundedRectangleBorder(borderRadius: _radius),
  );
  const buttonSize = WidgetStatePropertyAll(Size(64, 48));
  final buttonText = WidgetStatePropertyAll(
    text.labelLarge!.copyWith(fontWeight: FontWeight.w700),
  );

  return base.copyWith(
    textTheme: text,
    scaffoldBackgroundColor: CinemaColors.bg,
    focusColor: CinemaColors.amberSoft,
    hoverColor: CinemaColors.hover,
    pageTransitionsTheme: PageTransitionsTheme(
      builders: {
        for (final platform in TargetPlatform.values)
          platform: const CinemaPageTransitionsBuilder(),
      },
    ),
    appBarTheme: AppBarTheme(
      backgroundColor: CinemaColors.bg,
      foregroundColor: CinemaColors.text0,
      surfaceTintColor: Colors.transparent,
      scrolledUnderElevation: 0,
      centerTitle: false,
      titleTextStyle: text.titleLarge,
    ),
    filledButtonTheme: FilledButtonThemeData(
      style: ButtonStyle(
        minimumSize: buttonSize,
        shape: buttonShape,
        textStyle: buttonText,
        // 琥珀底上用暖白描边，琥珀环会和按钮融在一起
        side: WidgetStateProperty.resolveWith(
          (s) => _focusSide(s, CinemaColors.text0),
        ),
      ),
    ),
    outlinedButtonTheme: OutlinedButtonThemeData(
      style: ButtonStyle(
        minimumSize: buttonSize,
        shape: buttonShape,
        textStyle: buttonText,
        foregroundColor: const WidgetStatePropertyAll(CinemaColors.text0),
        side: WidgetStateProperty.resolveWith(
          (s) =>
              _focusSide(s, CinemaColors.amber) ??
              const BorderSide(color: CinemaColors.outline),
        ),
      ),
    ),
    textButtonTheme: TextButtonThemeData(
      style: ButtonStyle(
        minimumSize: const WidgetStatePropertyAll(Size(48, 44)),
        shape: buttonShape,
        textStyle: WidgetStatePropertyAll(text.labelLarge),
        side: WidgetStateProperty.resolveWith(
          (s) => _focusSide(s, CinemaColors.amber),
        ),
      ),
    ),
    iconButtonTheme: IconButtonThemeData(
      style: ButtonStyle(
        side: WidgetStateProperty.resolveWith(
          (s) => _focusSide(s, CinemaColors.amber),
        ),
      ),
    ),
    // 选中段同时改变底色、字重并带琥珀勾，不只靠颜色区分
    segmentedButtonTheme: SegmentedButtonThemeData(
      selectedIcon: const Icon(Icons.check, color: CinemaColors.amber),
      style: ButtonStyle(
        minimumSize: const WidgetStatePropertyAll(Size(48, 44)),
        shape: buttonShape,
        backgroundColor: WidgetStateProperty.resolveWith(
          (s) => s.contains(WidgetState.selected)
              ? CinemaColors.surface3
              : CinemaColors.surface1,
        ),
        foregroundColor: WidgetStateProperty.resolveWith(
          (s) => s.contains(WidgetState.selected)
              ? CinemaColors.text0
              : CinemaColors.text2,
        ),
        textStyle: WidgetStateProperty.resolveWith(
          (s) => text.labelLarge!.copyWith(
            fontWeight: s.contains(WidgetState.selected)
                ? FontWeight.w700
                : FontWeight.w500,
          ),
        ),
        side: WidgetStateProperty.resolveWith(
          (s) =>
              _focusSide(s, CinemaColors.amber) ??
              const BorderSide(color: CinemaColors.line),
        ),
      ),
    ),
    // 生效中的胶囊：琥珀浅底 + 琥珀字和边，和资料库的筛选胶囊一致
    chipTheme: ChipThemeData(
      shape: const StadiumBorder(),
      backgroundColor: CinemaColors.surface1,
      selectedColor: CinemaColors.amberSoft,
      checkmarkColor: CinemaColors.amber,
      labelStyle: WidgetStateTextStyle.resolveWith(
        (s) => text.labelMedium!.copyWith(
          color: s.contains(WidgetState.selected)
              ? CinemaColors.amber
              : CinemaColors.text1,
        ),
      ),
      side: WidgetStateBorderSide.resolveWith(
        (s) =>
            _focusSide(s, CinemaColors.amber) ??
            BorderSide(
              color: s.contains(WidgetState.selected)
                  ? CinemaColors.amberLine
                  : CinemaColors.line,
            ),
      ),
    ),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      fillColor: CinemaColors.surface1,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
      labelStyle: const TextStyle(color: CinemaColors.text2),
      floatingLabelStyle: WidgetStateTextStyle.resolveWith(
        (s) => TextStyle(
          color: s.contains(WidgetState.error)
              ? CinemaColors.error
              : s.contains(WidgetState.focused)
              ? CinemaColors.amber
              : CinemaColors.text2,
        ),
      ),
      border: const OutlineInputBorder(
        borderRadius: _radius,
        borderSide: BorderSide.none,
      ),
      enabledBorder: const OutlineInputBorder(
        borderRadius: _radius,
        borderSide: BorderSide.none,
      ),
      focusedBorder: const OutlineInputBorder(
        borderRadius: _radius,
        borderSide: BorderSide(color: CinemaColors.amber, width: 1.5),
      ),
      errorBorder: const OutlineInputBorder(
        borderRadius: _radius,
        borderSide: BorderSide(color: CinemaColors.error),
      ),
      focusedErrorBorder: const OutlineInputBorder(
        borderRadius: _radius,
        borderSide: BorderSide(color: CinemaColors.error, width: 1.5),
      ),
    ),
    textSelectionTheme: const TextSelectionThemeData(
      cursorColor: CinemaColors.amber,
      selectionColor: CinemaColors.selection,
      selectionHandleColor: CinemaColors.amber,
    ),
    navigationBarTheme: NavigationBarThemeData(
      height: 68,
      elevation: 0,
      backgroundColor: CinemaColors.surface1.withValues(alpha: 0.92),
      surfaceTintColor: Colors.transparent,
      indicatorColor: CinemaColors.amberSoft,
      indicatorShape: const StadiumBorder(),
      iconTheme: WidgetStateProperty.resolveWith(
        (s) => IconThemeData(
          color: s.contains(WidgetState.selected)
              ? CinemaColors.amber
              : CinemaColors.text2,
        ),
      ),
      labelTextStyle: WidgetStateProperty.resolveWith(
        (s) => s.contains(WidgetState.selected)
            ? text.labelSmall!.copyWith(
                color: CinemaColors.text0,
                fontWeight: FontWeight.w700,
              )
            : text.labelSmall!.copyWith(color: CinemaColors.text2),
      ),
    ),
    navigationRailTheme: NavigationRailThemeData(
      backgroundColor: CinemaColors.surface1,
      indicatorColor: CinemaColors.amberSoft,
      indicatorShape: const StadiumBorder(),
      selectedIconTheme: const IconThemeData(color: CinemaColors.amber),
      unselectedIconTheme: const IconThemeData(color: CinemaColors.text2),
      selectedLabelTextStyle: text.labelSmall!.copyWith(
        color: CinemaColors.text0,
        fontWeight: FontWeight.w700,
      ),
      unselectedLabelTextStyle: text.labelSmall!.copyWith(
        color: CinemaColors.text2,
      ),
    ),
    dividerTheme: const DividerThemeData(
      color: CinemaColors.line,
      space: 1,
      thickness: 1,
    ),
    listTileTheme: const ListTileThemeData(
      iconColor: CinemaColors.text2,
      selectedColor: CinemaColors.amber,
      contentPadding: EdgeInsets.symmetric(horizontal: CinemaSpacing.gutter),
      minVerticalPadding: 10,
    ),
    cardTheme: const CardThemeData(
      color: CinemaColors.surface2,
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.all(Radius.circular(CinemaRadius.card)),
      ),
    ),
    dialogTheme: DialogThemeData(
      backgroundColor: CinemaColors.surface2,
      surfaceTintColor: Colors.transparent,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.all(Radius.circular(CinemaRadius.dialog)),
      ),
      titleTextStyle: text.titleLarge,
      contentTextStyle: text.bodyMedium,
    ),
    bottomSheetTheme: const BottomSheetThemeData(
      backgroundColor: CinemaColors.surface2,
      surfaceTintColor: Colors.transparent,
      showDragHandle: true,
      dragHandleColor: CinemaColors.text2,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(
          top: Radius.circular(CinemaRadius.sheet),
        ),
      ),
    ),
    popupMenuTheme: PopupMenuThemeData(
      color: CinemaColors.surface2,
      surfaceTintColor: Colors.transparent,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.all(Radius.circular(CinemaRadius.card)),
      ),
      textStyle: text.bodyMedium,
    ),
    snackBarTheme: SnackBarThemeData(
      backgroundColor: CinemaColors.inverse,
      contentTextStyle: text.bodyMedium!.copyWith(color: CinemaColors.bg),
      actionTextColor: CinemaColors.amberOnInverse,
      behavior: SnackBarBehavior.floating,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.all(Radius.circular(CinemaRadius.card)),
      ),
    ),
    progressIndicatorTheme: const ProgressIndicatorThemeData(
      color: CinemaColors.amber,
      linearTrackColor: CinemaColors.track,
      linearMinHeight: 3,
    ),
    switchTheme: SwitchThemeData(
      thumbColor: WidgetStateProperty.resolveWith(
        (s) => s.contains(WidgetState.selected)
            ? CinemaColors.switchThumb
            : CinemaColors.text2,
      ),
      trackColor: WidgetStateProperty.resolveWith(
        (s) => s.contains(WidgetState.selected)
            ? CinemaColors.amber
            : CinemaColors.surface3,
      ),
      trackOutlineColor: const WidgetStatePropertyAll(Colors.transparent),
    ),
    tooltipTheme: TooltipThemeData(
      decoration: const BoxDecoration(
        color: CinemaColors.inverse,
        borderRadius: BorderRadius.all(Radius.circular(CinemaRadius.small)),
      ),
      textStyle: text.labelMedium!.copyWith(color: CinemaColors.bg),
    ),
  );
}
