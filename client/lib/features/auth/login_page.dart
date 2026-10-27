import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../app/launch_splash.dart';
import '../../app/motion.dart';
import '../../app/theme.dart';
import '../../core/api/api_error.dart';
import '../../core/widgets/form_error_text.dart';
import '../../core/servers.dart';
import 'account_switcher.dart';
import 'auth_controller.dart';

class LoginPage extends ConsumerStatefulWidget {
  const LoginPage({super.key});

  @override
  ConsumerState<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends ConsumerState<LoginPage> {
  final _formKey = GlobalKey<FormState>();
  final _username = TextEditingController();
  final _password = TextEditingController();
  final _passwordFocus = FocusNode();
  bool _submitting = false;
  String? _error;

  @override
  void dispose() {
    _username.dispose();
    _password.dispose();
    _passwordFocus.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_submitting || !_formKey.currentState!.validate()) return;
    setState(() {
      _submitting = true;
      _error = null;
    });
    try {
      await ref
          .read(authControllerProvider.notifier)
          .login(_username.text.trim(), _password.text);
      // 登录成功后由路由守卫跳转
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = ApiError.from(e).message;
        _submitting = false;
      });
      // 提交时输入框被禁用会丢焦点；失败后回到密码框并全选，直接重输
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (!mounted) return;
        _passwordFocus.requestFocus();
        _password.selection = TextSelection(
          baseOffset: 0,
          extentOffset: _password.text.length,
        );
      });
      return;
    }
    if (mounted) setState(() => _submitting = false);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(24),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 380),
              child: AutofillGroup(
                child: Form(
                  key: _formKey,
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      const Center(child: IrisMark(dimension: 64)),
                      const SizedBox(height: 16),
                      Text(
                        '光圈',
                        textAlign: TextAlign.center,
                        style: theme.textTheme.headlineMedium,
                      ),
                      const SizedBox(height: 4),
                      Text(
                        '家里的片库，点开就看',
                        textAlign: TextAlign.center,
                        style: theme.textTheme.bodyMedium!.copyWith(
                          color: CinemaColors.text2,
                        ),
                      ),
                      const SizedBox(height: 32),
                      TextFormField(
                        controller: _username,
                        enabled: !_submitting,
                        autofillHints: const [AutofillHints.username],
                        textInputAction: TextInputAction.next,
                        autofocus: true,
                        decoration: const InputDecoration(labelText: '用户名'),
                        validator: (v) =>
                            (v == null || v.trim().isEmpty) ? '请输入用户名' : null,
                      ),
                      const SizedBox(height: 16),
                      TextFormField(
                        controller: _password,
                        focusNode: _passwordFocus,
                        enabled: !_submitting,
                        obscureText: true,
                        autofillHints: const [AutofillHints.password],
                        textInputAction: TextInputAction.done,
                        onFieldSubmitted: (_) => _submit(),
                        decoration: const InputDecoration(labelText: '密码'),
                        validator: (v) =>
                            (v == null || v.isEmpty) ? '请输入密码' : null,
                      ),
                      if (_error case final error?) ...[
                        const SizedBox(height: 16),
                        FormErrorText(error),
                      ],
                      const SizedBox(height: 24),
                      PressScale(
                        enabled: !_submitting,
                        child: FilledButton(
                          onPressed: _submitting ? null : _submit,
                          child: _submitting
                              ? const SizedBox.square(
                                  dimension: 20,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                  ),
                                )
                              : const Text('登录'),
                        ),
                      ),
                      const SizedBox(height: 16),
                      Text(
                        '账号由管理员创建，如需开通请联系管理员',
                        textAlign: TextAlign.center,
                        style: theme.textTheme.bodySmall,
                      ),
                      // 本机记住了其他账号时，可以直接切过去不用输密码
                      if (ref.watch(savedAccountsProvider).value?.isNotEmpty ??
                          false) ...[
                        const SizedBox(height: 8),
                        TextButton(
                          onPressed: _submitting
                              ? null
                              : () => showAccountSwitcher(context),
                          child: const Text('切换到已登录的账号'),
                        ),
                      ],
                      if (ref.watch(manageServersProvider)) ...[
                        const SizedBox(height: 24),
                        _CurrentServer(enabled: !_submitting),
                      ],
                    ],
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// App 上登录到哪台服务器，以及换一台的入口
class _CurrentServer extends ConsumerWidget {
  const _CurrentServer({required this.enabled});

  final bool enabled;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final server = ref.watch(serverListProvider).value?.current;
    final theme = Theme.of(context);
    return Column(
      children: [
        if (server != null)
          Text(
            '${server.name} · ${server.url}',
            textAlign: TextAlign.center,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: theme.textTheme.bodySmall,
          ),
        TextButton(
          onPressed: enabled ? () => context.go('/servers') : null,
          child: const Text('切换服务器'),
        ),
      ],
    );
  }
}
