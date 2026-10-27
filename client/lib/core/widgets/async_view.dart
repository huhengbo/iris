import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../features/auth/auth_controller.dart';
import '../api/api_error.dart';
import 'api_error_style.dart';

/// 统一的加载、错误（带重试）和数据状态。
class AsyncView<T> extends StatelessWidget {
  const AsyncView({
    super.key,
    required this.value,
    required this.onRetry,
    required this.builder,
  });

  final AsyncValue<T> value;
  final VoidCallback onRetry;
  final Widget Function(T data) builder;

  @override
  Widget build(BuildContext context) {
    return switch (value) {
      AsyncData(:final value) => builder(value),
      // 后台刷新失败时继续显示上一次的内容，不用错误页顶掉它
      AsyncError(hasValue: true, :final value) => builder(value as T),
      AsyncError(:final error) => ErrorRetry(
        message: ApiError.from(error).message,
        kind: ApiError.from(error).kind,
        onRetry: onRetry,
      ),
      _ => const Center(child: CircularProgressIndicator()),
    };
  }
}

class ErrorRetry extends ConsumerWidget {
  const ErrorRetry({
    super.key,
    required this.message,
    required this.onRetry,
    this.kind = ApiErrorKind.unknown,
    this.secondary,
  });

  final String message;
  final VoidCallback onRetry;
  final ApiErrorKind kind;

  /// 重试下方的另一条出路，如「切换服务器」
  final Widget? secondary;

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final isAdmin = ref.watch(authControllerProvider).value?.isAdmin == true;
    final style = apiErrorStyle(
      kind,
      Theme.of(context).colorScheme,
      isAdmin: isAdmin,
    );
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(style.icon, size: 40, color: style.color),
            const SizedBox(height: 12),
            Text(style.title, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 4),
            Text(message, textAlign: TextAlign.center),
            const SizedBox(height: 4),
            Text(
              style.hint,
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.bodySmall,
            ),
            const SizedBox(height: 16),
            FilledButton.tonal(onPressed: onRetry, child: const Text('重试')),
            if (secondary case final secondary?) ...[
              const SizedBox(height: 8),
              secondary,
            ],
          ],
        ),
      ),
    );
  }
}
