import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/shell.dart';
import '../../app/theme.dart';
import '../../core/format.dart';
import '../../core/widgets/expandable_text.dart';
import '../../core/widgets/skeleton.dart';
import '../library/art_image.dart';
import '../library/browse_api.dart';
import '../library/poster_card.dart';
import '../../core/widgets/empty_state.dart';
import '../favorites/favorite_button.dart';
import '../favorites/favorites_api.dart';

/// 演员和导演：头像、生平，下面是本地资料库里能看到的作品（按年份从新到旧）。
/// TMDB 暂时拿不到资料时只有姓名和头像，作品照常显示（ADR 0014）。
class PersonPage extends ConsumerWidget {
  const PersonPage({super.key, required this.id});

  final int id;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final person = ref.watch(personProvider(id));
    final detail = person.value;
    final gutter = CinemaSpacing.gutterFor(
      wide: MediaQuery.sizeOf(context).width >= wideLayoutBreakpoint,
    );
    final works = detail == null
        ? const AsyncLoading<CardList>()
        : AsyncData(
            CardList(
              items: [for (final w in detail.works) w.card],
              total: detail.works.length,
            ),
          );
    final roles = {
      for (final w in detail?.works ?? <PersonWork>[]) w.card.id: w.role,
    };
    return Scaffold(
      body: CustomScrollView(
        slivers: [
          SliverAppBar(
            pinned: true,
            leading: BackButton(
              onPressed: () =>
                  context.canPop() ? context.pop() : context.go('/'),
            ),
            title: Text(detail?.name ?? ''),
          ),
          // 首次加载失败时头部不再显示骨架，整页只留下面的说明和重试
          if (detail != null || !person.hasError)
            SliverToBoxAdapter(
              child: Padding(
                padding: EdgeInsets.fromLTRB(gutter, 8, gutter, 0),
                child: detail == null
                    ? const _HeaderSkeleton()
                    : _Header(person: detail),
              ),
            ),
          if (detail != null && detail.works.isNotEmpty)
            SliverToBoxAdapter(
              child: Padding(
                // 网格自带 8 的上边距，合起来是区块标题与内容的 12
                padding: EdgeInsets.fromLTRB(
                  gutter,
                  CinemaSpacing.section,
                  gutter,
                  CinemaSpacing.heading - 8,
                ),
                child: Semantics(
                  header: true,
                  child: Text(
                    '作品',
                    style: Theme.of(context).textTheme.titleMedium!
                        .copyWith(color: CinemaColors.text0),
                  ),
                ),
              ),
            ),
          PagedPosterGrid(
            // 首次加载失败时整页只显示一个重试，不在头部和作品区各放一个
            value: person.hasError && detail == null
                ? AsyncError<CardList>(person.error!, person.stackTrace!)
                : works,
            heroPrefix: 'person:$id',
            padding: EdgeInsets.fromLTRB(gutter, 8, gutter, 0),
            subtitle: (media) => roles[media.id] ?? media.year?.toString(),
            onRetry: () => ref.invalidate(personProvider(id)),
            onLoadMore: () {},
            onRetryMore: () {},
            empty: const EmptyState(
              icon: Icons.movie_filter_outlined,
              title: '资料库里还没有这个人的作品',
              message: '可能在你没有权限的资料库里',
            ),
          ),
        ],
      ),
    );
  }
}

class _Header extends StatelessWidget {
  const _Header({required this.person});

  final PersonDetail person;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final original = person.originalName;
    final facts = _facts(person);
    final biography = person.biography;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            ClipOval(
              child: SizedBox.square(
                dimension: 96,
                child: ArtImage(
                  art: person.profile,
                  icon: Icons.person_outline,
                ),
              ),
            ),
            const SizedBox(width: 20),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    person.name,
                    style: textTheme.titleLarge!.copyWith(
                      color: CinemaColors.text0,
                    ),
                  ),
                  if (original != null && original != person.name) ...[
                    const SizedBox(height: 2),
                    Text(original, style: textTheme.bodyMedium),
                  ],
                  if (facts != null) ...[
                    const SizedBox(height: 4),
                    Text(
                      facts,
                      style: textTheme.bodySmall!.copyWith(
                        color: CinemaColors.text2,
                      ),
                    ),
                  ],
                ],
              ),
            ),
            const SizedBox(width: 8),
            FavoriteButton(
              kind: FavoriteKind.person,
              id: person.id,
              favorite: person.favorite,
              personId: person.id,
            ),
          ],
        ),
        if (biography != null && biography.isNotEmpty) ...[
          const SizedBox(height: 20),
          ExpandableText(
            biography,
            maxLines: 4,
            style: textTheme.bodyMedium!.copyWith(height: 1.6),
          ),
        ],
      ],
    );
  }
}

/// 生卒和出生地，如「1974年4月3日 出生 · 中国北京」。
String? _facts(PersonDetail person) {
  final birthday = _date(person.birthday);
  final deathday = _date(person.deathday);
  final life = switch ((birthday, deathday)) {
    (final born?, final died?) => '$born 至 $died',
    (null, final died?) => '$died 去世',
    (final born?, null) => '$born 出生',
    _ => null,
  };
  final parts = [?life, ?person.placeOfBirth];
  return parts.isEmpty ? null : parts.join(' · ');
}

/// 服务端给的 YYYY-MM-DD。生卒日期总带年份（formatDate 只对今年省略年份，
/// 所以拿公元元年当「今年」）；解析不了时原样显示。
String? _date(String? value) {
  if (value == null || value.isEmpty) return null;
  final date = DateTime.tryParse(value);
  return date == null ? value : formatDate(date, now: DateTime(0));
}

class _HeaderSkeleton extends StatelessWidget {
  const _HeaderSkeleton();

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: '正在加载',
      child: Row(
        children: [
          const ClipOval(
            child: SizedBox.square(dimension: 96, child: Skeleton()),
          ),
          const SizedBox(width: 20),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                FractionallySizedBox(
                  widthFactor: 0.5,
                  child: SizedBox(
                    height: 22,
                    child: Skeleton(
                      borderRadius: BorderRadius.circular(CinemaRadius.tiny),
                    ),
                  ),
                ),
                const SizedBox(height: 10),
                FractionallySizedBox(
                  widthFactor: 0.7,
                  child: SizedBox(
                    height: 12,
                    child: Skeleton(
                      borderRadius: BorderRadius.circular(CinemaRadius.tiny),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
