part of 'cast_page.dart';

/// 电视图标 + 名字 + 当前状态。播放中图标换成琥珀。
class _DeviceHeader extends StatelessWidget {
  const _DeviceHeader({
    required this.name,
    required this.status,
    required this.active,
  });

  final String name;
  final String status;
  final bool active;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    return Column(
      children: [
        AnimatedContainer(
          duration: Motion.short,
          width: 88,
          height: 88,
          decoration: BoxDecoration(
            color: active ? CinemaColors.amberSoft : CinemaColors.surface2,
            shape: BoxShape.circle,
          ),
          child: Icon(
            active ? Icons.cast_connected_rounded : Icons.cast_rounded,
            size: 38,
            color: active ? CinemaColors.amber : CinemaColors.text1,
          ),
        ),
        const SizedBox(height: 14),
        Text(
          name,
          textAlign: TextAlign.center,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: textTheme.titleMedium!.copyWith(color: CinemaColors.text0),
        ),
        const SizedBox(height: 2),
        Semantics(
          liveRegion: true,
          child: Text(status, style: textTheme.bodySmall),
        ),
      ],
    );
  }
}
