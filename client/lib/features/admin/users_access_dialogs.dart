part of 'users_page.dart';

/// 「全部资料库」开关加每个资料库的勾选。开关打开时勾选不可改，但保留，关掉后按它分配。
class _LibraryAccessFields extends StatelessWidget {
  const _LibraryAccessFields({
    required this.libraries,
    required this.access,
    required this.enabled,
    required this.onChanged,
    this.autofocus = false,
  });

  final List<AdminLibrary> libraries;
  final UserLibraryAccess access;
  final bool enabled;
  final ValueChanged<UserLibraryAccess> onChanged;

  /// 对话框里没有输入框时，默认焦点放在「全部资料库」开关上
  final bool autofocus;

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        FocusRing(
          child: SwitchListTile(
            autofocus: autofocus,
            contentPadding: EdgeInsets.zero,
            value: access.all,
            title: const Text('全部资料库'),
            onChanged: enabled
                ? (value) => onChanged((ids: access.ids, all: value))
                : null,
          ),
        ),
        for (final library in libraries)
          FocusRing(
            child: CheckboxListTile(
              contentPadding: EdgeInsets.zero,
              value: access.all || access.ids.contains(library.id),
              title: Text(library.name),
              subtitle: Text(libraryKindLabel(library.kind)),
              onChanged: enabled && !access.all
                  ? (checked) => onChanged((
                      ids: checked!
                          ? {...access.ids, library.id}
                          : ({...access.ids}..remove(library.id)),
                      all: false,
                    ))
                  : null,
            ),
          ),
      ],
    );
  }
}

/// 勾选普通用户能看到的资料库。有变化时该用户会被退出登录，重新登录后按新权限浏览。
class _LibraryAccessDialog extends ConsumerStatefulWidget {
  const _LibraryAccessDialog({required this.user});

  final AppUser user;

  @override
  ConsumerState<_LibraryAccessDialog> createState() =>
      _LibraryAccessDialogState();
}

class _LibraryAccessDialogState extends ConsumerState<_LibraryAccessDialog> {
  /// 用户改过的设置；为空时显示服务端的当前值
  UserLibraryAccess? _selected;
  bool _submitting = false;
  String? _error;

  Future<void> _submit(
    UserLibraryAccess original,
    UserLibraryAccess selected,
  ) async {
    if (selected.all == original.all &&
        selected.ids.length == original.ids.length &&
        selected.ids.containsAll(original.ids)) {
      Navigator.of(context).pop(false);
      return;
    }
    setState(() {
      _submitting = true;
      _error = null;
    });
    try {
      await saveUserLibraries(
        ref.read(apiClientProvider),
        widget.user.id,
        selected,
      );
      if (mounted) Navigator.of(context).pop(true);
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = ApiError.from(e).message;
          _submitting = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final libraries = ref.watch(adminLibrariesProvider);
    final access = ref.watch(userLibrariesProvider(widget.user.id));
    final original = access.value;
    final selected = _selected ?? original;
    final list = libraries.value;
    final error = libraries.error ?? access.error;
    final Widget content;
    if (error != null && (list == null || original == null)) {
      content = ErrorRetry(
        message: ApiError.from(error).message,
        onRetry: () {
          ref.invalidate(adminLibrariesProvider);
          ref.invalidate(userLibrariesProvider(widget.user.id));
        },
      );
    } else if (list == null || selected == null) {
      content = const SizedBox(
        height: 120,
        child: Center(child: CircularProgressIndicator()),
      );
    } else {
      content = Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _LibraryAccessFields(
            libraries: list,
            access: selected,
            enabled: !_submitting,
            autofocus: true,
            onChanged: (value) => setState(() => _selected = value),
          ),
          const SizedBox(height: 8),
          Text(
            selected.all ? '修改后该用户会退出登录。' : '修改后该用户会退出登录。之后新建的资料库不会自动开放给这个用户。',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          if (_error != null) ...[
            const SizedBox(height: 8),
            Semantics(
              liveRegion: true,
              child: Text(
                _error!,
                style: Theme.of(context).textTheme.bodyMedium
                    ?.copyWith(color: CinemaColors.error),
              ),
            ),
          ],
        ],
      );
    }
    return AlertDialog(
      title: Text('${widget.user.username} 能看到的资料库'),
      content: SingleChildScrollView(child: content),
      actions: [
        TextButton(
          onPressed: _submitting
              ? null
              : () => Navigator.of(context).pop(false),
          child: const Text('取消'),
        ),
        FilledButton(
          onPressed:
              _submitting ||
                  list == null ||
                  original == null ||
                  selected == null
              ? null
              : () => _submit(original, selected),
          child: _submitting
              ? const SizedBox.square(
                  dimension: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Text('保存'),
        ),
      ],
    );
  }
}

class _CreateUserDialog extends ConsumerStatefulWidget {
  const _CreateUserDialog();

  @override
  ConsumerState<_CreateUserDialog> createState() => _CreateUserDialogState();
}

class _CreateUserDialogState extends ConsumerState<_CreateUserDialog> {
  final _formKey = GlobalKey<FormState>();
  final _username = TextEditingController();
  final _password = TextEditingController();
  String _role = 'user';

  /// 普通用户的可见范围；没改过时为空，按服务端默认分配当时的全部资料库
  UserLibraryAccess? _access;

  /// 普通用户是否允许下载在线字幕，默认开启（管理员始终允许）
  bool _allowSubtitleDownload = true;
  bool _submitting = false;
  String? _error;

  @override
  void dispose() {
    _username.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_submitting || !_formKey.currentState!.validate()) return;
    setState(() {
      _submitting = true;
      _error = null;
    });
    try {
      await createUser(
        ref.read(apiClientProvider),
        username: _username.text.trim(),
        password: _password.text,
        role: _role,
        access: _access,
        allowSubtitleDownload: _allowSubtitleDownload,
      );
      if (mounted) Navigator.of(context).pop(true);
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = ApiError.from(e).message;
          _submitting = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      // 输入法占去半个屏幕时（TV、横屏）标题和输入框一起滚动，光标所在处保持可见
      scrollable: true,
      title: const Text('新建用户'),
      content: Form(
        key: _formKey,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextFormField(
              controller: _username,
              autofocus: true,
              decoration: const InputDecoration(labelText: '用户名'),
              validator: (v) =>
                  (v == null || v.trim().isEmpty) ? '请输入用户名' : null,
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _password,
              obscureText: true,
              decoration: const InputDecoration(labelText: '初始密码'),
              validator: (v) => (v == null || v.length < minPasswordLength)
                  ? '至少 $minPasswordLength 个字符'
                  : null,
            ),
            const SizedBox(height: 16),
            SegmentedButton<String>(
              segments: const [
                ButtonSegment(value: 'user', label: Text('普通用户')),
                ButtonSegment(value: 'admin', label: Text('管理员')),
              ],
              selected: {_role},
              onSelectionChanged: (s) => setState(() => _role = s.first),
            ),
            // 管理员始终允许下载在线字幕，不需要开关
            if (_role == 'user')
              FocusRing(
                child: SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  value: _allowSubtitleDownload,
                  title: const Text('允许下载在线字幕'),
                  subtitle: const Text('详情页和播放器里可以搜索更多字幕'),
                  onChanged: _submitting
                      ? null
                      : (value) =>
                            setState(() => _allowSubtitleDownload = value),
                ),
              ),
            ...switch (ref.watch(adminLibrariesProvider).value) {
              final libraries? when libraries.isNotEmpty => [
                const SizedBox(height: 16),
                _LibraryAccessFields(
                  libraries: libraries,
                  access:
                      _access ??
                      (ids: {for (final l in libraries) l.id}, all: false),
                  enabled: !_submitting,
                  onChanged: (value) => setState(() => _access = value),
                ),
              ],
              // 资料库还没加载出来或者没有资料库时不显示，按默认分配
              _ => const <Widget>[],
            },
            if (_error != null) ...[
              const SizedBox(height: 12),
              Semantics(
                liveRegion: true,
                child: Text(
                  _error!,
                  style: Theme.of(context).textTheme.bodyMedium
                      ?.copyWith(color: CinemaColors.error),
                ),
              ),
            ],
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: _submitting
              ? null
              : () => Navigator.of(context).pop(false),
          child: const Text('取消'),
        ),
        FilledButton(
          onPressed: _submitting ? null : _submit,
          child: _submitting
              ? const SizedBox.square(
                  dimension: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : const Text('创建'),
        ),
      ],
    );
  }
}
