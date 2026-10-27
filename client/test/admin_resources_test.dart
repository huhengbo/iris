import 'package:iris/features/admin/resource_source_detail_page.dart';
import 'package:iris/features/admin/resources_page.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'support/fake_api.dart';

const _base = '/api/admin/resources';

Map<String, dynamic> _source(
  int id,
  String name, {
  String plugin = 'source.local',
  bool enabled = true,
  String status = 'ok',
  String? error,
  int folders = 0,
}) => {
  'id': id,
  'plugin_id': plugin,
  'plugin_name': plugin == 'source.115' ? '115 网盘' : '本地目录',
  'plugin_enabled': true,
  'name': name,
  'enabled': enabled,
  'config': plugin == 'source.115'
      ? <String, dynamic>{}
      : {'root': '/data/media/$name'},
  'status': status,
  'last_error': error,
  'checked_at': null,
  'folder_count': folders,
  'supports_delete': plugin == 'source.115',
};

const _providers = [
  {
    'plugin_id': 'source.115',
    'name': '115 网盘',
    'enabled': true,
    'multiple': false,
    'supports_delete': true,
    'config_fields': <Object>[],
    'source_count': 1,
  },
  {
    'plugin_id': 'source.local',
    'name': '本地目录',
    'enabled': true,
    'multiple': true,
    'supports_delete': false,
    'config_fields': [
      {
        'key': 'root',
        'label': '服务器目录',
        'help': '必须位于 /data/media 之内',
        'required': true,
        'default': '/data/media',
      },
    ],
    'source_count': 0,
  },
];

void main() {
  testWidgets('lists sources with status and last error', (tester) async {
    await pumpAdmin(
      tester,
      const ResourcesPage(),
      (api) => api.on('GET $_base', [
        _source(1, '115 网盘', plugin: 'source.115', folders: 3),
        _source(2, 'NAS', status: 'error', error: '服务器目录不存在或没有挂载'),
      ]),
    );
    expect(find.text('资源来源'), findsOneWidget);
    expect(find.text('正常'), findsOneWidget);
    expect(find.textContaining('服务器目录不存在或没有挂载'), findsOneWidget);
  });

  testWidgets('create dialog only offers creatable plugins and shows errors', (
    tester,
  ) async {
    final api = await pumpAdmin(tester, const ResourcesPage(), (api) {
      api.on('GET $_base', [_source(1, '115 网盘', plugin: 'source.115')]);
      api.on('GET $_base/providers', _providers);
      api.on('POST $_base', {
        'error': {
          'code': 'invalid_source_config',
          'message': '服务器目录必须位于 /data/media 之内',
        },
      }, status: 400);
    });
    await tester.tap(find.text('新建来源'));
    await tester.pumpAndSettle();

    // 115 只能有一个来源，已经有了就不再出现在类型里
    final dialog = find.byType(AlertDialog);
    expect(
      find.descendant(of: dialog, matching: find.text('115 网盘')),
      findsNothing,
    );
    // 名称默认填插件名
    expect(find.widgetWithText(TextField, '本地目录'), findsOneWidget);
    expect(find.text('必须位于 /data/media 之内'), findsOneWidget);
    await tester.enterText(find.widgetWithText(TextField, '服务器目录'), '/etc');
    await tester.tap(find.text('创建'));
    await tester.pumpAndSettle();

    expect(api.bodies['POST $_base'], {
      'plugin_id': 'source.local',
      'name': '本地目录',
      'config': {'root': '/etc'},
    });
    expect(find.text('服务器目录必须位于 /data/media 之内'), findsOneWidget);
    expect(find.byType(AlertDialog), findsOneWidget);
  });

  testWidgets('detail confirms disabling a source in use', (tester) async {
    final api = await pumpAdmin(
      tester,
      const ResourceSourceDetailPage(sourceId: 2),
      (api) {
        api.on('GET $_base', [_source(2, 'NAS', folders: 1)]);
        api.on('GET $_base/providers', _providers);
        api.on('PUT $_base/2/enabled', _source(2, 'NAS', enabled: false));
      },
    );
    expect(find.widgetWithText(TextField, '服务器目录'), findsOneWidget);
    // 还有资料库在用，不能删除
    final delete = find.widgetWithText(OutlinedButton, '删除来源');
    await tester.ensureVisible(delete);
    expect(tester.widget<OutlinedButton>(delete).onPressed, isNull);

    await tester.tap(find.byType(Switch));
    await tester.pumpAndSettle();
    expect(find.text('停用「NAS」？'), findsOneWidget);
    await tester.tap(find.text('停用'));
    await tester.pumpAndSettle();
    expect(api.bodies['PUT $_base/2/enabled'], {'enabled': false});
  });
}
