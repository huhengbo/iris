import 'package:flutter/material.dart';
import 'package:iris/features/library/art_image.dart';
import 'package:iris/features/library/libraries_page.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/fake_api.dart';

void main() {
  testWidgets('shows the visible libraries and their counts', (tester) async {
    final api = await pumpAdmin(tester, const LibrariesPage(), (api) {
      api.on('GET /api/libraries', [
        {
          'id': 1,
          'name': '电影',
          'kind': 'movie',
          'item_count': 12,
          'cover': <Object>[],
        },
        {
          'id': 2,
          'name': '剧集',
          'kind': 'series',
          'item_count': 4,
          'cover': <Object>[],
        },
      ]);
    });

    expect(find.text('电影'), findsOneWidget);
    expect(find.text('12 部'), findsOneWidget);
    expect(find.text('剧集'), findsOneWidget);
    expect(find.text('4 部'), findsOneWidget);
    expect(api.requests, ['GET /api/libraries']);
  });

  testWidgets('shows the cover thumbnail only when the library has one', (
    tester,
  ) async {
    await pumpAdmin(tester, const LibrariesPage(), (api) {
      api.on('GET /api/libraries', [
        {
          'id': 1,
          'name': '电影',
          'kind': 'movie',
          'item_count': 12,
          'cover': [
            {'width': 780, 'url': '/api/images/a/780.jpg'},
          ],
        },
        {'id': 2, 'name': '剧集', 'kind': 'series', 'item_count': 4, 'cover': []},
      ]);
    });
    expect(find.byType(ArtImage), findsOneWidget);
    expect(find.byIcon(Icons.tv_outlined), findsOneWidget);
    expect(find.byIcon(Icons.movie_outlined), findsNothing);
  });
}
