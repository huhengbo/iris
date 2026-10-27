import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/app/tv.dart';
import 'package:iris/core/format.dart';
import 'package:iris/core/providers.dart';
import 'package:iris/features/admin/plugin_bridge.dart';
import 'package:iris/features/admin/plugin_detail_page.dart';
import 'package:iris/features/admin/plugin_frame/plugin_frame.dart';
import 'package:iris/features/admin/plugin_frame/plugin_frame_io.dart';
import 'package:iris/features/admin/plugins_page.dart';

import 'support/fake_api.dart';

const _plugins = '/api/admin/plugins';
const _session = '/api/admin/plugins/example.hello/session';
const _settings = '/api/admin/settings';

Map<String, dynamic> _plugin({
  String id = 'example.hello',
  String name = '示例插件',
  String description = '一个用来测试的插件。',
  String category = 'metadata',
  String proxyMode = 'inherit',
  String? proxy,
  bool enabled = true,
  String? state,
  List<Map<String, String>> pages = const [
    {'id': 'settings', 'title': '设置'},
  ],
}) => {
  'id': id,
  'name': name,
  'version': '1.0.0',
  'sdk_version': '1',
  'provides': ['admin.ui'],
  'requires': ['config', 'secrets'],
  'trust': 'external',
  'built_in': false,
  'enabled': enabled,
  'state': state ?? (enabled ? 'running' : 'stopped'),
  'updated_at': '2026-10-04T00:00:00+00:00',
  'pages': pages,
  'description': description,
  'category': category,
  'proxy_mode': proxyMode,
  'proxy': proxy,
};

Map<String, dynamic> _settingsBody({String? proxy}) => {
  'server': {'id': 'abc123', 'name': '家里的影院', 'public_url': null},
  'plugins': {'proxy': proxy},
  'library': {'extras_min_mb': 200, 'missing_grace_days': 7},
};

Map<String, dynamic> _sessionBody(List<Map<String, String>> pages) => {
  'token': 'plugin-token',
  'expires_at': '2026-10-04T00:15:00+00:00',
  'pages': [
    for (final page in pages)
      {...page, 'url': '/plugins/example.hello/ui/${page['id']}.html'},
  ],
};

class _Recorder {
  final sent = <Map<String, Object?>>[];
  final toasts = <String>[];
  final errors = <Object>[];
  var issued = 0;

  PluginBridge bridge({bool fail = false}) => PluginBridge(
    pluginId: 'example.hello',
    issueToken: () async {
      if (fail) throw StateError('offline');
      issued++;
      return 'token-$issued';
    },
    onToast: toasts.add,
    onError: errors.add,
  )..attach(sent.add);
}

void main() {
  group('PluginBridge', () {
    test('页面就绪后发送令牌和主题', () async {
      final recorder = _Recorder();
      await recorder.bridge().handle({'type': 'iris:ready'});

      expect(recorder.sent.single, {
        'type': 'iris:init',
        'pluginId': 'example.hello',
        'token': 'token-1',
        'theme': pluginTheme(),
        'locale': 'zh-CN',
      });
    });

    test('Android 通道传来的 JSON 字符串也能处理', () async {
      final recorder = _Recorder();
      await recorder.bridge().handle(
        jsonEncode({'type': 'iris:refresh-token', 'requestId': '7'}),
      );

      expect(recorder.sent.single, {
        'type': 'iris:token',
        'requestId': '7',
        'token': 'token-1',
      });
    });

    test('提示文本去空白并截断', () async {
      final recorder = _Recorder();
      final bridge = recorder.bridge();
      await bridge.handle({'type': 'iris:toast', 'message': '  已保存 '});
      await bridge.handle({'type': 'iris:toast', 'message': 'x' * 500});
      await bridge.handle({'type': 'iris:toast', 'message': '   '});

      expect(recorder.toasts, ['已保存', '${'x' * 200}…']);
      expect(recorder.sent, isEmpty);
    });

    test('忽略不认识或格式不对的消息', () async {
      final recorder = _Recorder();
      final bridge = recorder.bridge();
      for (final message in <Object?>[
        null,
        42,
        'not json',
        {'type': 'iris:unknown'},
        {'kind': 'iris:ready'},
        {'type': 'iris:refresh-token'},
        {'type': 'iris:refresh-token', 'requestId': 'x' * 65},
        {'type': 'iris:toast', 'message': 3},
      ]) {
        await bridge.handle(message);
      }

      expect(recorder.sent, isEmpty);
      expect(recorder.toasts, isEmpty);
      expect(recorder.issued, 0);
    });

    test('申请令牌失败时报告错误且不发消息', () async {
      final recorder = _Recorder();
      await recorder.bridge(fail: true).handle({'type': 'iris:ready'});

      expect(recorder.sent, isEmpty);
      expect(recorder.errors.single, isA<StateError>());
    });

    test('断开后不再向页面发消息', () async {
      final recorder = _Recorder();
      final bridge = recorder.bridge()..detach();
      await bridge.handle({'type': 'iris:ready'});

      expect(recorder.sent, isEmpty);
    });
  });

  test('主题色转成 CSS rgba', () {
    expect(cssColor(CinemaColors.amber), 'rgba(233, 165, 72, 1)');
    expect(cssColor(const Color(0x33FFECD6)), 'rgba(255, 236, 214, 0.2)');
  });

  test('WebView 只允许停留在插件的 ui 目录', () {
    expect(
      pluginPagePrefix(
        Uri.parse('http://ui:8080/plugins/example.hello/ui/a/index.html?x=1'),
      ),
      'http://ui:8080/plugins/example.hello/ui/',
    );
  });

  group('插件详情页', () {
    late List<(Uri, PluginBridge)> frames;

    Future<FakeAdminApi> pump(
      WidgetTester tester,
      void Function(FakeAdminApi api) setup, {
      bool television = false,
      String? pageId,
    }) {
      frames = [];
      return pumpAdmin(
        tester,
        PluginDetailPage(pluginId: 'example.hello', pageId: pageId),
        setup,
        overrides: [
          televisionProvider.overrideWithValue(television),
          pluginFrameProvider.overrideWithValue((url, bridge) {
            frames.add((url, bridge));
            return Text('frame $url');
          }),
        ],
      );
    }

    testWidgets('启用的插件打开自己的页面并可切换', (tester) async {
      final pages = [
        {'id': 'settings', 'title': '设置'},
        {'id': 'logs', 'title': '日志'},
      ];
      final api = await pump(tester, (api) {
        api.on('GET $_plugins', {
          'plugins': [_plugin(pages: pages)],
        });
        api.on('POST $_session', _sessionBody(pages));
      });

      expect(find.text('示例插件'), findsOneWidget);
      expect(find.textContaining('第三方'), findsOneWidget);
      expect(
        find.text('frame http://test/plugins/example.hello/ui/settings.html'),
        findsOneWidget,
      );

      await tester.tap(find.text('日志'));
      await tester.pumpAndSettle();
      expect(
        find.text('frame http://test/plugins/example.hello/ui/logs.html'),
        findsOneWidget,
      );

      // 页面要令牌时，宿主重新向后端申请
      final sent = <Map<String, Object?>>[];
      frames.last.$2.attach(sent.add);
      await tester.runAsync(
        () => frames.last.$2.handle({'type': 'iris:ready'}),
      );
      expect(sent.single['token'], 'plugin-token');
      expect(api.requests.where((r) => r == 'POST $_session'), hasLength(2));
    });

    testWidgets('从来源状态打开 page 指定的页面', (tester) async {
      final pages = [
        {'id': 'settings', 'title': '设置'},
        {'id': 'login', 'title': '登录'},
      ];
      await pump(tester, (api) {
        api.on('GET $_plugins', {
          'plugins': [_plugin(pages: pages)],
        });
        api.on('POST $_session', _sessionBody(pages));
      }, pageId: 'login');

      expect(
        find.text('frame http://test/plugins/example.hello/ui/login.html'),
        findsOneWidget,
      );
    });

    testWidgets('停用的插件不打开页面', (tester) async {
      final api = await pump(tester, (api) {
        api.on('GET $_plugins', {
          'plugins': [_plugin(enabled: false)],
        });
      });

      expect(find.text('插件已停用'), findsOneWidget);
      expect(frames, isEmpty);
      expect(api.requests, isNot(contains('POST $_session')));
    });

    testWidgets('启动失败时提示原因并可重试', (tester) async {
      final api = await pump(tester, (api) {
        api.on('GET $_plugins', {
          'plugins': [_plugin(state: 'failed')],
        });
        api.on('PUT $_plugins/example.hello/enabled', _plugin());
      });

      expect(find.text('启动失败'), findsOneWidget);
      expect(find.text('启动失败，请查看服务端日志'), findsOneWidget);
      expect(find.text('插件启动失败'), findsOneWidget);
      expect(frames, isEmpty);

      await tester.tap(find.text('重试启动'));
      await tester.pumpAndSettle();
      expect(api.bodies['PUT $_plugins/example.hello/enabled'], {
        'enabled': true,
      });
    });

    testWidgets('未安装的插件不能启用，可以移除记录', (tester) async {
      final api = await pump(tester, (api) {
        api.on('GET $_plugins', {
          'plugins': [_plugin(state: 'not_installed')],
        });
      });

      expect(find.text('未安装'), findsOneWidget);
      expect(find.text('当前进程没有安装这个插件，不能启用。'), findsOneWidget);
      expect(find.byType(Switch), findsNothing);
      expect(frames, isEmpty);

      await tester.tap(find.text('移除记录'));
      await tester.pumpAndSettle();
      expect(find.text('移除「示例插件」的记录？'), findsOneWidget);
      expect(
        api.requests.where((request) => request.startsWith('DELETE')),
        isEmpty,
      );

      api.on('DELETE $_plugins/example.hello', null, status: 204);
      await tester.tap(find.text('移除'));
      await tester.pumpAndSettle();
      expect(api.requests, contains('DELETE $_plugins/example.hello'));
    });

    testWidgets('网络页显示全局代理并保存自定义代理', (tester) async {
      final api = await pump(tester, (api) {
        api.on('GET $_plugins', {
          'plugins': [_plugin()],
        });
        api.on(
          'POST $_session',
          _sessionBody(const [
            {'id': 'settings', 'title': '设置'},
          ]),
        );
        api.on(
          'GET $_settings',
          _settingsBody(proxy: 'http://user:***@global:7890'),
        );
        api.on(
          'PUT $_plugins/example.hello/proxy',
          _plugin(proxyMode: 'custom', proxy: 'http://user:***@plugin:1080'),
        );
      });

      await tester.tap(find.text('网络'));
      await tester.pumpAndSettle();

      expect(find.text('沿用全局代理'), findsOneWidget);
      expect(find.text('不走代理'), findsOneWidget);
      expect(find.text('自定义'), findsOneWidget);
      expect(
        find.textContaining('http://user:***@global:7890'),
        findsOneWidget,
      );
      expect(find.text('全局代理在 设置 › 插件网络代理 里修改'), findsOneWidget);

      await tester.tap(find.text('自定义'));
      await tester.pumpAndSettle();
      await tester.enterText(
        find.widgetWithText(TextField, '代理地址'),
        'http://user:secret@plugin:1080',
      );
      await tester.tap(find.widgetWithText(FilledButton, '保存'));
      await tester.pumpAndSettle();

      expect(api.bodies['PUT $_plugins/example.hello/proxy'], {
        'mode': 'custom',
        'proxy': 'http://user:secret@plugin:1080',
      });
      expect(find.text('已保存'), findsOneWidget);
    });

    testWidgets('自定义代理无效时显示服务端原因', (tester) async {
      final api = await pump(tester, (api) {
        api.on('GET $_plugins', {
          'plugins': [_plugin()],
        });
        api.on(
          'POST $_session',
          _sessionBody(const [
            {'id': 'settings', 'title': '设置'},
          ]),
        );
        api.on('PUT $_plugins/example.hello/proxy', {
          'error': {'code': 'invalid_proxy', 'message': '代理地址无效'},
        }, status: 400);
      });

      await tester.tap(find.text('网络'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('自定义'));
      await tester.pumpAndSettle();
      await tester.enterText(
        find.widgetWithText(TextField, '代理地址'),
        'not-a-proxy',
      );
      await tester.tap(find.widgetWithText(FilledButton, '保存'));
      await tester.pumpAndSettle();

      expect(api.bodies['PUT $_plugins/example.hello/proxy'], {
        'mode': 'custom',
        'proxy': 'not-a-proxy',
      });
      expect(find.text('代理地址无效'), findsOneWidget);
    });

    testWidgets('关于页显示插件的信息和能力', (tester) async {
      await pump(tester, (api) {
        api.on('GET $_plugins', {
          'plugins': [
            _plugin(description: '从 TMDB 获取影片简介和海报。', proxyMode: 'none'),
          ],
        });
        api.on(
          'POST $_session',
          _sessionBody(const [
            {'id': 'settings', 'title': '设置'},
          ]),
        );
      });

      await tester.tap(find.text('关于'));
      await tester.pumpAndSettle();

      expect(find.text('从 TMDB 获取影片简介和海报。'), findsOneWidget);
      expect(find.text('提供的能力'), findsOneWidget);
      expect(find.text('admin.ui'), findsOneWidget);
      expect(find.text('使用的宿主能力'), findsOneWidget);
      expect(find.text('config、secrets'), findsOneWidget);
      expect(find.text('信任级别'), findsOneWidget);
      expect(find.text('第三方（external）'), findsOneWidget);
      expect(find.text('更新时间'), findsOneWidget);
      expect(
        find.text(
          formatDateTime(DateTime.parse('2026-10-04T00:00:00+00:00').toLocal()),
        ),
        findsOneWidget,
      );
    });

    testWidgets('电视上提示到别处配置，不提供网络设置', (tester) async {
      await pump(tester, television: true, (api) {
        api.on('GET $_plugins', {
          'plugins': [_plugin()],
        });
      });

      expect(find.text('请在网页端或手机上配置'), findsOneWidget);
      expect(find.text('网络'), findsNothing);
      expect(find.text('关于'), findsOneWidget);
      expect(frames, isEmpty);
    });

    testWidgets('没有页面的插件只显示信息', (tester) async {
      await pump(tester, (api) {
        api.on('GET $_plugins', {
          'plugins': [_plugin(pages: const [])],
        });
      });

      expect(find.text('没有设置页面'), findsOneWidget);
      expect(frames, isEmpty);
    });

    testWidgets('插件不存在', (tester) async {
      await pump(tester, (api) {
        api.on('GET $_plugins', {'plugins': <Object>[]});
      });

      expect(find.text('插件不存在'), findsOneWidget);
    });
  });

  testWidgets('插件中心按分类分组显示卡片和状态', (tester) async {
    await pumpAdmin(tester, const PluginsPage(), (api) {
      api.on('GET $_plugins', {
        'plugins': [
          _plugin(
            id: 'builtin.tmdb',
            name: 'TMDB 元数据',
            description: '从 TMDB 获取影片简介、演职员和海报。',
          ),
          _plugin(
            id: 'builtin.assrt',
            name: '射手网字幕',
            description: '从射手网搜索并下载字幕。',
            category: 'subtitle',
          ),
          _plugin(
            id: 'source.115',
            name: '网盘',
            description: '从网盘读取视频文件。',
            category: 'resource',
          ),
          _plugin(
            id: 'example.other',
            name: '其他插件',
            description: '',
            category: 'other',
          ),
        ],
      });
    });

    for (final title in ['元数据', '字幕', '资源来源', '其他']) {
      expect(find.text(title), findsOneWidget);
    }
    expect(find.text('TMDB 元数据'), findsOneWidget);
    expect(find.text('从 TMDB 获取影片简介、演职员和海报。'), findsOneWidget);
    expect(find.text('v1.0.0'), findsNWidgets(4));
    expect(find.text('运行中'), findsNWidgets(4));

    // 分组顺序固定：元数据、字幕、资源来源、其他
    final metadataY = tester.getTopLeft(find.text('元数据')).dy;
    final subtitleY = tester.getTopLeft(find.text('字幕')).dy;
    final resourceY = tester.getTopLeft(find.text('资源来源')).dy;
    final otherY = tester.getTopLeft(find.text('其他')).dy;
    expect(metadataY, lessThan(subtitleY));
    expect(subtitleY, lessThan(resourceY));
    expect(resourceY, lessThan(otherY));
  });

  testWidgets('插件中心不显示空的分类', (tester) async {
    await pumpAdmin(tester, const PluginsPage(), (api) {
      api.on('GET $_plugins', {
        'plugins': [
          _plugin(id: 'builtin.tmdb', name: 'TMDB 元数据'),
          _plugin(id: 'example.other', name: '其他插件', category: 'other'),
        ],
      });
    });

    expect(find.text('元数据'), findsOneWidget);
    expect(find.text('其他'), findsOneWidget);
    expect(find.text('字幕'), findsNothing);
    expect(find.text('资源来源'), findsNothing);
  });

  testWidgets('插件中心为空时说明怎么安装', (tester) async {
    await pumpAdmin(tester, const PluginsPage(), (api) {
      api.on('GET $_plugins', {'plugins': <Object>[]});
    });

    expect(find.text('还没有插件'), findsOneWidget);
  });

  testWidgets('卡片菜单可以启停插件', (tester) async {
    final api = await pumpAdmin(tester, const PluginsPage(), (api) {
      api.on('GET $_plugins', {
        'plugins': [_plugin()],
      });
      api.on(
        'PUT $_plugins/example.hello/enabled',
        _plugin(enabled: false, state: 'stopped'),
      );
    });

    await tester.tap(find.byTooltip('管理 示例插件'));
    await tester.pumpAndSettle();
    expect(find.text('设置'), findsOneWidget);
    expect(find.text('停用'), findsOneWidget);
    expect(find.text('移除记录'), findsNothing);

    await tester.tap(find.text('停用'));
    await tester.pumpAndSettle();
    expect(api.bodies['PUT $_plugins/example.hello/enabled'], {
      'enabled': false,
    });
  });

  testWidgets('卡片菜单可以移除未安装插件的记录', (tester) async {
    final api = await pumpAdmin(tester, const PluginsPage(), (api) {
      api.on('GET $_plugins', {
        'plugins': [_plugin(enabled: false, state: 'not_installed')],
      });
    });

    await tester.tap(find.byTooltip('管理 示例插件'));
    await tester.pumpAndSettle();
    expect(find.text('设置'), findsOneWidget);
    expect(find.text('启用'), findsNothing);
    expect(find.text('停用'), findsNothing);
    expect(find.text('移除记录'), findsOneWidget);

    await tester.tap(find.text('移除记录'));
    await tester.pumpAndSettle();
    expect(find.text('移除「示例插件」的记录？'), findsOneWidget);
    expect(api.requests.where((r) => r.startsWith('DELETE')), isEmpty);

    api.on('DELETE $_plugins/example.hello', null, status: 204);
    await tester.tap(find.text('移除'));
    await tester.pumpAndSettle();
    expect(api.requests, contains('DELETE $_plugins/example.hello'));
  });

  testWidgets('卡片菜单的设置进入详情页', (tester) async {
    final api = FakeAdminApi();
    api.on('GET $_plugins', {
      'plugins': [_plugin()],
    });
    final dio = Dio(BaseOptions(baseUrl: 'http://test'))
      ..httpClientAdapter = api;
    tester.view.physicalSize = const Size(390 * 3, 1600 * 3);
    tester.view.devicePixelRatio = 3;
    addTearDown(tester.view.reset);
    final router = GoRouter(
      initialLocation: '/settings/manage/plugins',
      routes: [
        GoRoute(
          path: '/settings/manage/plugins',
          builder: (_, _) => const PluginsPage(),
        ),
        GoRoute(
          path: '/settings/manage/plugins/:id',
          builder: (_, state) =>
              Scaffold(body: Text('详情 ${state.pathParameters['id']}')),
        ),
      ],
    );
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          apiClientProvider.overrideWithValue(dio),
          apiBaseUrlProvider.overrideWithValue('http://test'),
        ],
        child: MaterialApp.router(theme: buildTheme(), routerConfig: router),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.byTooltip('管理 示例插件'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('设置'));
    await tester.pumpAndSettle();

    expect(find.text('详情 example.hello'), findsOneWidget);
  });
}
