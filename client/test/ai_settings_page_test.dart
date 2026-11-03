import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/app/theme.dart';
import 'package:iris/core/providers.dart';
import 'package:iris/core/server.dart';
import 'package:iris/features/admin/ai_settings_page.dart';

import 'support/fake_api.dart';

const _base = '/api/admin/ai';
const _providersRoute = 'GET $_base/providers';
const _rolesRoute = 'GET $_base/roles';
const _semanticRoute = 'GET $_base/semantic';

/// 明文 Key：任何界面文字、提示里都不该出现
const _secret = 'sk-secret-key-123456';

Map<String, dynamic> _provider(
  int id,
  String name, {
  String kind = 'openai',
  String baseUrl = 'https://api.deepseek.com',
  bool keySet = true,
  Map<String, dynamic>? lastTest,
  List<String> usedBy = const [],
}) => {
  'id': id,
  'name': name,
  'kind': kind,
  'base_url': baseUrl,
  'key_set': keySet,
  'last_test': lastTest,
  'used_by': usedBy,
};

const _presets = {
  'presets': [
    {
      'id': 'deepseek',
      'name': 'DeepSeek',
      'kind': 'openai',
      'base_url': 'https://api.deepseek.com',
      'chat_model': 'deepseek-chat',
      'embedding_model': '',
    },
    {
      'id': 'siliconflow',
      'name': '硅基流动',
      'kind': 'openai',
      'base_url': 'https://api.siliconflow.cn/v1',
      'chat_model': '',
      'embedding_model': 'BAAI/bge-m3',
    },
  ],
};

Map<String, dynamic> _roles({
  Map<String, dynamic>? chat,
  Map<String, dynamic>? task,
  Map<String, dynamic>? embedding,
}) => {'chat': chat, 'task': task, 'embedding': embedding};

Map<String, dynamic> _binding(int id, String model) => {
  'provider_id': id,
  'model': model,
};

Map<String, dynamic> _settings({
  bool suggest = false,
  bool auto = false,
  double threshold = 0.9,
}) => {
  'reversible_write': 'confirm',
  'suggest_matches': suggest,
  'auto_match': auto,
  'auto_match_threshold': threshold,
};

Map<String, dynamic> _semantic(
  String state, {
  String? model,
  int total = 0,
  int indexed = 0,
  String? error,
}) => {
  'state': state,
  'model': model,
  'total': total,
  'indexed': indexed,
  'error': error,
};

Map<String, dynamic> _test({
  bool ok = true,
  String? kind,
  String? message,
  int ms = 320,
}) => {
  'ok': ok,
  'checked_at': '2026-11-03T10:00:00Z',
  'error_kind': kind,
  'message': message,
  'duration_ms': ms,
};

/// 页面外面套一层不用水波纹的主题（对话框、弹层和菜单会继承它）。
/// 本机的 Flutter 引擎创建 InkSparkle 着色器（ink_sparkle.frag）会校验失败，
/// 第一次点击水波纹的用例会被连累；这里和点击效果无关，换成无水波纹即可。
Widget _page() => Theme(
  data: buildTheme().copyWith(splashFactory: NoSplash.splashFactory),
  child: const AiSettingsPage(),
);

/// 默认的接口：有两个提供方，用途和语义索引都没配置。
void _defaults(
  FakeAdminApi api, {
  List<Map<String, dynamic>>? providers,
  Map<String, dynamic>? roles,
  Map<String, dynamic>? settings,
  Map<String, dynamic>? semantic,
  List<Map<String, dynamic>> usage = const [],
}) {
  api
    ..on('GET $_base/presets', _presets)
    ..on(_providersRoute, {
      'providers':
          providers ??
          [
            _provider(1, 'DeepSeek'),
            _provider(
              2,
              'Claude',
              kind: 'anthropic',
              baseUrl: 'https://api.anthropic.com',
            ),
          ],
    })
    ..on(_rolesRoute, roles ?? _roles())
    ..on('GET $_base/settings', settings ?? _settings())
    ..on(_semanticRoute, semantic ?? _semantic('off'))
    ..on('GET $_base/usage?months=6', {'rows': usage});
}

/// 打开页面并把视口拉高，让所有分段都渲染出来。
Future<({FakeAdminApi api, int Function() availabilityLoads})> _pump(
  WidgetTester tester,
  void Function(FakeAdminApi api) setup,
) async {
  var loads = 0;
  final api = await pumpAdmin(
    tester,
    _page(),
    setup,
    overrides: [
      serverInfoProvider.overrideWith((ref) {
        loads++;
        return const ServerInfo(id: 's', name: 'n', apiVersion: 1);
      }),
    ],
  );
  tester.view.physicalSize = const Size(390 * 3, 6000 * 3);
  await tester.pumpAndSettle();
  final container = ProviderScope.containerOf(
    tester.element(find.byType(AiSettingsPage)),
  );
  // 先让系统信息加载一次，之后保存成功应当触发第二次
  container.read(serverInfoProvider);
  await tester.pump();
  return (
    api: api,
    availabilityLoads: () {
      container.read(serverInfoProvider);
      return loads;
    },
  );
}

/// 对话框里的保存按钮（页面上的用途分段也有一个「保存」）
Finder _dialogSave() => find.descendant(
  of: find.byType(AlertDialog),
  matching: find.widgetWithText(FilledButton, '保存'),
);

/// 非输入框的文字：输入框里的内容是密文显示，但控件的文本模型里仍是明文。
Finder _plainText(String text) =>
    find.byWidgetPredicate((w) => w is Text && (w.data ?? '').contains(text));

int _count(FakeAdminApi api, String route) =>
    api.requests.where((r) => r == route).length;

/// 离开页面，释放轮询用的定时器。
Future<void> _leave(WidgetTester tester) async {
  await tester.pumpWidget(const SizedBox());
}

Future<void> _openMenu(WidgetTester tester, String providerName) async {
  final tile = find.ancestor(
    of: find.text(providerName),
    matching: find.byType(ListTile),
  );
  await tester.tap(
    find.descendant(of: tile.first, matching: find.byTooltip('更多操作')),
  );
  await tester.pumpAndSettle();
}

Future<void> _choose(WidgetTester tester, String text) async {
  await tester.tap(find.text(text).last);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('shows the privacy note and guides adding a first provider', (
    tester,
  ) async {
    final api = await _pump(
      tester,
      (api) => _defaults(api, providers: const []),
    );

    expect(find.textContaining('会发送给你配置的模型提供方'), findsOneWidget);
    expect(find.textContaining('本地 Ollama'), findsWidgets);
    expect(find.text('先添加一个模型提供方'), findsOneWidget);
    expect(find.text('添加模型提供方'), findsOneWidget);
    // 没有提供方时其余分段没有意义，不显示
    expect(find.text('模型用途'), findsNothing);
    expect(find.text('用量'), findsNothing);
    expect(_count(api.api, 'GET $_base/usage?months=6'), 0);
    await _leave(tester);
  });

  testWidgets('lists providers with key state, last test and users', (
    tester,
  ) async {
    await _pump(
      tester,
      (api) => _defaults(
        api,
        providers: [
          _provider(
            1,
            'DeepSeek',
            usedBy: const ['chat', 'task'],
            lastTest: {
              'ok': true,
              'checked_at': '2026-11-03T10:00:00Z',
              'message': null,
            },
          ),
          _provider(
            2,
            'Claude',
            kind: 'anthropic',
            baseUrl: 'https://api.anthropic.com',
            keySet: false,
            lastTest: {
              'ok': false,
              'checked_at': '2026-11-03T10:00:00Z',
              'message': 'Key 无效',
            },
          ),
        ],
      ),
    );

    expect(find.text('DeepSeek'), findsWidgets);
    expect(
      find.textContaining('OpenAI 兼容 · https://api.deepseek.com'),
      findsOneWidget,
    );
    expect(find.textContaining('Key 已设置 · 用于「对话」、「后台任务」'), findsOneWidget);
    expect(find.textContaining('最近测试：连接成功'), findsOneWidget);
    expect(
      find.textContaining('Anthropic · https://api.anthropic.com'),
      findsOneWidget,
    );
    expect(find.textContaining('Key 未设置 · 未被使用'), findsOneWidget);
    expect(find.textContaining('最近测试：失败，Key 无效'), findsOneWidget);
    expect(find.text('先添加一个模型提供方'), findsNothing);
    await _leave(tester);
  });

  testWidgets('adds a provider from a preset with the right request body', (
    tester,
  ) async {
    final h = await _pump(tester, (api) => _defaults(api, providers: const []));
    final api = h.api;
    expect(h.availabilityLoads(), 1);

    await tester.tap(find.text('添加模型提供方'));
    await tester.pumpAndSettle();
    expect(find.text('DeepSeek'), findsOneWidget);
    expect(find.text('硅基流动'), findsOneWidget);
    expect(find.text('自定义'), findsOneWidget);

    await _choose(tester, 'DeepSeek');
    // 预设预填名称、类型和地址
    expect(
      tester
          .widget<TextField>(find.byKey(const ValueKey('provider-name')))
          .controller!
          .text,
      'DeepSeek',
    );
    expect(
      tester
          .widget<TextField>(find.byKey(const ValueKey('provider-url')))
          .controller!
          .text,
      'https://api.deepseek.com',
    );
    expect(find.text('OpenAI 兼容'), findsOneWidget);

    // Key 输入框是密文，且明文不会出现在界面文字里
    final keyField = find.byKey(const ValueKey('provider-key'));
    expect(tester.widget<TextField>(keyField).obscureText, isTrue);
    await tester.enterText(keyField, _secret);
    await tester.pump();
    expect(_plainText(_secret), findsNothing);

    api
      ..on('POST $_base/providers', _provider(1, 'DeepSeek'), status: 201)
      ..on(_providersRoute, {
        'providers': [_provider(1, 'DeepSeek')],
      });
    await tester.tap(_dialogSave());
    await tester.pumpAndSettle();

    expect(api.bodies['POST $_base/providers'], {
      'name': 'DeepSeek',
      'kind': 'openai',
      'base_url': 'https://api.deepseek.com',
      'api_key': _secret,
    });
    expect(find.text('已添加模型提供方'), findsOneWidget);
    expect(find.text(_secret), findsNothing);
    // 添加成功后列表刷新，系统信息重新获取
    expect(find.textContaining('Key 已设置'), findsOneWidget);
    expect(h.availabilityLoads(), 2);
    await _leave(tester);
  });

  testWidgets('a custom provider omits the key when left blank and validates', (
    tester,
  ) async {
    final h = await _pump(tester, (api) => _defaults(api, providers: const []));
    await tester.tap(find.text('添加模型提供方'));
    await tester.pumpAndSettle();
    await _choose(tester, '自定义');

    // 什么都没填：给出校验提示，不发请求
    await tester.tap(_dialogSave());
    await tester.pumpAndSettle();
    expect(find.text('请填写名称'), findsOneWidget);
    expect(find.text('请填写 http:// 或 https:// 开头的地址'), findsOneWidget);
    expect(h.api.bodies, isEmpty);

    await tester.enterText(find.byKey(const ValueKey('provider-name')), '本地');
    await tester.enterText(
      find.byKey(const ValueKey('provider-url')),
      'http://host:11434/v1',
    );
    h.api.on('POST $_base/providers', _provider(3, '本地'), status: 201);
    await tester.tap(_dialogSave());
    await tester.pumpAndSettle();
    expect(h.api.bodies['POST $_base/providers'], {
      'name': '本地',
      'kind': 'openai',
      'base_url': 'http://host:11434/v1',
    });
    await _leave(tester);
  });

  testWidgets('a rejected save keeps the dialog open and shows the reason', (
    tester,
  ) async {
    final h = await _pump(tester, (api) => _defaults(api, providers: const []));
    h.api.on('POST $_base/providers', {
      'error': {'code': 'ai_provider_name_taken', 'message': '已经有同名的模型提供方'},
    }, status: 409);
    await tester.tap(find.text('添加模型提供方'));
    await tester.pumpAndSettle();
    await _choose(tester, 'DeepSeek');
    await tester.tap(_dialogSave());
    await tester.pumpAndSettle();

    expect(find.text('已经有同名的模型提供方'), findsOneWidget);
    expect(find.text('添加模型提供方'), findsWidgets);
    expect(h.availabilityLoads(), 1, reason: '失败时不刷新系统信息');
    await _leave(tester);
  });

  testWidgets('editing keeps the key unless asked to change or clear it', (
    tester,
  ) async {
    final h = await _pump(tester, _defaults);
    h.api.on('PUT $_base/providers/1', _provider(1, 'DeepSeek 2'));

    // 不动 Key：请求体不带 api_key
    await _openMenu(tester, 'DeepSeek');
    await _choose(tester, '编辑');
    expect(find.text('已设置，留空保持不变'), findsOneWidget);
    await tester.enterText(
      find.byKey(const ValueKey('provider-name')),
      'DeepSeek 2',
    );
    await tester.tap(_dialogSave());
    await tester.pumpAndSettle();
    var body = h.api.bodies['PUT $_base/providers/1'] as Map;
    expect(body['name'], 'DeepSeek 2');
    expect(body.containsKey('api_key'), isFalse);
    expect(h.availabilityLoads(), 2);

    // 换新 Key
    await _openMenu(tester, 'DeepSeek');
    await _choose(tester, '编辑');
    await tester.enterText(find.byKey(const ValueKey('provider-key')), _secret);
    await tester.tap(_dialogSave());
    await tester.pumpAndSettle();
    body = h.api.bodies['PUT $_base/providers/1'] as Map;
    expect(body['api_key'], _secret);

    // 清除 Key：api_key 传 null
    await _openMenu(tester, 'DeepSeek');
    await _choose(tester, '编辑');
    await tester.tap(find.byKey(const ValueKey('provider-clear-key')));
    await tester.pump();
    expect(
      tester
          .widget<TextField>(find.byKey(const ValueKey('provider-key')))
          .enabled,
      isFalse,
    );
    await tester.tap(_dialogSave());
    await tester.pumpAndSettle();
    body = h.api.bodies['PUT $_base/providers/1'] as Map;
    expect(body.containsKey('api_key'), isTrue);
    expect(body['api_key'], isNull);
    await _leave(tester);
  });

  testWidgets('deleting a provider confirms, then refreshes', (tester) async {
    final h = await _pump(tester, _defaults);
    h.api.on('DELETE $_base/providers/2', null, status: 204);

    await _openMenu(tester, 'Claude');
    await _choose(tester, '删除');
    expect(find.text('删除「Claude」？'), findsOneWidget);
    // 取消不发请求
    await tester.tap(find.text('取消'));
    await tester.pumpAndSettle();
    expect(_count(h.api, 'DELETE $_base/providers/2'), 0);

    h.api.on(_providersRoute, {
      'providers': [_provider(1, 'DeepSeek')],
    });
    await _openMenu(tester, 'Claude');
    await _choose(tester, '删除');
    await tester.tap(find.widgetWithText(FilledButton, '删除'));
    await tester.pumpAndSettle();

    expect(_count(h.api, 'DELETE $_base/providers/2'), 1);
    expect(find.text('已删除「Claude」'), findsOneWidget);
    expect(find.textContaining('https://api.anthropic.com'), findsNothing);
    expect(h.availabilityLoads(), 2);
    await _leave(tester);
  });

  testWidgets('deleting a provider that is in use explains why', (
    tester,
  ) async {
    final h = await _pump(
      tester,
      (api) => _defaults(
        api,
        providers: [
          _provider(1, 'DeepSeek', usedBy: const ['chat', 'embedding']),
        ],
        roles: _roles(chat: _binding(1, 'deepseek-chat')),
      ),
    );
    h.api.on('DELETE $_base/providers/1', {
      'error': {'code': 'ai_provider_in_use', 'message': '该提供方正被某个用途使用，请先改用途'},
    }, status: 409);

    await _openMenu(tester, 'DeepSeek');
    await _choose(tester, '删除');
    await tester.tap(find.widgetWithText(FilledButton, '删除'));
    await tester.pumpAndSettle();

    expect(
      find.text('「DeepSeek」正被「对话」、「向量」使用，请先在「模型用途」里改用其他提供方或清除，再删除。'),
      findsOneWidget,
    );
    expect(find.text('已删除「DeepSeek」'), findsNothing);
    expect(h.availabilityLoads(), 1, reason: '删除失败，系统信息不用刷新');
    await _leave(tester);
  });

  testWidgets('testing a provider shows the failure reason and refreshes', (
    tester,
  ) async {
    final h = await _pump(tester, _defaults);
    h.api.on(
      'POST $_base/providers/1/test',
      _test(ok: false, kind: 'auth', message: '401 Unauthorized'),
    );
    expect(_count(h.api, _providersRoute), 1);

    h.api.on(_providersRoute, {
      'providers': [
        _provider(
          1,
          'DeepSeek',
          lastTest: {
            'ok': false,
            'checked_at': '2026-11-03T10:00:00Z',
            'message': '401 Unauthorized',
          },
        ),
        _provider(
          2,
          'Claude',
          kind: 'anthropic',
          baseUrl: 'https://api.anthropic.com',
        ),
      ],
    });
    await _openMenu(tester, 'DeepSeek');
    await _choose(tester, '测试连接');

    expect(
      find.text('「DeepSeek」连接失败：Key 无效或没有权限（401 Unauthorized）'),
      findsOneWidget,
    );
    expect(_count(h.api, _providersRoute), 2);
    expect(find.textContaining('最近测试：失败，401 Unauthorized'), findsOneWidget);

    h.api.on('POST $_base/providers/2/test', _test(ms: 512));
    await _openMenu(tester, 'Claude');
    await _choose(tester, '测试连接');
    expect(find.text('「Claude」连接成功（512 ms）'), findsOneWidget);
    await _leave(tester);
  });

  testWidgets(
    'saves roles: only changed ones, prefilled model, no Anthropic for embedding',
    (tester) async {
      final h = await _pump(tester, _defaults);
      expect(find.text('每种用途选一个提供方，模型名手动填写。改动后点「保存」才生效。'), findsOneWidget);
      expect(find.text('AI 找片和对话'), findsOneWidget);
      expect(find.text('为待处理作品生成匹配建议'), findsOneWidget);
      expect(find.text('语义索引，可不配'), findsOneWidget);

      // 没有改动时保存按钮不可用
      expect(
        tester
            .widget<FilledButton>(find.widgetWithText(FilledButton, '保存'))
            .onPressed,
        isNull,
      );

      // 向量的下拉里没有 Anthropic 的提供方，对话的有
      await tester.tap(
        find.byKey(const ValueKey('role-provider-embedding-null')),
      );
      await tester.pumpAndSettle();
      expect(find.text('Claude'), findsOneWidget, reason: '只剩提供方列表里那一行');
      await tester.tap(find.text('DeepSeek').last);
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('role-provider-chat-null')));
      await tester.pumpAndSettle();
      expect(find.text('Claude'), findsNWidgets(2));
      await tester.tap(find.text('DeepSeek').last);
      await tester.pumpAndSettle();

      // 选了与预设同地址的提供方：对话预填对话模型名，向量没有预设模型名则留空
      expect(
        tester
            .widget<TextField>(find.byKey(const ValueKey('role-model-chat')))
            .controller!
            .text,
        'deepseek-chat',
      );
      expect(
        tester
            .widget<TextField>(
              find.byKey(const ValueKey('role-model-embedding')),
            )
            .controller!
            .text,
        isEmpty,
      );

      // 向量选了提供方但没填模型名：不发请求，提示填写
      await tester.tap(find.widgetWithText(FilledButton, '保存'));
      await tester.pumpAndSettle();
      expect(find.text('请填写「向量」的模型名'), findsOneWidget);
      expect(h.api.bodies, isEmpty);

      await tester.enterText(
        find.byKey(const ValueKey('role-model-embedding')),
        'bge-m3',
      );
      h.api
        ..on('PUT $_base/roles', _roles())
        ..on(
          _rolesRoute,
          _roles(
            chat: _binding(1, 'deepseek-chat'),
            embedding: _binding(1, 'bge-m3'),
          ),
        );
      await tester.tap(find.widgetWithText(FilledButton, '保存'));
      await tester.pumpAndSettle();

      expect(h.api.bodies['PUT $_base/roles'], {
        'chat': {'provider_id': 1, 'model': 'deepseek-chat'},
        'embedding': {'provider_id': 1, 'model': 'bge-m3'},
      });
      expect(find.text('已保存模型用途'), findsOneWidget);
      expect(h.availabilityLoads(), 2);
      // 保存后与服务端一致，保存按钮回到不可用
      expect(
        tester
            .widget<FilledButton>(find.widgetWithText(FilledButton, '保存'))
            .onPressed,
        isNull,
      );
      await _leave(tester);
    },
  );

  testWidgets('clearing a role sends null; testing uses the saved binding', (
    tester,
  ) async {
    final h = await _pump(
      tester,
      (api) => _defaults(
        api,
        roles: _roles(
          chat: _binding(1, 'deepseek-chat'),
          task: _binding(1, 'deepseek-chat'),
        ),
      ),
    );
    expect(
      tester
          .widget<TextField>(find.byKey(const ValueKey('role-model-task')))
          .controller!
          .text,
      'deepseek-chat',
    );

    // 测试：发给服务端的是已保存的配置
    h.api.on('POST $_base/roles/chat/test', _test(ms: 410));
    await tester.tap(find.byKey(const ValueKey('role-test-chat')));
    await tester.pumpAndSettle();
    expect(find.text('「对话」的模型可用（410 ms）'), findsOneWidget);

    h.api.on(
      'POST $_base/roles/task/test',
      _test(ok: false, kind: 'bad_request', message: 'model not found'),
    );
    await tester.tap(find.byKey(const ValueKey('role-test-task')));
    await tester.pumpAndSettle();
    expect(
      find.text('「后台任务」的模型不可用：请求被拒绝，模型名或地址可能不对（model not found）'),
      findsOneWidget,
    );
    // 没配置的用途没有可测的
    expect(
      tester
          .widget<TextButton>(find.byKey(const ValueKey('role-test-embedding')))
          .onPressed,
      isNull,
    );

    // 清除后台任务：只传这一项，值为 null
    await tester.tap(find.byKey(const ValueKey('role-clear-task')));
    await tester.pump();
    // 有改动时测试按钮停用，要先保存
    expect(
      tester
          .widget<TextButton>(find.byKey(const ValueKey('role-test-task')))
          .onPressed,
      isNull,
    );
    h.api
      ..on('PUT $_base/roles', _roles())
      ..on(_rolesRoute, _roles(chat: _binding(1, 'deepseek-chat')));
    await tester.tap(find.widgetWithText(FilledButton, '保存'));
    await tester.pumpAndSettle();
    expect(h.api.bodies['PUT $_base/roles'], {'task': null});
    expect(
      tester
          .widget<TextField>(find.byKey(const ValueKey('role-model-task')))
          .controller!
          .text,
      isEmpty,
    );
    await _leave(tester);
  });

  group('match suggestions', () {
    testWidgets('are disabled until a background-task model is configured', (
      tester,
    ) async {
      final h = await _pump(tester, _defaults);
      expect(find.text('还没有配置「后台任务」模型，配置后才能使用匹配建议。'), findsOneWidget);
      for (final key in ['suggest-matches', 'auto-match']) {
        expect(
          tester.widget<SwitchListTile>(find.byKey(ValueKey(key))).onChanged,
          isNull,
          reason: key,
        );
      }
      await tester.tap(
        find.byKey(const ValueKey('suggest-matches')),
        warnIfMissed: false,
      );
      await tester.pumpAndSettle();
      expect(h.api.bodies.keys.where((r) => r.contains('settings')), isEmpty);
      await _leave(tester);
    });

    testWidgets('switches and the threshold save only what changed', (
      tester,
    ) async {
      final h = await _pump(
        tester,
        (api) =>
            _defaults(api, roles: _roles(task: _binding(1, 'deepseek-chat'))),
      );
      expect(find.textContaining('还没有配置「后台任务」模型'), findsNothing);
      expect(find.textContaining('通过年份、季集数、片名校验'), findsOneWidget);
      expect(find.textContaining('可在条目页撤销'), findsOneWidget);
      expect(find.text('90%'), findsOneWidget);
      // 自动匹配没开时，阈值不能调
      expect(
        tester
            .widget<Slider>(find.byKey(const ValueKey('threshold-slider')))
            .onChanged,
        isNull,
      );

      h.api
        ..on('PUT $_base/settings', _settings(suggest: true))
        ..on('GET $_base/settings', _settings(suggest: true));
      await tester.tap(find.byKey(const ValueKey('suggest-matches')));
      await tester.pumpAndSettle();
      expect(h.api.bodies['PUT $_base/settings'], {'suggest_matches': true});
      expect(
        tester
            .widget<SwitchListTile>(
              find.byKey(const ValueKey('suggest-matches')),
            )
            .value,
        isTrue,
      );
      expect(h.availabilityLoads(), 2);

      h.api
        ..on('PUT $_base/settings', _settings(suggest: true, auto: true))
        ..on('GET $_base/settings', _settings(suggest: true, auto: true));
      await tester.tap(find.byKey(const ValueKey('auto-match')));
      await tester.pumpAndSettle();
      expect(h.api.bodies['PUT $_base/settings'], {'auto_match': true});
      expect(h.availabilityLoads(), 3);

      // 开启后可调阈值，松手时保存，显示成百分比
      final slider = tester.widget<Slider>(
        find.byKey(const ValueKey('threshold-slider')),
      );
      expect(slider.onChanged, isNotNull);
      expect(slider.min, 0.5);
      expect(slider.max, 1.0);
      h.api
        ..on(
          'PUT $_base/settings',
          _settings(suggest: true, auto: true, threshold: 0.8),
        )
        ..on(
          'GET $_base/settings',
          _settings(suggest: true, auto: true, threshold: 0.8),
        );
      slider.onChanged!(0.8);
      await tester.pump();
      expect(find.text('80%'), findsOneWidget);
      slider.onChangeEnd!(0.8);
      await tester.pumpAndSettle();
      expect(h.api.bodies['PUT $_base/settings'], {
        'auto_match_threshold': 0.8,
      });
      expect(find.text('80%'), findsOneWidget);
      expect(h.availabilityLoads(), 4);
      await _leave(tester);
    });

    testWidgets('a failed save reports the error and keeps the old value', (
      tester,
    ) async {
      final h = await _pump(
        tester,
        (api) =>
            _defaults(api, roles: _roles(task: _binding(1, 'deepseek-chat'))),
      );
      h.api.on('PUT $_base/settings', {
        'error': {'code': 'validation_error', 'message': '阈值不合法'},
      }, status: 422);
      await tester.tap(find.byKey(const ValueKey('suggest-matches')));
      await tester.pumpAndSettle();
      expect(find.text('阈值不合法'), findsOneWidget);
      expect(
        tester
            .widget<SwitchListTile>(
              find.byKey(const ValueKey('suggest-matches')),
            )
            .value,
        isFalse,
      );
      expect(h.availabilityLoads(), 1);
      await _leave(tester);
    });
  });

  group('semantic index', () {
    String stateText(WidgetTester tester) =>
        tester.widget<Text>(find.byKey(const ValueKey('semantic-state'))).data!;

    testWidgets('building shows progress and refreshes every 5 seconds', (
      tester,
    ) async {
      final h = await _pump(
        tester,
        (api) => _defaults(
          api,
          semantic: _semantic(
            'building',
            model: 'bge-m3',
            total: 860,
            indexed: 120,
          ),
        ),
      );
      expect(stateText(tester), '正在建立索引 120/860');
      expect(find.text('向量模型：bge-m3'), findsOneWidget);
      expect(find.byType(LinearProgressIndicator), findsOneWidget);
      expect(_count(h.api, _semanticRoute), 1);

      h.api.on(
        _semanticRoute,
        _semantic('building', model: 'bge-m3', total: 860, indexed: 400),
      );
      await tester.pump(const Duration(seconds: 5));
      await tester.pumpAndSettle();
      expect(_count(h.api, _semanticRoute), 2);
      expect(stateText(tester), '正在建立索引 400/860');

      // 建好之后不再轮询
      h.api.on(
        _semanticRoute,
        _semantic('ready', model: 'bge-m3', total: 860, indexed: 860),
      );
      await tester.pump(const Duration(seconds: 5));
      await tester.pumpAndSettle();
      expect(_count(h.api, _semanticRoute), 3);
      expect(stateText(tester), '已是最新（860 个条目）');
      await tester.pump(const Duration(seconds: 15));
      await tester.pumpAndSettle();
      expect(_count(h.api, _semanticRoute), 3);
      await _leave(tester);
    });

    testWidgets('error, unavailable and off are explained', (tester) async {
      await _pump(
        tester,
        (api) => _defaults(
          api,
          semantic: _semantic(
            'error',
            model: 'bge-m3',
            total: 10,
            error: 'Key 无效',
          ),
        ),
      );
      expect(stateText(tester), '向量模型调用失败：Key 无效');
      await _leave(tester);

      await _pump(
        tester,
        (api) =>
            _defaults(api, semantic: _semantic('unavailable', model: 'bge-m3')),
      );
      expect(stateText(tester), 'sqlite-vec 不可用，只能按条件筛选。');
      expect(
        tester
            .widget<OutlinedButton>(find.widgetWithText(OutlinedButton, '重建索引'))
            .onPressed,
        isNull,
      );
      await _leave(tester);

      await _pump(tester, (api) => _defaults(api, semantic: _semantic('off')));
      expect(stateText(tester), contains('未启用'));
      expect(find.textContaining('向量模型：'), findsNothing);
      await _leave(tester);
    });

    testWidgets('rebuilding asks first, then posts and refreshes', (
      tester,
    ) async {
      final h = await _pump(
        tester,
        (api) => _defaults(
          api,
          semantic: _semantic('ready', model: 'bge-m3', total: 5, indexed: 5),
        ),
      );
      h.api.on('POST $_base/semantic/rebuild', null, status: 204);

      await tester.tap(find.text('重建索引'));
      await tester.pumpAndSettle();
      expect(find.text('重建语义索引？'), findsOneWidget);
      await tester.tap(find.text('取消'));
      await tester.pumpAndSettle();
      expect(_count(h.api, 'POST $_base/semantic/rebuild'), 0);

      h.api.on(
        _semanticRoute,
        _semantic('building', model: 'bge-m3', total: 5, indexed: 0),
      );
      await tester.tap(find.text('重建索引'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(FilledButton, '重建'));
      await tester.pumpAndSettle();
      expect(_count(h.api, 'POST $_base/semantic/rebuild'), 1);
      expect(find.text('已开始重建索引'), findsOneWidget);
      expect(stateText(tester), '正在建立索引 0/5');
      expect(h.availabilityLoads(), 2);
      await _leave(tester);
    });
  });

  testWidgets('usage is grouped by month per role and says it is not limited', (
    tester,
  ) async {
    await _pump(
      tester,
      (api) => _defaults(
        api,
        usage: [
          {
            'month': '2026-10',
            'role': 'chat',
            'calls': 3,
            'failures': 0,
            'input_tokens': 900,
            'output_tokens': 300,
          },
          {
            'month': '2026-11',
            'role': 'task',
            'calls': 1200,
            'failures': 4,
            'input_tokens': 1234567,
            'output_tokens': 89000,
          },
          {
            'month': '2026-11',
            'role': 'chat',
            'calls': 12,
            'failures': 1,
            'input_tokens': 3200,
            'output_tokens': 1100,
          },
        ],
      ),
    );

    expect(find.textContaining('仅记录，不做限额'), findsOneWidget);
    expect(find.text('2026 年 11 月'), findsOneWidget);
    expect(find.text('2026 年 10 月'), findsOneWidget);
    expect(
      find.text('12 次调用，失败 1 次 · 输入 3,200 / 输出 1,100 token'),
      findsOneWidget,
    );
    expect(
      find.text('1,200 次调用，失败 4 次 · 输入 1,234,567 / 输出 89,000 token'),
      findsOneWidget,
    );
    expect(find.text('3 次调用，失败 0 次 · 输入 900 / 输出 300 token'), findsOneWidget);
    // 新的月份在前
    expect(
      tester.getTopLeft(find.text('2026 年 11 月')).dy,
      lessThan(tester.getTopLeft(find.text('2026 年 10 月')).dy),
    );
    await _leave(tester);
  });

  testWidgets('empty usage says there are no calls yet', (tester) async {
    await _pump(tester, _defaults);
    expect(find.text('还没有调用记录。'), findsOneWidget);
    await _leave(tester);
  });

  testWidgets('a failing section shows its own error with retry', (
    tester,
  ) async {
    final h = await _pump(tester, (api) {
      _defaults(api);
      api.on('GET $_base/usage?months=6', {
        'error': {'code': 'internal', 'message': '统计服务出错'},
      }, status: 500);
    });
    expect(find.text('统计服务出错'), findsOneWidget);
    // 其他分段不受影响
    expect(find.text('模型提供方'), findsOneWidget);

    h.api.on('GET $_base/usage?months=6', {'rows': <Object>[]});
    await tester.tap(find.text('重试'));
    await tester.pumpAndSettle();
    expect(find.text('统计服务出错'), findsNothing);
    expect(find.text('还没有调用记录。'), findsOneWidget);
    await _leave(tester);
  });

  testWidgets('a failing provider list is an error page with retry', (
    tester,
  ) async {
    final api = await pumpAdmin(tester, _page(), (api) {
      api.on(_providersRoute, {
        'error': {'code': 'internal', 'message': '读不出来'},
      }, status: 500);
    });
    expect(find.text('读不出来'), findsOneWidget);
    expect(find.text('重试'), findsOneWidget);

    api.on(_providersRoute, {'providers': <Object>[]});
    await tester.tap(find.text('重试'));
    await tester.pumpAndSettle();
    expect(find.text('先添加一个模型提供方'), findsOneWidget);
    await _leave(tester);
  });
}
