import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../../core/widgets/copy_row.dart';
import '../../core/widgets/focus_ring.dart';
import '../../core/widgets/picker_sheet.dart';
import '../../core/widgets/snack.dart';
import '../../core/api/api_error.dart';
import '../../core/providers.dart';
import '../../core/network_status.dart';
import '../library/browse_api.dart';
import 'cast_page.dart';
import 'dlna.dart';
import 'network_guard.dart';
import 'play_api.dart';
import 'player_page.dart';

part 'player_picker.dart';

/// 一次播放请求。
class PlayRequest {
  const PlayRequest({
    required this.itemId,
    required this.target,
    required this.subtitle,
    this.versions = const [],
    this.recoveryVersions = const [],
    this.subtitleId,
    this.playlistId,
  });

  /// 所属作品，播放后刷新它的详情（接着看的一集会变）
  final int itemId;
  final PlayTarget target;

  /// 选择播放器时标题下的一行，如「片名 · 第 2 集」
  final String subtitle;

  /// 一集有多个版本时，选播放器的同时可以选版本；不选就用 [target]
  final List<MediaVersion> versions;

  /// 播放签发失败时可切换的版本。电影通常在详情页先选版本，所以不重复显示在播放器选择器中。
  final List<MediaVersion> recoveryVersions;

  /// 用户在单集页明确选择的外挂字幕；为空时由服务端按偏好匹配。
  final int? subtitleId;

  /// 从播放列表播放。连播只有内置播放器能做，有内置播放器时不再让用户选。
  final int? playlistId;
}

typedef PlayChoice = ({ExternalPlayer player, int? versionId, bool remember});

enum PlayFailureRecovery { close, retry, chooseVersion, useBuiltin }

class PlayFailureInfo {
  const PlayFailureInfo({
    required this.title,
    required this.message,
    required this.recovery,
    this.retryLabel = '重试',
    this.reason = '',
  });

  final String title;
  final String message;
  final PlayFailureRecovery recovery;
  final String retryLabel;

  /// 「查看原因」展示的说明：错误码 + 通俗解释，不含播放地址或令牌。
  final String reason;
}

/// 错误码对应的原因说明。只用错误码和固定文案，服务端 message 可能带地址，不进入这里。
String playFailureReason(ApiError error) {
  final explain = switch (error.kind) {
    _
        when error.code == 'play_token_expired' ||
            error.code == 'play_link_invalid' ||
            error.statusCode == 410 =>
      '播放地址是短时有效的，放置太久或已被使用就会失效。重新获取即可，不会影响观看进度。',
    _ when error.code == 'player_unsupported' =>
      '当前播放器不能打开这个文件的格式或地址，换内置播放器或其他播放器即可。',
    ApiErrorKind.rateLimited => '资源来源对短时间内的请求做了限制。等待片刻再试，连续重试会让限制更久。',
    ApiErrorKind.resourceUnavailable =>
      '资源来源暂时不可用，可能是插件没有运行或账号状态有问题。稍后重试；持续出现请联系管理员。',
    ApiErrorKind.missingFile => '来源上的文件已被删除或移走，可以换一个版本，或让管理员重新同步。',
    ApiErrorKind.network => '设备到服务器的网络不通，检查网络后重试。',
    ApiErrorKind.server => '服务器暂时出错，稍后重试；持续出现请联系管理员。',
    _ => '服务器拒绝了这次请求。',
  };
  final status = error.statusCode == null ? '' : '（HTTP ${error.statusCode}）';
  return '错误码：${error.code}$status\n$explain';
}

PlayFailureInfo playFailureInfo(
  ApiError error, {
  required bool canChooseVersion,
  bool canUseBuiltin = false,
}) {
  if (canChooseVersion &&
      (error.code == 'not_found' || error.code == 'file_missing')) {
    return const PlayFailureInfo(
      title: '这个版本无法播放',
      message: '文件可能已被删除或移走，可以换一个版本继续播放。',
      recovery: PlayFailureRecovery.chooseVersion,
    );
  }
  if (error.code == 'public_url_not_configured') {
    if (canUseBuiltin) {
      return const PlayFailureInfo(
        title: '对外地址未配置',
        message: '当前播放器需要对外访问地址，可以改用内置播放器继续播放。',
        recovery: PlayFailureRecovery.useBuiltin,
        retryLabel: '改用内置播放器',
      );
    }
    return PlayFailureInfo(
      title: '暂时不能播放',
      message: error.message,
      recovery: PlayFailureRecovery.close,
    );
  }
  if (error.code == 'player_unsupported') {
    return PlayFailureInfo(
      title: '播放器不支持这个文件',
      message: canUseBuiltin ? '可以改用内置播放器继续播放。' : '请换一个播放器，或复制链接到其他播放器。',
      recovery: canUseBuiltin
          ? PlayFailureRecovery.useBuiltin
          : PlayFailureRecovery.close,
      retryLabel: '改用内置播放器',
      reason: playFailureReason(error),
    );
  }
  if (error.code == 'play_token_expired' ||
      error.code == 'play_link_invalid' ||
      error.statusCode == 410) {
    return PlayFailureInfo(
      title: '播放地址已过期',
      message: '播放地址已失效，重新获取后即可继续播放。',
      recovery: PlayFailureRecovery.retry,
      retryLabel: '重新获取',
      reason: playFailureReason(error),
    );
  }
  if (error.kind == ApiErrorKind.rateLimited) {
    final wait = error.retryAfter == null ? '' : '约 ${error.retryAfter} 秒后';
    return PlayFailureInfo(
      title: '资源来源暂时限制了访问',
      message: '来源限制了短时间内的请求，请等待${wait.isEmpty ? '片刻' : wait}再试，连续重试会让限制更久。',
      recovery: PlayFailureRecovery.retry,
      retryLabel: '稍后重试',
      reason: playFailureReason(error),
    );
  }
  if (error.kind == ApiErrorKind.resourceUnavailable) {
    return PlayFailureInfo(
      title: '资源来源暂时不可用',
      message: error.message,
      recovery: PlayFailureRecovery.retry,
      reason: playFailureReason(error),
    );
  }
  final retryable = error.retryable;
  return PlayFailureInfo(
    title: '播放地址获取失败',
    message: error.retryAfter == null
        ? error.message
        : '${error.message}（约 ${error.retryAfter} 秒后再试）',
    recovery: retryable ? PlayFailureRecovery.retry : PlayFailureRecovery.close,
    retryLabel: error.retryAfter == null ? '重试' : '稍后重试',
    reason: playFailureReason(error),
  );
}

/// 播放：选播放器（记住了且不用选版本时跳过）→ 签发播放链接 → 内置播放、投屏、调起或显示链接。
Future<void> play(BuildContext context, PlayRequest request) async {
  final container = ProviderScope.containerOf(context, listen: false);
  final platform = container.read(playPlatformProvider);
  final network = container.read(networkStatusProvider);
  var cellularCheck = await checkCellularPlayback(
    context,
    network,
    duringPlayback: false,
  );
  if (!context.mounted || !cellularCheck.allowed) return;
  var cellularConfirmed = cellularCheck.cellular;
  final remembered = await container.read(defaultPlayerProvider.future);
  if (!context.mounted) return;

  var target = request.target;
  int? subtitleId = request.subtitleId;
  var player = ExternalPlayer.builtin;
  final pickVersion = request.versions.length > 1;
  if (request.playlistId != null &&
      platform.players.contains(ExternalPlayer.builtin)) {
    player = ExternalPlayer.builtin;
  } else if (remembered != null && !pickVersion) {
    player = remembered;
  } else if (platform.players.length == 1 && !pickVersion) {
    player = platform.players.single;
  } else {
    final choice = await showPlayerPicker(
      context,
      subtitle: request.subtitle,
      players: platform.players,
      current: remembered,
      versions: request.versions,
    );
    if (choice == null || !context.mounted) return;
    player = choice.player;
    if (choice.versionId case final id?) {
      target = PlayTarget.version(id);
      // 版本改变后原字幕 ID 不再属于当前版本，交给服务端按偏好选择。
      subtitleId = null;
    }
    // 复制链接不是播放器，不改动记住的选择
    if (player.rememberable) {
      final saved = choice.remember ? player : null;
      if (saved != remembered) {
        await container.read(defaultPlayerProvider.notifier).set(saved);
      }
    }
    if (!context.mounted) return;
  }

  // 先选电视再签发：没找到电视时不白白记一次播放
  CastDevice? device;
  if (player == ExternalPlayer.cast) {
    device = await showCastDevicePicker(context);
    if (device == null || !context.mounted) return;
  }

  // 选播放器或电视期间可能刚好离开 Wi-Fi，签发前再检查一次。
  if (!cellularConfirmed) {
    cellularCheck = await checkCellularPlayback(
      context,
      network,
      duringPlayback: false,
    );
    if (!context.mounted || !cellularCheck.allowed) return;
    cellularConfirmed = cellularCheck.cellular;
  }

  var builtin = player == ExternalPlayer.builtin;
  final issued = await _issue(
    context,
    container,
    target,
    platform,
    request: request,
    player: player,
    builtin: builtin,
    subtitleId: subtitleId,
  );
  if (issued == null || !context.mounted) return;
  player = issued.player;
  builtin = player == ExternalPlayer.builtin;
  final ticket = issued.ticket;
  // 最近播放和接着看的一集都变了
  container
    ..invalidate(homeFeedProvider)
    ..invalidate(mediaDetailProvider(request.itemId));

  if (device != null) {
    await _cast(context, container, request, device, ticket);
    return;
  }

  if (builtin) {
    final args = PlayerArgs.fromTicket(
      ticket,
      container.read(apiBaseUrlProvider),
      castable: platform.players.contains(ExternalPlayer.cast),
      cellularConfirmed: cellularConfirmed,
      playlistId: request.playlistId,
    );
    // 在播放器里选了电视时带回来，接着投屏
    final handoff = await GoRouter.of(context)
        .push<CastDevice>('/player', extra: args);
    // 播放器退出前已存好进度，刷新进度条和已看标记
    _refresh(container, request);
    if (handoff == null || !context.mounted) return;
    // 投屏要对外地址，内置播放器的签发可能没有；按刚才的版本重新签发，从刚存的进度接着放
    final castTicket = await _issue(
      context,
      container,
      PlayTarget.version(ticket.versionId),
      platform,
      request: request,
      player: ExternalPlayer.cast,
      builtin: false,
      subtitleId: null,
    );
    if (castTicket == null || !context.mounted) return;
    await _cast(context, container, request, handoff, castTicket.ticket);
    return;
  }

  if (player == ExternalPlayer.system &&
      (platform == PlayPlatform.androidApp ||
          platform == PlayPlatform.androidTv)) {
    try {
      // 非内置播放时服务端一定给对外地址，没有时签发就已失败
      await container.read(videoIntentProvider)(ticket.url!);
    } catch (_) {
      if (!context.mounted) return;
      if (platform == PlayPlatform.androidTv) {
        showSnack(context, '没有找到能播放视频的应用，可以改用内置播放器');
      } else {
        showSnack(
          context,
          '没有找到能播放视频的应用，可以复制链接到其他播放器',
          action: SnackBarAction(
            label: '复制链接',
            onPressed: () => showPlayLinkDialog(context, ticket),
          ),
        );
      }
    }
    return;
  }

  final uri = ticket.launchUri(player);
  if (uri == null) {
    await showPlayLinkDialog(context, ticket);
    return;
  }
  final opened = await container
      .read(urlOpenerProvider)(uri)
      .catchError((Object _) => false);
  if (!opened && context.mounted) {
    showSnack(
      context,
      '没能打开 ${player.label}，可以复制链接到其他播放器',
      action: SnackBarAction(
        label: '复制链接',
        onPressed: () => showPlayLinkDialog(context, ticket),
      ),
    );
  }
}

Future<void> _cast(
  BuildContext context,
  ProviderContainer container,
  PlayRequest request,
  CastDevice device,
  PlayTicket ticket,
) async {
  await GoRouter.of(context)
      .push<void>('/cast', extra: CastArgs.fromTicket(device, ticket));
  // 投屏页退出前已存好进度
  _refresh(container, request);
}

void _refresh(ProviderContainer container, PlayRequest request) => container
  ..invalidate(homeFeedProvider)
  ..invalidate(mediaDetailProvider(request.itemId))
  ..invalidate(seasonProvider);

/// 签发期间挡住界面，避免连点签发多次。失败时提示并返回空。
typedef _IssuedPlay = ({PlayTicket ticket, ExternalPlayer player});

Future<_IssuedPlay?> _issue(
  BuildContext context,
  ProviderContainer container,
  PlayTarget target,
  PlayPlatform platform, {
  required PlayRequest request,
  required ExternalPlayer player,
  required bool builtin,
  required int? subtitleId,
}) async {
  var currentTarget = target;
  var currentPlayer = player;
  var currentBuiltin = builtin;
  final recoveryVersions = request.recoveryVersions.isNotEmpty
      ? request.recoveryVersions
      : request.versions;
  while (context.mounted) {
    if (!context.mounted) return null;
    final navigator = Navigator.of(context, rootNavigator: true);
    unawaited(
      showDialog<void>(
        context: context,
        barrierDismissible: false,
        builder: (_) => const _IssuingDialog(),
      ),
    );
    await Future<void>.delayed(Duration.zero);
    var loadingOpen = true;
    try {
      final ticket = await issuePlay(
        container.read(apiClientProvider),
        currentTarget,
        platform,
        builtin: currentBuiltin,
        subtitleId: subtitleId,
      );
      return (ticket: ticket, player: currentPlayer);
    } catch (e) {
      if (!context.mounted) return null;
      if (loadingOpen) {
        navigator.pop();
        loadingOpen = false;
      }
      final error = ApiError.from(e);
      final failure = playFailureInfo(
        error,
        canChooseVersion: recoveryVersions.length > 1,
        canUseBuiltin:
            currentPlayer != ExternalPlayer.builtin &&
            currentPlayer != ExternalPlayer.cast &&
            platform.players.contains(ExternalPlayer.builtin),
      );
      if (failure.recovery == PlayFailureRecovery.close) {
        showSnack(context, failure.message);
        return null;
      }
      final action = await _showPlayFailure(
        context,
        error,
        versions: recoveryVersions,
        canUseBuiltin:
            currentPlayer != ExternalPlayer.builtin &&
            currentPlayer != ExternalPlayer.cast &&
            platform.players.contains(ExternalPlayer.builtin),
      );
      if (action == null || action.recovery == PlayFailureRecovery.close) {
        return null;
      }
      if (action.recovery == PlayFailureRecovery.chooseVersion) {
        currentTarget = PlayTarget.version(action.versionId!);
        subtitleId = null;
      } else if (action.recovery == PlayFailureRecovery.useBuiltin) {
        currentPlayer = ExternalPlayer.builtin;
        currentBuiltin = true;
      }
    } finally {
      if (context.mounted && loadingOpen) navigator.pop();
    }
  }
  return null;
}

class _PlayFailureAction {
  const _PlayFailureAction(this.recovery, [this.versionId]);
  final PlayFailureRecovery recovery;
  final int? versionId;
}

Future<_PlayFailureAction?> _showPlayFailure(
  BuildContext context,
  ApiError error, {
  required List<MediaVersion> versions,
  required bool canUseBuiltin,
}) {
  final info = playFailureInfo(
    error,
    canChooseVersion: versions.length > 1,
    canUseBuiltin: canUseBuiltin,
  );
  return showDialog<_PlayFailureAction>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text(info.title),
      content: Text(info.message),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('关闭'),
        ),
        TextButton(
          onPressed: () => showDialog<void>(
            context: context,
            builder: (inner) => AlertDialog(
              title: const Text('失败原因'),
              content: SelectableText(info.reason),
              actions: [
                TextButton(
                  autofocus: true,
                  onPressed: () => Navigator.pop(inner),
                  child: const Text('知道了'),
                ),
              ],
            ),
          ),
          child: const Text('查看原因'),
        ),
        // 主动作（重试 / 选择版本）是默认焦点，遥控器直接确定即可
        if (info.recovery == PlayFailureRecovery.chooseVersion)
          FilledButton(
            autofocus: true,
            onPressed: () async {
              final id = await _chooseRecoveryVersion(context, versions);
              if (context.mounted && id != null) {
                Navigator.pop(
                  context,
                  _PlayFailureAction(PlayFailureRecovery.chooseVersion, id),
                );
              }
            },
            child: const Text('选择版本'),
          )
        else if (info.recovery == PlayFailureRecovery.retry ||
            info.recovery == PlayFailureRecovery.useBuiltin)
          FilledButton(
            autofocus: true,
            onPressed: () =>
                Navigator.pop(context, _PlayFailureAction(info.recovery)),
            child: Text(info.retryLabel),
          ),
      ],
    ),
  );
}

Future<int?> _chooseRecoveryVersion(
  BuildContext context,
  List<MediaVersion> versions,
) async {
  final picked = await showChoicePicker<int>(
    context,
    title: '选择播放版本',
    choices: [
      for (final v in versions) (value: v.id, label: v.label, subtitle: null),
    ],
    current: null,
  );
  return picked?.$1;
}

class _IssuingDialog extends StatelessWidget {
  const _IssuingDialog();

  @override
  Widget build(BuildContext context) {
    // 对话框没有可点的东西，焦点停在它自己身上，遥控器按键不会落到下面的页面
    return const PopScope(
      canPop: false,
      child: Focus(
        autofocus: true,
        child: Dialog(
          child: Padding(
            padding: EdgeInsets.symmetric(horizontal: 24, vertical: 20),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                SizedBox.square(
                  dimension: 20,
                  child: CircularProgressIndicator(strokeWidth: 2.5),
                ),
                SizedBox(width: 16),
                Text('正在获取播放地址…'),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
