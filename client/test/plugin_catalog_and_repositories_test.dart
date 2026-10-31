import 'dart:typed_data';

import 'package:file_selector_platform_interface/file_selector_platform_interface.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/features/admin/plugin_detail_page.dart';
import 'package:iris/features/admin/plugin_repositories_page.dart';
import 'package:iris/features/admin/plugins_page.dart';

import 'support/fake_api.dart';

class _FakeFileSelectorPlatform extends FileSelectorPlatform {
  XFile? fileToReturn;

  @override
  Future<XFile?> openFile({
    List<XTypeGroup>? acceptedTypeGroups = const <XTypeGroup>[],
    String? initialDirectory,
    String? confirmButtonText,
  }) async {
    return fileToReturn;
  }
}

Map<String, dynamic> _mockPlugin({
  String id = 'example.hello',
  String name = '示例插件',
  String version = '1.0.0',
  String source = 'system',
  String? repositoryName,
  int? repositoryId,
  String? availableVersion,
  bool builtIn = false,
  bool enabled = true,
}) => {
  'id': id,
  'name': name,
  'version': version,
  'sdk_version': '1',
  'provides': ['admin.ui'],
  'requires': [],
  'trust': builtIn ? 'built_in' : 'external',
  'built_in': builtIn,
  'enabled': enabled,
  'state': enabled ? 'running' : 'stopped',
  'updated_at': '2026-10-31T00:00:00+00:00',
  'source': source,
  'repository_id': repositoryId,
  'repository_name': repositoryName,
  'available_version': availableVersion,
  'package_status': 'ok',
  'package_error': null,
  'pages': [
    {'id': 'settings', 'title': '设置'},
  ],
};

Map<String, dynamic> _mockCatalog() => {
  'sdk_version': '1.0.0',
  'repositories': [
    {
      'id': 1,
      'name': 'Iris 官方仓库',
      'url': 'https://huhengbo.github.io/iris-plugins/index.json',
      'official': true,
      'enabled': true,
      'status': 'ok',
      'error': null,
      'schema': 1,
      'plugin_count': 5,
      'invalid_count': 0,
      'last_checked_at': '2026-10-31T10:00:00+00:00',
      'last_success_at': '2026-10-31T10:00:00+00:00',
      'created_at': '2026-10-31T00:00:00+00:00',
    },
  ],
  'plugins': [
    {
      'id': 'cat.available',
      'name': '可安装插件',
      'description': '一个完全兼容且可以安装的插件',
      'version': '1.0.0',
      'repository_id': 1,
      'repository_name': 'Iris 官方仓库',
      'category': 'metadata',
      'sdk': '>=1.0,<2.0',
      'provides': ['metadata.movie'],
      'capabilities': ['network', 'storage'],
      'size': 1024 * 100,
      'status': 'available',
      'installed_version': null,
      'reason': null,
    },
    {
      'id': 'cat.installed',
      'name': '已安装插件',
      'description': '已经安装在此宿主上的插件',
      'version': '1.0.0',
      'repository_id': 1,
      'repository_name': 'Iris 官方仓库',
      'category': 'subtitle',
      'sdk': '>=1.0,<2.0',
      'provides': ['subtitle.search'],
      'capabilities': [],
      'size': 1024 * 50,
      'status': 'installed',
      'installed_version': '1.0.0',
      'reason': null,
    },
    {
      'id': 'cat.update',
      'name': '可更新插件',
      'description': '有新版本可供升级的插件',
      'version': '2.0.0',
      'repository_id': 1,
      'repository_name': 'Iris 官方仓库',
      'category': 'resource',
      'sdk': '>=1.0,<2.0',
      'provides': ['resource.source'],
      'capabilities': ['network'],
      'size': 1024 * 200,
      'status': 'update_available',
      'installed_version': '1.0.0',
      'reason': null,
    },
    {
      'id': 'cat.conflict',
      'name': '冲突插件',
      'description': '与系统插件或本地插件冲突',
      'version': '1.0.0',
      'repository_id': 1,
      'repository_name': 'Iris 官方仓库',
      'category': 'other',
      'sdk': '>=1.0,<2.0',
      'provides': ['admin.ui'],
      'capabilities': [],
      'size': 1024 * 10,
      'status': 'conflict',
      'installed_version': null,
      'reason': '与已安装的本地插件冲突',
    },
    {
      'id': 'cat.incompatible',
      'name': '不兼容插件',
      'description': '要求更高的宿主 SDK 版本',
      'version': '3.0.0',
      'repository_id': 1,
      'repository_name': 'Iris 官方仓库',
      'category': 'other',
      'sdk': '>=3.0',
      'provides': ['admin.ui'],
      'capabilities': [],
      'size': 1024 * 10,
      'status': 'incompatible',
      'installed_version': null,
      'reason': 'SDK 版本不兼容（需要 >=3.0，当前宿主 1.0.0）',
    },
  ],
};

Map<String, dynamic> _mockRepositories() => {
  'repositories': [
    {
      'id': 1,
      'name': 'Iris 官方仓库',
      'url': 'https://huhengbo.github.io/iris-plugins/index.json',
      'official': true,
      'enabled': true,
      'status': 'ok',
      'error': null,
      'schema': 1,
      'plugin_count': 12,
      'invalid_count': 0,
      'last_checked_at': '2026-10-31T10:00:00+00:00',
      'last_success_at': '2026-10-31T10:00:00+00:00',
      'created_at': '2026-10-31T00:00:00+00:00',
    },
    {
      'id': 2,
      'name': '社区自定义源',
      'url': 'https://example.com/custom-repo/index.json',
      'official': false,
      'enabled': true,
      'status': 'ok',
      'error': null,
      'schema': 1,
      'plugin_count': 3,
      'invalid_count': 1,
      'last_checked_at': '2026-10-31T10:05:00+00:00',
      'last_success_at': '2026-10-31T10:05:00+00:00',
      'created_at': '2026-10-31T01:00:00+00:00',
    },
  ],
};

void main() {
  group('插件中心 - 目录与多状态渲染', () {
    testWidgets('目录展示可安装、已安装、可更新、冲突、不兼容状态与原因', (tester) async {
      await pumpAdmin(tester, const PluginsPage(), (api) {
        api.on('GET /api/admin/plugins', {'plugins': []});
        api.on('GET /api/admin/plugin-catalog', _mockCatalog());
        api.on('GET /api/admin/resources', <String, dynamic>{'sources': []});
      });

      // 切换到「目录」标签页
      await tester.tap(find.text('目录'));
      await tester.pumpAndSettle();

      // 验证各状态卡片文本
      expect(find.text('可安装插件'), findsOneWidget);
      expect(find.text('可安装'), findsOneWidget);

      expect(find.text('已安装插件'), findsOneWidget);
      expect(find.text('已安装'), findsOneWidget);

      expect(find.text('可更新插件'), findsOneWidget);
      expect(find.text('可更新'), findsOneWidget);

      expect(find.text('冲突插件'), findsOneWidget);
      expect(find.text('冲突'), findsOneWidget);
      expect(find.text('与已安装的本地插件冲突'), findsOneWidget);

      expect(find.text('不兼容插件'), findsOneWidget);
      expect(find.text('不兼容'), findsOneWidget);
      expect(find.text('SDK 版本不兼容（需要 >=3.0，当前宿主 1.0.0）'), findsOneWidget);

      // 点击冲突插件，打开详情面板，按钮显示“不可安装”且不可点击
      await tester.tap(find.text('冲突插件'));
      await tester.pumpAndSettle();

      expect(find.text('无法安装原因：与已安装的本地插件冲突'), findsOneWidget);
      final cantInstallBtn = tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, '不可安装'),
      );
      expect(cantInstallBtn.onPressed, isNull);
    });

    testWidgets('安装确认流程：权限提示、启动任务、轮询进度并成功刷新', (tester) async {
      final fakeApi = await pumpAdmin(tester, const PluginsPage(), (api) {
        api.on('GET /api/admin/plugins', {'plugins': []});
        api.on('GET /api/admin/plugin-catalog', _mockCatalog());
        api.on('GET /api/admin/resources', <String, dynamic>{'sources': []});
        api.on('POST /api/admin/plugins/install', {'task_id': 42}, status: 202);
        api.on('GET /api/admin/tasks/42', {
          'id': 42,
          'kind': 'plugin:install',
          'phase': 'completed',
          'status': 'completed',
          'progress_done': 100,
          'progress_total': 100,
          'cancel_requested': false,
          'error': null,
          'result': {'plugin_id': 'cat.available', 'version': '1.0.0'},
          'started_at': '2026-10-31T10:00:00+00:00',
        });
      });

      // 切换到「目录」
      await tester.tap(find.text('目录'));
      await tester.pumpAndSettle();

      // 点击可安装插件卡片
      await tester.tap(find.text('可安装插件'));
      await tester.pumpAndSettle();

      // 详情面板中点击「安装插件」
      await tester.tap(find.widgetWithText(FilledButton, '安装插件'));
      await tester.pumpAndSettle();

      // 弹出权限确认对话框
      expect(find.text('安装「可安装插件」'), findsOneWidget);
      expect(find.text('metadata.movie'), findsOneWidget);
      expect(find.text('network、storage'), findsOneWidget);

      // 点击「确认安装」
      await tester.tap(find.text('确认安装'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      await tester.pumpAndSettle();

      // 验证安装接口调用
      expect(
        fakeApi.requests.contains('POST /api/admin/plugins/install'),
        isTrue,
      );
      expect(fakeApi.bodies['POST /api/admin/plugins/install'], {
        'repository_id': 1,
        'plugin_id': 'cat.available',
        'version': '1.0.0',
        'accept_permissions': true,
      });

      // 验证完成提示
      expect(find.text('插件可安装插件安装成功'), findsOneWidget);
    });

    testWidgets('任务失败时展示 result.error_code 中文说明并标明回滚', (tester) async {
      await pumpAdmin(tester, const PluginsPage(), (api) {
        api.on('GET /api/admin/plugins', {'plugins': []});
        api.on('GET /api/admin/plugin-catalog', _mockCatalog());
        api.on('GET /api/admin/resources', <String, dynamic>{'sources': []});
        api.on('POST /api/admin/plugins/install', {'task_id': 99}, status: 202);
        api.on('GET /api/admin/tasks/99', {
          'id': 99,
          'kind': 'plugin:install',
          'phase': 'dependencies',
          'status': 'failed',
          'progress_done': 0,
          'progress_total': 1,
          'cancel_requested': false,
          'error': 'uv exited with code 1',
          'result': {
            'plugin_id': 'cat.available',
            'version': '1.0.0',
            'rolled_back': true,
            'error_code': 'dependency_install_failed',
          },
          'started_at': '2026-10-31T10:00:00+00:00',
        });
      });

      await tester.tap(find.text('目录'));
      await tester.pumpAndSettle();

      await tester.tap(find.text('可安装插件'));
      await tester.pumpAndSettle();

      await tester.tap(find.widgetWithText(FilledButton, '安装插件'));
      await tester.pumpAndSettle();

      await tester.tap(find.text('确认安装'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      await tester.pumpAndSettle();

      // 错误对话框提示包含 error_code 中文说明和回滚标记
      expect(find.textContaining('安装插件依赖失败（已自动回滚至上一版本）'), findsOneWidget);
      expect(find.text('关闭'), findsOneWidget);

      await tester.tap(find.text('关闭'));
      await tester.pumpAndSettle();
      expect(find.text('安装插件依赖失败'), findsNothing);
    });
  });

  group('插件仓库管理页', () {
    testWidgets('展示仓库列表、启停、官方不可删除、删除普通仓库', (tester) async {
      final fakeApi = await pumpAdmin(tester, const PluginRepositoriesPage(), (
        api,
      ) {
        api.on('GET /api/admin/plugin-repositories', _mockRepositories());
        api.on(
          'PATCH /api/admin/plugin-repositories/2',
          _mockRepositories()['repositories'][1],
        );
        api.on('DELETE /api/admin/plugin-repositories/2', null, status: 204);
      });

      expect(find.text('插件仓库'), findsOneWidget);
      expect(find.text('Iris 官方仓库'), findsOneWidget);
      expect(find.text('官方'), findsOneWidget);
      expect(find.text('社区自定义源'), findsOneWidget);

      // 启停开关
      final switches = find.byType(Switch);
      expect(switches, findsNWidgets(2));
      await tester.tap(switches.at(1));
      await tester.pumpAndSettle();

      expect(
        fakeApi.requests.contains('PATCH /api/admin/plugin-repositories/2'),
        isTrue,
      );

      // 官方仓库不可删除提示
      expect(find.byTooltip('官方仓库不可删除'), findsOneWidget);

      // 删除普通仓库
      final deleteButtons = find.byTooltip('删除仓库');
      expect(deleteButtons, findsOneWidget);
      await tester.tap(deleteButtons);
      await tester.pumpAndSettle();

      // 确认对话框
      expect(find.text('删除仓库「社区自定义源」？'), findsOneWidget);
      await tester.tap(find.text('删除'));
      await tester.pumpAndSettle();

      expect(
        fakeApi.requests.contains('DELETE /api/admin/plugin-repositories/2'),
        isTrue,
      );
      expect(find.text('仓库已删除'), findsOneWidget);
    });

    testWidgets('添加仓库需勾选风险确认，检查更新触发 refresh', (tester) async {
      final fakeApi = await pumpAdmin(tester, const PluginRepositoriesPage(), (
        api,
      ) {
        api.on('GET /api/admin/plugin-repositories', _mockRepositories());
        api.on(
          'POST /api/admin/plugin-repositories',
          _mockRepositories()['repositories'][1],
          status: 201,
        );
        api.on('POST /api/admin/plugin-repositories/refresh', {
          'repositories': _mockRepositories()['repositories'],
        });
      });

      // 点击检查更新
      await tester.tap(find.text('检查更新'));
      await tester.pumpAndSettle();
      expect(
        fakeApi.requests.contains(
          'POST /api/admin/plugin-repositories/refresh',
        ),
        isTrue,
      );
      expect(find.text('已完成所有仓库检查'), findsOneWidget);

      // 点击添加仓库
      await tester.tap(find.byTooltip('添加仓库'));
      await tester.pumpAndSettle();

      expect(find.text('添加插件仓库'), findsOneWidget);
      // 输入 URL
      await tester.enterText(
        find.byType(TextField),
        'https://example.com/new-repo/index.json',
      );
      await tester.pumpAndSettle();

      // 未勾选风险确认，添加按钮禁用
      final addBtnBefore = tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, '添加'),
      );
      expect(addBtnBefore.onPressed, isNull);

      // 勾选风险确认
      await tester.tap(find.text('我已知晓并承担使用第三方插件仓库的风险'));
      await tester.pumpAndSettle();

      final addBtnAfter = tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, '添加'),
      );
      expect(addBtnAfter.onPressed, isNotNull);

      await tester.tap(find.widgetWithText(FilledButton, '添加'));
      await tester.pumpAndSettle();

      expect(
        fakeApi.requests.contains('POST /api/admin/plugin-repositories'),
        isTrue,
      );
      expect(fakeApi.bodies['POST /api/admin/plugin-repositories'], {
        'url': 'https://example.com/new-repo/index.json',
        'accept_risk': true,
      });
      expect(find.text('仓库添加成功'), findsOneWidget);
    });
    testWidgets('添加仓库被后端拒绝时对话框展示后端说明而不是异常原文', (tester) async {
      await pumpAdmin(tester, const PluginRepositoriesPage(), (api) {
        api.on('GET /api/admin/plugin-repositories', _mockRepositories());
        api.on('POST /api/admin/plugin-repositories', {
          'error': {
            'code': 'invalid_repository_url',
            'message': '仓库地址只能使用 HTTPS',
          },
        }, status: 400);
      });

      await tester.tap(find.byTooltip('添加仓库'));
      await tester.pumpAndSettle();
      await tester.enterText(
        find.byType(TextField),
        'http://example.com/index.json',
      );
      await tester.tap(find.text('我已知晓并承担使用第三方插件仓库的风险'));
      await tester.pumpAndSettle();
      await tester.tap(find.widgetWithText(FilledButton, '添加'));
      await tester.pumpAndSettle();

      expect(find.text('仓库地址只能使用 HTTPS'), findsOneWidget);
      expect(find.textContaining('DioException'), findsNothing);
    });
  });

  group('本地安装流程', () {
    late _FakeFileSelectorPlatform fakeSelector;

    setUp(() {
      fakeSelector = _FakeFileSelectorPlatform();
      FileSelectorPlatform.instance = fakeSelector;
    });

    testWidgets('选择 zip、上传、预览与风险确认后发起安装', (tester) async {
      fakeSelector.fileToReturn = XFile.fromData(
        Uint8List.fromList([1, 2, 3, 4]),
        name: 'test-plugin.zip',
      );

      final fakeApi = await pumpAdmin(tester, const PluginsPage(), (api) {
        api.on('GET /api/admin/plugins', {'plugins': []});
        api.on('GET /api/admin/plugin-catalog', _mockCatalog());
        api.on('GET /api/admin/resources', <String, dynamic>{'sources': []});
        api.on('POST /api/admin/plugins/uploads', {
          'upload_id': 'u_test123',
          'sha256': 'abcdef1234567890',
          'size': 1024 * 12,
          'manifest': {
            'id': 'local.sample',
            'name': '本地测试插件',
            'version': '1.0.0',
            'description': '本地上传的插件包',
            'sdk': '>=1.0,<2.0',
            'provides': ['admin.ui'],
            'capabilities': ['network'],
          },
          'category': 'other',
          'action': 'install',
          'installed_version': null,
        }, status: 201);
        api.on('POST /api/admin/plugins/uploads/u_test123/install', {
          'task_id': 77,
        }, status: 202);
        api.on('GET /api/admin/tasks/77', {
          'id': 77,
          'kind': 'plugin:install',
          'phase': 'start',
          'status': 'completed',
          'progress_done': 1,
          'progress_total': 1,
          'cancel_requested': false,
          'error': null,
          'result': {'plugin_id': 'local.sample', 'version': '1.0.0'},
          'started_at': '2026-10-31T10:00:00+00:00',
        });
      });

      // 点击 AppBar 上的「本地安装」
      await tester.tap(find.text('本地安装'));
      await tester.pumpAndSettle();

      // 验证上传接口被调用
      expect(
        fakeApi.requests.contains('POST /api/admin/plugins/uploads'),
        isTrue,
      );

      // 弹出预览对话框
      expect(find.text('本地安装预览：本地测试插件'), findsOneWidget);
      expect(find.text('插件 ID：local.sample'), findsOneWidget);
      expect(find.text('目标版本：v1.0.0'), findsOneWidget);
      expect(find.text('安装方式：全新安装'), findsOneWidget);

      // 未勾选风险确认时安装按钮禁用
      final installBtnBefore = tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, '确认安装'),
      );
      expect(installBtnBefore.onPressed, isNull);

      // 勾选风险确认
      await tester.tap(find.text('我已知晓并承担运行第三方插件代码的风险'));
      await tester.pumpAndSettle();

      final installBtnAfter = tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, '确认安装'),
      );
      expect(installBtnAfter.onPressed, isNotNull);

      // 确认安装
      await tester.tap(find.widgetWithText(FilledButton, '确认安装'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      await tester.pumpAndSettle();

      expect(
        fakeApi.requests.contains(
          'POST /api/admin/plugins/uploads/u_test123/install',
        ),
        isTrue,
      );
      expect(
        fakeApi.bodies['POST /api/admin/plugins/uploads/u_test123/install'],
        {'accept_risk': true},
      );
      expect(find.text('插件本地测试插件安装成功'), findsOneWidget);
    });
  });

  group('我的插件 - 卸载、deleteData 与来源徽标', () {
    testWidgets('非系统插件菜单显示升级与卸载，卸载传递 delete_data 参数', (tester) async {
      final fakeApi = await pumpAdmin(tester, const PluginsPage(), (api) {
        api.on('GET /api/admin/plugins', {
          'plugins': [
            _mockPlugin(
              id: 'sys.plugin',
              name: '系统内置插件',
              source: 'system',
              builtIn: true,
            ),
            _mockPlugin(
              id: 'repo.plugin',
              name: '仓库插件',
              source: 'repository',
              repositoryName: 'Iris 官方仓库',
              availableVersion: '1.2.0',
            ),
          ],
        });
        api.on('GET /api/admin/resources', <String, dynamic>{'sources': []});
        api.on(
          'POST /api/admin/plugins/repo.plugin/uninstall',
          null,
          status: 204,
        );
      });

      // 验证来源与更新徽标
      expect(find.text('系统内置插件'), findsOneWidget);
      expect(find.text('系统'), findsOneWidget);

      expect(find.text('仓库插件'), findsOneWidget);
      expect(find.text('Iris 官方仓库'), findsOneWidget);
      expect(find.text('有更新'), findsOneWidget);

      // 打开系统插件菜单：没有升级和卸载选项
      await tester.tap(find.byTooltip('管理 系统内置插件'));
      await tester.pumpAndSettle();
      expect(find.text('设置'), findsOneWidget);
      expect(find.text('停用'), findsOneWidget);
      expect(find.text('升级'), findsNothing);
      expect(find.text('卸载'), findsNothing);

      // 关闭菜单
      await tester.tapAt(const Offset(10, 10));
      await tester.pumpAndSettle();

      // 打开仓库插件菜单：包含升级和卸载
      await tester.tap(find.byTooltip('管理 仓库插件'));
      await tester.pumpAndSettle();
      expect(find.text('升级'), findsOneWidget);
      expect(find.text('卸载'), findsOneWidget);

      // 点击「卸载」
      await tester.tap(find.text('卸载'));
      await tester.pumpAndSettle();

      expect(find.text('卸载「仓库插件」？'), findsOneWidget);
      expect(find.text('同时删除数据'), findsOneWidget);

      // 勾选同时删除数据
      await tester.tap(find.text('同时删除数据'));
      await tester.pumpAndSettle();

      // 点击确认卸载
      await tester.tap(find.widgetWithText(FilledButton, '卸载'));
      await tester.pumpAndSettle();

      expect(
        fakeApi.requests.contains(
          'POST /api/admin/plugins/repo.plugin/uninstall',
        ),
        isTrue,
      );
      expect(fakeApi.bodies['POST /api/admin/plugins/repo.plugin/uninstall'], {
        'delete_data': true,
      });
      expect(find.text('插件已卸载'), findsOneWidget);
    });

    testWidgets('卸载被资源来源占用时展示后端错误说明', (tester) async {
      await pumpAdmin(tester, const PluginsPage(), (api) {
        api.on('GET /api/admin/plugins', {
          'plugins': [
            _mockPlugin(
              id: 'repo.plugin',
              name: '被占用的插件',
              source: 'repository',
              repositoryName: 'Iris 官方仓库',
            ),
          ],
        });
        api.on('GET /api/admin/resources', <String, dynamic>{'sources': []});
        api.on('POST /api/admin/plugins/repo.plugin/uninstall', {
          'error': {
            'code': 'plugin_in_use',
            'message': '该插件有 2 个资源来源正在使用，请先删除来源。',
          },
        }, status: 409);
      });

      await tester.tap(find.byTooltip('管理 被占用的插件'));
      await tester.pumpAndSettle();

      await tester.tap(find.text('卸载'));
      await tester.pumpAndSettle();

      await tester.tap(find.widgetWithText(FilledButton, '卸载'));
      await tester.pumpAndSettle();

      expect(find.text('该插件有 2 个资源来源正在使用，请先删除来源。'), findsOneWidget);
    });
  });

  group('插件详情页 - 来源、版本、升级与卸载', () {
    testWidgets('显示仓库来源和新版本提示，提供升级和卸载', (tester) async {
      final fakeApi = await pumpAdmin(
        tester,
        const PluginDetailPage(pluginId: 'repo.plugin'),
        (api) {
          api.on('GET /api/admin/plugins', {
            'plugins': [
              _mockPlugin(
                id: 'repo.plugin',
                name: '详情测试插件',
                source: 'repository',
                repositoryName: 'Iris 官方仓库',
                availableVersion: '2.0.0',
              ),
            ],
          });
          api.on('POST /api/admin/plugins/repo.plugin/session', {
            'token': 'tok',
            'expires_at': '2026-10-31T12:00:00+00:00',
            'pages': [
              {'id': 'settings', 'title': '设置', 'url': '/ui/index.html'},
            ],
          });
          api.on('POST /api/admin/plugins/repo.plugin/upgrade', {
            'task_id': 101,
          }, status: 202);
          api.on('GET /api/admin/tasks/101', {
            'id': 101,
            'kind': 'plugin:upgrade',
            'phase': 'start',
            'status': 'completed',
            'progress_done': 1,
            'progress_total': 1,
            'cancel_requested': false,
            'error': null,
            'result': {'plugin_id': 'repo.plugin', 'version': '2.0.0'},
            'started_at': '2026-10-31T10:00:00+00:00',
          });
        },
      );

      expect(find.text('详情测试插件'), findsOneWidget);
      expect(find.textContaining('第三方 · 来源：Iris 官方仓库'), findsOneWidget);
      expect(find.text('有新版本可用：v2.0.0'), findsOneWidget);
      expect(find.text('升级'), findsOneWidget);
      expect(find.text('卸载插件'), findsOneWidget);

      // 点击升级
      await tester.tap(find.widgetWithText(FilledButton, '升级'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      await tester.pumpAndSettle();

      expect(
        fakeApi.requests.contains(
          'POST /api/admin/plugins/repo.plugin/upgrade',
        ),
        isTrue,
      );
      expect(fakeApi.bodies['POST /api/admin/plugins/repo.plugin/upgrade'], {
        'version': '2.0.0',
        'accept_permissions': true,
      });
      expect(find.text('插件详情测试插件升级成功'), findsOneWidget);
    });
  });
}
