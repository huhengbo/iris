import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/core/widgets/skeleton.dart';
import 'package:iris/features/home/home_page.dart';

import 'support/fake_api.dart';

Map<String, Object?> _card(int id, String title) => {
  'id': id,
  'library_id': 1,
  'kind': 'movie',
  'title': title,
  'original_title': null,
  'year': 2019,
  'rating': 7.9,
  'genres': <String>[],
  'runtime': 125,
  'poster': <Object>[],
  'backdrop': <Object>[],
  'watched': false,
};

const _movies = {
  'id': 1,
  'name': '电影',
  'kind': 'movie',
  'item_count': 2,
  'cover': <Object>[],
};

Map<String, Object?> _home(String title) => {
  'libraries': [_movies],
  'recent': <Object>[],
  'favorites': <Object>[],
  'watchlist': <Object>[],
  'shelves': [
    {
      'library': _movies,
      'hub': {
        'kind': 'latest',
        'arg': null,
        'title': '最近添加的电影',
        'items': [_card(10, title), _card(11, '星际穿越')],
        'recent': <Object>[],
      },
    },
  ],
};

/// 用一个开关模拟「进入页面 / 离开页面」：离开后首页的 autoDispose provider 没有监听者。
class _Host extends StatelessWidget {
  const _Host(this.visible);

  final ValueNotifier<bool> visible;

  @override
  Widget build(BuildContext context) => ValueListenableBuilder<bool>(
    valueListenable: visible,
    builder: (_, show, _) => show ? const HomePage() : const SizedBox(),
  );
}

void main() {
  late ValueNotifier<bool> visible;
  late int calls;
  late String title;

  Future<FakeAdminApi> pumpHome(WidgetTester tester) async {
    visible = ValueNotifier(true);
    addTearDown(visible.dispose);
    calls = 0;
    title = '流浪地球';
    return pumpAdmin(tester, _Host(visible), (api) {
      api.routes['GET /api/home'] = (_) {
        calls++;
        return (200, _home(title));
      };
    });
  }

  Future<void> leaveFor(WidgetTester tester, Duration away) async {
    visible.value = false;
    await tester.pump();
    expect(find.byType(HomePage), findsNothing);
    await tester.pump(away);
  }

  testWidgets(
    'returning within 5 minutes shows cached content, then refreshes',
    (tester) async {
      await pumpHome(tester);
      expect(find.text('最近添加的电影'), findsOneWidget);
      expect(calls, 1);

      await leaveFor(tester, const Duration(minutes: 4, seconds: 50));
      visible.value = true;
      await tester.pump();

      // 第一帧就是旧内容，不是整页骨架
      expect(find.byType(Skeleton), findsNothing);
      expect(find.text('最近添加的电影'), findsOneWidget);
      expect(find.text('星际穿越'), findsOneWidget);
      expect(calls, 1, reason: '请求在第一帧之后才发出');
      await tester.pumpAndSettle();
      expect(find.text('最近添加的电影'), findsOneWidget);
      expect(calls, 2, reason: '返回页面时后台静默刷新一次');
    },
  );

  testWidgets('the silent refresh replaces cached content without a skeleton', (
    tester,
  ) async {
    await pumpHome(tester);
    await leaveFor(tester, const Duration(minutes: 1));

    title = '三体';
    visible.value = true;
    await tester.pump();
    expect(find.text('流浪地球'), findsWidgets, reason: '先显示旧内容');
    expect(find.byType(Skeleton), findsNothing);

    await tester.pumpAndSettle();
    expect(find.byType(Skeleton), findsNothing);
    expect(find.text('三体'), findsWidgets, reason: '刷新结果静默替换');
    expect(find.text('流浪地球'), findsNothing);
  });

  testWidgets('a failed silent refresh keeps showing the cached content', (
    tester,
  ) async {
    final api = await pumpHome(tester);
    await leaveFor(tester, const Duration(minutes: 1));

    api.routes['GET /api/home'] = (_) {
      calls++;
      return (
        500,
        {
          'error': {'code': 'server_error', 'message': '服务出错'},
        },
      );
    };
    visible.value = true;
    await tester.pumpAndSettle();

    // Riverpod 会按退避策略自动重试失败的请求，这里只关心页面不被错误替换
    expect(calls, greaterThanOrEqualTo(2));
    expect(find.text('流浪地球'), findsWidgets);
    expect(find.byType(Skeleton), findsNothing);
  });

  testWidgets('after the 5 minute window the page loads from scratch', (
    tester,
  ) async {
    await pumpHome(tester);
    await leaveFor(tester, const Duration(minutes: 5, seconds: 1));

    visible.value = true;
    await tester.pump();
    expect(find.byType(Skeleton), findsWidgets);
    expect(find.text('最近添加的电影'), findsNothing);
    await tester.pumpAndSettle();
    expect(calls, 2, reason: '缓存过期后重新请求');
    expect(find.text('最近添加的电影'), findsOneWidget);
  });

  testWidgets('pull to refresh still does a full reload after returning', (
    tester,
  ) async {
    await pumpHome(tester);
    await leaveFor(tester, const Duration(minutes: 1));
    visible.value = true;
    await tester.pumpAndSettle();
    expect(calls, 2, reason: '返回时已静默刷新一次');
    expect(find.text('流浪地球'), findsWidgets);

    title = '三体';
    await tester.dragFrom(const Offset(195, 700), const Offset(0, 500));
    await tester.pumpAndSettle();
    await tester.pumpAndSettle();

    expect(calls, 3, reason: '下拉刷新再请求一次');
    expect(find.text('三体'), findsWidgets);
    expect(find.text('流浪地球'), findsNothing);
  });
}
