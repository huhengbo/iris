import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/features/admin/library_recommendations_page.dart';

import 'support/fake_api.dart';

Map<String, Object?> hub(
  int id,
  String kind,
  String title, {
  int position = 0,
  bool home = true,
  bool library = true,
  String? arg,
}) => {
  'id': id,
  'library_id': 1,
  'kind': kind,
  'arg': arg,
  'position': position,
  'show_home': home,
  'show_library': library,
  'title': title,
};

void main() {
  testWidgets('switches and order are saved together', (tester) async {
    final rows = [
      hub(11, 'latest', '最近添加的电影', home: true, library: true),
      hub(
        12,
        'genre',
        '科幻 · 电影',
        position: 1,
        home: false,
        library: true,
        arg: '科幻',
      ),
    ];
    final api = await pumpAdmin(
      tester,
      const LibraryRecommendationsPage(id: 1, name: '电影'),
      (api) {
        api.on('GET /api/admin/libraries/1/hubs', rows);
        api.routes['PUT /api/admin/libraries/1/hubs'] = (body) {
          final sent = body! as List<dynamic>;
          return (
            200,
            [
              for (final (index, raw) in sent.indexed)
                {
                  ...rows.firstWhere(
                    (row) =>
                        row['kind'] == (raw as Map<String, dynamic>)['kind'],
                  ),
                  ...raw,
                  'position': index,
                },
            ],
          );
        };
      },
    );

    expect(find.text('最近添加的电影'), findsOneWidget);
    expect(find.text('科幻 · 电影'), findsOneWidget);
    expect(find.widgetWithText(SwitchListTile, '在首页显示'), findsNWidgets(2));

    // 第一行不再出现在首页，并移到第二行
    await tester.tap(find.widgetWithText(SwitchListTile, '在首页显示').first);
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('下移').first);
    await tester.pumpAndSettle();
    await tester.tap(find.text('保存'));
    await tester.pumpAndSettle();

    expect(api.bodies['PUT /api/admin/libraries/1/hubs'], [
      {'kind': 'genre', 'arg': '科幻', 'show_home': false, 'show_library': true},
      {'kind': 'latest', 'arg': null, 'show_home': false, 'show_library': true},
    ]);
    expect(find.text('已保存推荐行'), findsOneWidget);
  });

  testWidgets('the second switch controls the recommendation tab', (
    tester,
  ) async {
    final api = await pumpAdmin(
      tester,
      const LibraryRecommendationsPage(id: 1, name: '电影'),
      (api) {
        api.on('GET /api/admin/libraries/1/hubs', [
          hub(11, 'latest', '最近添加的电影'),
        ]);
        api.on('PUT /api/admin/libraries/1/hubs', [
          hub(11, 'latest', '最近添加的电影', home: true, library: false),
        ]);
      },
    );
    await tester.tap(find.widgetWithText(SwitchListTile, '在推荐标签显示'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('保存'));
    await tester.pumpAndSettle();

    expect(api.bodies['PUT /api/admin/libraries/1/hubs'], [
      {'kind': 'latest', 'arg': null, 'show_home': true, 'show_library': false},
    ]);
  });

  testWidgets('a failed save keeps the changes and shows the reason', (
    tester,
  ) async {
    final api = await pumpAdmin(
      tester,
      const LibraryRecommendationsPage(id: 1, name: '电影'),
      (api) {
        api
          ..on('GET /api/admin/libraries/1/hubs', [
            hub(11, 'latest', '最近添加的电影'),
          ])
          ..on('PUT /api/admin/libraries/1/hubs', {
            'error': {'code': 'invalid_library_hub', 'message': '推荐行重复'},
          }, status: 400);
      },
    );
    await tester.tap(find.widgetWithText(SwitchListTile, '在首页显示'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('保存'));
    await tester.pumpAndSettle();

    expect(find.text('推荐行重复'), findsOneWidget);
    expect(
      tester
          .widget<SwitchListTile>(find.widgetWithText(SwitchListTile, '在首页显示'))
          .value,
      isFalse,
    );
    expect(api.requests, contains('PUT /api/admin/libraries/1/hubs'));
  });
}
