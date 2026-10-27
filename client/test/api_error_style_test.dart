import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:iris/core/api/api_error.dart';
import 'package:iris/core/widgets/api_error_style.dart';
import 'package:iris/core/widgets/async_view.dart';
import 'package:iris/core/widgets/snack.dart';
import 'package:iris/features/auth/auth_controller.dart';

void main() {
  final scheme = ColorScheme.fromSeed(seedColor: Colors.blue);

  test('每个 ApiErrorKind 都有完整的呈现', () {
    for (final kind in ApiErrorKind.values) {
      for (final admin in [false, true]) {
        final s = apiErrorStyle(kind, scheme, isAdmin: admin);
        expect(s.title, isNotEmpty, reason: '$kind');
        expect(s.hint, isNotEmpty, reason: '$kind');
      }
    }
  });

  test('限流提示稍后重试，来源不可用对管理员指向资源来源页', () {
    expect(
      apiErrorStyle(ApiErrorKind.rateLimited, scheme).hint,
      contains('稍后'),
    );
    final admin = apiErrorStyle(
      ApiErrorKind.resourceUnavailable,
      scheme,
      isAdmin: true,
    );
    final user = apiErrorStyle(ApiErrorKind.resourceUnavailable, scheme);
    expect(admin.hint, contains('资源来源'));
    expect(user.hint, contains('联系管理员'));
    expect(admin.hint, isNot(user.hint));
  });

  testWidgets('showApiErrorSnack 显示错误信息', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: Builder(
            builder: (context) => TextButton(
              onPressed: () => showApiErrorSnack(
                context,
                const ApiError(code: 'x', message: '出错啦', statusCode: 500),
              ),
              child: const Text('go'),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('go'));
    await tester.pump();
    expect(find.text('出错啦'), findsOneWidget);
  });

  for (final (role, expected) in [('admin', '设置 › 资源来源'), ('user', '联系管理员')]) {
    testWidgets('ErrorRetry 按角色($role)显示来源建议', (tester) async {
      await tester.pumpWidget(
        ProviderScope(
          overrides: [authControllerProvider.overrideWith(() => _User(role))],
          child: MaterialApp(
            home: Scaffold(
              body: ErrorRetry(
                message: '来源暂时不可用',
                kind: ApiErrorKind.resourceUnavailable,
                onRetry: () {},
              ),
            ),
          ),
        ),
      );
      await tester.pump();
      expect(find.text('资源来源不可用'), findsOneWidget);
      expect(find.text('来源暂时不可用'), findsOneWidget);
      expect(find.textContaining(expected), findsOneWidget);
    });
  }
}

class _User extends AuthController {
  _User(this.role);
  final String role;

  @override
  Future<AppUser?> build() async => AppUser(
    id: 1,
    username: 'u',
    role: role,
    disabled: false,
    allowSubtitleDownload: true,
  );
}
