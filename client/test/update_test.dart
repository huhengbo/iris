import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/features/update/update_controller.dart';
import 'package:iris/features/update/update_models.dart';
import 'package:iris/features/update/update_platform.dart';
import 'package:iris/features/update/update_preferences.dart';

/// 按地址返回固定内容的假网络，记录请求过的地址。
class _FakeGitHub implements HttpClientAdapter {
  _FakeGitHub(this.responses);

  final Map<String, (int, List<int>)> responses;
  final requested = <String>[];

  @override
  Future<ResponseBody> fetch(
    RequestOptions options,
    Stream<Uint8List>? requestStream,
    Future<void>? cancelFuture,
  ) async {
    final url = options.uri.toString();
    requested.add(url);
    final (status, body) = responses[url] ?? (404, utf8.encode('Not Found'));
    return ResponseBody.fromBytes(body, status);
  }

  @override
  void close({bool force = false}) {}
}

/// 假原生层：sha256 按给定顺序返回，记录是否清理和安装过。
class _FakePlatform implements UpdatePlatform {
  _FakePlatform({required this.path, this.abis = const ['arm64-v8a']});

  final String path;
  final List<String> abis;
  final digests = <String?>[];
  bool cleared = false;

  @override
  Future<List<String>> supportedAbis() async => abis;

  @override
  Future<String> prepareFile(String name) async => path;

  @override
  Future<String?> sha256(String path) async =>
      digests.isEmpty ? null : digests.removeAt(0);

  @override
  Future<void> clear() async => cleared = true;

  @override
  Future<InstallOutcome> install(String path) async =>
      InstallOutcome.installing;
}

Map<String, Object?> _manifest({String version = '0.4.0'}) => {
  'version': version,
  'version_code': 400,
  'notes': '### 新功能\n\n- 应用内升级',
  'min_version': null,
  'apks': {
    'arm64-v8a': {
      'url':
          'https://github.com/huhengbo/iris/releases/download/v$version/app-arm64-v8a-release.apk',
      'size': 3,
      'sha256': 'ABC',
    },
    'x86_64': {
      'url':
          'https://github.com/huhengbo/iris/releases/download/v$version/app-x86_64-release.apk',
      'size': 3,
      'sha256': 'def',
    },
  },
};

UpdateService _service(
  _FakeGitHub github,
  _FakePlatform platform, {
  String current = '0.3.0',
}) => UpdateService(
  dio: Dio()..httpClientAdapter = github,
  platform: platform,
  currentVersion: () async => current,
);

void main() {
  group('版本号', () {
    test('逐段按数字比较', () {
      expect(compareVersions('0.10.0', '0.9.0'), 1);
      expect(compareVersions('0.3.0', '0.3.0'), 0);
      expect(compareVersions('1.0.0', '1.0.1'), -1);
      expect(compareVersions(' 2.0.0 ', '1.9.9'), 1);
    });

    test('不是 x.y.z 时无法比较', () {
      expect(compareVersions('0.3', '0.3.0'), isNull);
      expect(compareVersions('0.3.0-beta', '0.3.0'), isNull);
      expect(compareVersions('v0.3.0', '0.3.0'), isNull);
      expect(compareVersions('', '0.3.0'), isNull);
    });
  });

  group('解析 update.json', () {
    test('按契约解析，sha256 统一小写，min_version 为空', () {
      final update = AppUpdate.tryParse(_manifest())!;
      expect(update.version, '0.4.0');
      expect(update.versionCode, 400);
      expect(update.minVersion, isNull);
      expect(update.apks.keys, ['arm64-v8a', 'x86_64']);
      expect(update.apks['arm64-v8a']!.sha256, 'abc');
      expect(update.apks['arm64-v8a']!.size, 3);
    });

    test('结构不对时返回空，坏掉的单个安装包被跳过', () {
      expect(AppUpdate.tryParse(null), isNull);
      expect(AppUpdate.tryParse('0.4.0'), isNull);
      expect(AppUpdate.tryParse({'apks': {}}), isNull);
      expect(AppUpdate.tryParse({'version': 4}), isNull);
      final update = AppUpdate.tryParse({
        'version': '0.4.0',
        'notes': 1,
        'apks': {
          'arm64-v8a': {'url': 'https://x/a.apk', 'sha256': 'aa', 'size': '3'},
          'armeabi-v7a': {'url': 'https://x/b.apk'},
          'x86_64': 'oops',
        },
      })!;
      expect(update.notes, '');
      expect(update.apks.keys, ['arm64-v8a']);
      expect(update.apks['arm64-v8a']!.size, isNull);
    });
  });

  test('按设备 ABI 顺序挑安装包', () {
    final apks = AppUpdate.tryParse(_manifest())!.apks;
    expect(selectApk(apks, ['x86_64', 'arm64-v8a']), same(apks['x86_64']));
    expect(
      selectApk(apks, ['armeabi-v7a', 'arm64-v8a']),
      same(apks['arm64-v8a']),
    );
    expect(selectApk(apks, ['armeabi-v7a']), isNull);
  });

  test('自动检查每 24 小时最多一次，关闭后不检查', () {
    final now = DateTime(2026, 11, 3, 12);
    bool due(DateTime? last, {bool enabled = true}) =>
        autoCheckDue(now: now, lastCheckAt: last, enabled: enabled);
    expect(due(null), isTrue);
    expect(due(now.subtract(const Duration(hours: 23))), isFalse);
    expect(due(now.subtract(const Duration(hours: 24))), isTrue);
    // 系统时间被往回调过
    expect(due(now.add(const Duration(hours: 1))), isTrue);
    expect(due(null, enabled: false), isFalse);
  });

  test('更新说明去掉 Markdown 标记', () {
    expect(
      plainNotes('### ✨ 新功能\n\n\n\n- **AI** 助手，见 `设置`\n'),
      '✨ 新功能\n\n- AI 助手，见 设置',
    );
  });

  group('下载加速前缀', () {
    test('校验：空串合法，其他必须是 http(s)', () {
      expect(downloadPrefixError(''), isNull);
      expect(downloadPrefixError('  '), isNull);
      expect(downloadPrefixError('https://proxy.example/'), isNull);
      expect(downloadPrefixError('proxy.example'), isNotNull);
      expect(downloadPrefixError('ftp://proxy.example/'), isNotNull);
    });

    test('拼接：补末尾斜杠，空前缀原样返回', () {
      const url = 'https://github.com/a.apk';
      expect(withDownloadPrefix('', url), url);
      expect(
        withDownloadPrefix(' https://p.example ', url),
        'https://p.example/https://github.com/a.apk',
      );
      expect(
        withDownloadPrefix('https://p.example/gh/', url),
        'https://p.example/gh/https://github.com/a.apk',
      );
      // 非法的旧数据按不加速处理
      expect(withDownloadPrefix('p.example', url), url);
    });
  });

  group('UpdateService', () {
    late Directory temp;

    setUp(() => temp = Directory.systemTemp.createTempSync('iris-update'));
    tearDown(() => temp.deleteSync(recursive: true));

    test('清单地址拼上加速前缀', () async {
      final github = _FakeGitHub({
        'https://p.example/$updateManifestUrl': (
          200,
          utf8.encode(jsonEncode(_manifest())),
        ),
      });
      final service = _service(github, _FakePlatform(path: ''));
      final update = await service.fetchManifest('https://p.example');
      expect(update.version, '0.4.0');
    });

    test('404 和格式错误给出可读的原因', () async {
      final missing = _service(_FakeGitHub({}), _FakePlatform(path: ''));
      await expectLater(
        missing.fetchManifest(''),
        throwsA(
          isA<UpdateException>().having(
            (e) => e.message,
            'message',
            contains('404'),
          ),
        ),
      );
      final broken = _service(
        _FakeGitHub({updateManifestUrl: (200, utf8.encode('<html>'))}),
        _FakePlatform(path: ''),
      );
      await expectLater(
        broken.fetchManifest(''),
        throwsA(isA<UpdateException>()),
      );
    });

    test('已下载且校验通过时不再请求网络', () async {
      final github = _FakeGitHub({});
      final platform = _FakePlatform(path: '${temp.path}/a.apk')
        ..digests.add('abc');
      final update = AppUpdate.tryParse(_manifest())!;
      final path = await _service(
        github,
        platform,
      ).download(update, update.apks['arm64-v8a']!, prefix: '');
      expect(path, '${temp.path}/a.apk');
      expect(github.requested, isEmpty);
    });

    test('下载后校验不一致时清掉文件并报错', () async {
      final update = AppUpdate.tryParse(_manifest())!;
      final apk = update.apks['arm64-v8a']!;
      final github = _FakeGitHub({
        'https://p.example/${apk.url}': (200, [1, 2, 3]),
      });
      final platform = _FakePlatform(path: '${temp.path}/a.apk')
        ..digests.addAll([null, 'not-abc']);
      await expectLater(
        _service(
          github,
          platform,
        ).download(update, apk, prefix: 'https://p.example/'),
        throwsA(isA<UpdateException>()),
      );
      expect(github.requested, ['https://p.example/${apk.url}']);
      expect(File('${temp.path}/a.apk').readAsBytesSync(), [1, 2, 3]);
      expect(platform.cleared, isTrue);
    });
  });

  group('UpdateController', () {
    ProviderContainer container(
      _FakeGitHub github,
      MemoryUpdatePreferences prefs, {
      String current = '0.3.0',
      List<String> abis = const ['arm64-v8a'],
    }) {
      final container = ProviderContainer(
        overrides: [
          updateSupportedProvider.overrideWithValue(true),
          updatePreferencesProvider.overrideWithValue(prefs),
          updateServiceProvider.overrideWithValue(
            _service(
              github,
              _FakePlatform(path: '', abis: abis),
              current: current,
            ),
          ),
        ],
      );
      addTearDown(container.dispose);
      return container;
    }

    _FakeGitHub withManifest([String version = '0.4.0']) => _FakeGitHub({
      updateManifestUrl: (
        200,
        utf8.encode(jsonEncode(_manifest(version: version))),
      ),
    });

    test('手动检查：有新版本、已是最新、本机架构没有安装包', () async {
      final newer = await container(
        withManifest(),
        MemoryUpdatePreferences(),
      ).read(updateControllerProvider.notifier).check();
      expect(newer, isA<UpdateAvailable>());
      expect((newer as UpdateAvailable).apk, isNotNull);

      final same = await container(
        withManifest('0.3.0'),
        MemoryUpdatePreferences(),
      ).read(updateControllerProvider.notifier).check();
      expect(same, isA<UpdateUpToDate>());

      final noApk = await container(
        withManifest(),
        MemoryUpdatePreferences(),
        abis: ['armeabi-v7a'],
      ).read(updateControllerProvider.notifier).check();
      expect((noApk as UpdateAvailable).apk, isNull);
    });

    test('手动检查不管是否跳过过该版本', () async {
      final status = await container(
        withManifest(),
        MemoryUpdatePreferences(skippedVersion: '0.4.0'),
      ).read(updateControllerProvider.notifier).check();
      expect(status, isA<UpdateAvailable>());
    });

    test('自动检查：记录时间，24 小时内和每次启动只查一次', () async {
      final now = DateTime(2026, 11, 3, 12);
      final github = withManifest();
      final prefs = MemoryUpdatePreferences();
      final controller = container(
        github,
        prefs,
      ).read(updateControllerProvider.notifier);
      expect(await controller.autoCheck(now: now), isNotNull);
      expect(prefs.lastCheckAt, now);
      // 同一次启动不再查
      expect(await controller.autoCheck(now: now), isNull);
      expect(github.requested, hasLength(1));

      // 新的一次启动，但还没到 24 小时
      final later = container(
        github,
        prefs,
      ).read(updateControllerProvider.notifier);
      expect(
        await later.autoCheck(now: now.add(const Duration(hours: 1))),
        isNull,
      );
      expect(github.requested, hasLength(1));
    });

    test('自动检查：跳过的版本不弹窗，关闭开关不请求', () async {
      final github = withManifest();
      final skipped = container(
        github,
        MemoryUpdatePreferences(skippedVersion: '0.4.0'),
      );
      expect(
        await skipped.read(updateControllerProvider.notifier).autoCheck(),
        isNull,
      );
      // 状态里仍然记着有新版本，「关于」页能看到
      expect(skipped.read(updateControllerProvider), isA<UpdateAvailable>());

      final off = _FakeGitHub({});
      expect(
        await container(
          off,
          MemoryUpdatePreferences(autoCheck: false),
        ).read(updateControllerProvider.notifier).autoCheck(),
        isNull,
      );
      expect(off.requested, isEmpty);
    });

    test('自动检查失败不弹窗，也算一次', () async {
      final prefs = MemoryUpdatePreferences();
      final c = container(_FakeGitHub({}), prefs);
      expect(
        await c.read(updateControllerProvider.notifier).autoCheck(),
        isNull,
      );
      expect(c.read(updateControllerProvider), isA<UpdateFailed>());
      expect(prefs.lastCheckAt, isNotNull);
    });

    test('设置：保存规范化后的前缀', () async {
      final prefs = MemoryUpdatePreferences();
      final c = container(_FakeGitHub({}), prefs);
      await c.read(updateSettingsProvider.future);
      await c
          .read(updateSettingsProvider.notifier)
          .setDownloadPrefix(' https://p.example ');
      expect(prefs.downloadPrefix, 'https://p.example/');
      expect(
        c.read(updateSettingsProvider).value!.downloadPrefix,
        'https://p.example/',
      );
      await c.read(updateSettingsProvider.notifier).setAutoCheck(false);
      expect(prefs.autoCheck, isFalse);
    });
  });
}
