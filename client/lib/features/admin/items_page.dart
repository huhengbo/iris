import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/theme.dart';
import '../../core/widgets/async_view.dart';
import '../../core/widgets/focus_ring.dart';
import '../library/art_image.dart';
import 'admin_widgets.dart';
import 'catalog_api.dart';
import '../../core/widgets/empty_state.dart';

/// 搜索框停止输入多久后再请求
const _searchDebounce = Duration(milliseconds: 300);

/// 管理后台的条目页：按片名搜索全部已入库的电影和剧集，进入详情做刷新、重新匹配等操作。
class ItemsPage extends ConsumerStatefulWidget {
  const ItemsPage({super.key});

  @override
  ConsumerState<ItemsPage> createState() => _ItemsPageState();
}

class _ItemsPageState extends ConsumerState<ItemsPage> {
  final _search = TextEditingController();
  Timer? _debounce;
  String _query = '';
  int _offset = 0;

  @override
  void dispose() {
    _debounce?.cancel();
    _search.dispose();
    super.dispose();
  }

  void _onChanged(String value) {
    _debounce?.cancel();
    _debounce = Timer(_searchDebounce, () {
      if (!mounted) return;
      setState(() {
        _query = value.trim();
        _offset = 0;
      });
    });
  }

  @override
  Widget build(BuildContext context) {
    final provider = adminItemsProvider((query: _query, offset: _offset));
    return Scaffold(
      appBar: AppBar(title: const Text('条目')),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(
              CinemaSpacing.gutter,
              8,
              CinemaSpacing.gutter,
              8,
            ),
            child: TextField(
              controller: _search,
              maxLength: 100,
              textInputAction: TextInputAction.search,
              decoration: const InputDecoration(
                prefixIcon: Icon(Icons.search),
                hintText: '搜索片名、原名或英文名',
                counterText: '',
              ),
              onChanged: _onChanged,
            ),
          ),
          Expanded(
            child: AsyncView(
              value: ref.watch(provider),
              onRetry: () => ref.invalidate(provider),
              builder: (page) {
                if (page.total == 0) {
                  return EmptyState(
                    icon: Icons.movie_outlined,
                    title: _query.isEmpty ? '还没有条目' : '没有找到「$_query」',
                    message: _query.isEmpty
                        ? '同步并匹配元数据后，电影和剧集会出现在这里。'
                        : '换个片名试试；没匹配上的作品在「待处理」里。',
                  );
                }
                return ListView(
                  padding: const EdgeInsets.only(bottom: 24),
                  children: [
                    for (final item in page.items) _ItemTile(item: item),
                    Pager(
                      offset: _offset,
                      total: page.total,
                      onChanged: (value) => setState(() => _offset = value),
                    ),
                  ],
                );
              },
            ),
          ),
        ],
      ),
    );
  }
}

class _ItemTile extends StatelessWidget {
  const _ItemTile({required this.item});

  final AdminItem item;

  @override
  Widget build(BuildContext context) {
    return FocusRing(
      child: ListTile(
        leading: ClipRRect(
          borderRadius: BorderRadius.circular(CinemaRadius.small),
          child: SizedBox(
            width: 40,
            height: 60,
            child: ArtImage(art: item.poster),
          ),
        ),
        title: Text(
          item.year == null ? item.title : '${item.title}（${item.year}）',
        ),
        subtitle: Text(
          '${item.libraryName} · ${item.versionCount} 个版本',
          style: const TextStyle(color: CinemaColors.text2),
        ),
        trailing: const Icon(Icons.chevron_right),
        onTap: () => context.push('/settings/manage/items/${item.id}'),
      ),
    );
  }
}
