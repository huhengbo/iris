import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';
import '../auth/auth_controller.dart';

const usersPath = '/api/admin/users';

/// 管理后台看到的用户，比 [AppUser] 多了可见范围开关和最近活动。
class AdminUser {
  const AdminUser({
    required this.user,
    required this.allLibraries,
    this.lastSeenAt,
  });

  factory AdminUser.fromJson(Map<String, dynamic> json) => AdminUser(
    user: AppUser.fromJson(json),
    allLibraries: json['all_libraries'] as bool? ?? false,
    lastSeenAt: json['last_seen_at'] == null
        ? null
        : DateTime.parse(json['last_seen_at'] as String).toLocal(),
  );

  final AppUser user;

  /// 能访问全部资料库，包括以后新建的
  final bool allLibraries;

  /// 最近一次登录或刷新令牌的时间，精度约 15 分钟；从没登录过为空
  final DateTime? lastSeenAt;
}

final adminUsersProvider = FutureProvider.autoDispose<List<AdminUser>>((
  ref,
) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<List<dynamic>>(usersPath);
  return [
    for (final item in response.data!)
      AdminUser.fromJson(item as Map<String, dynamic>),
  ];
});

final userSessionsProvider = FutureProvider.autoDispose
    .family<List<Map<String, dynamic>>, int>((ref, userId) async {
      final response = await ref
          .watch(apiClientProvider)
          .get<List<dynamic>>('$usersPath/$userId/sessions');
      return [
        for (final raw in response.data ?? const [])
          raw as Map<String, dynamic>,
      ];
    });

/// 用户的可见范围：勾选的资料库，以及是否开放全部资料库（包括以后新建的）。
typedef UserLibraryAccess = ({Set<int> ids, bool all});

final userLibrariesProvider = FutureProvider.autoDispose
    .family<UserLibraryAccess, int>((ref, userId) async {
      final response = await ref
          .watch(apiClientProvider)
          .get<Map<String, dynamic>>('$usersPath/$userId/libraries');
      final data = response.data!;
      return (
        ids: {for (final id in data['library_ids'] as List<dynamic>) id as int},
        all: data['all_libraries'] as bool? ?? false,
      );
    });

Future<void> createUser(
  Dio dio, {
  required String username,
  required String password,
  required String role,
  UserLibraryAccess? access,
  bool allowSubtitleDownload = true,
}) => dio.post<void>(
  usersPath,
  data: {
    'username': username,
    'password': password,
    'role': role,
    if (role == 'user') 'allow_subtitle_download': allowSubtitleDownload,
    if (role == 'user' && access != null) ...{
      'library_ids': access.ids.toList()..sort(),
      'all_libraries': access.all,
    },
  },
);

Future<void> resetUserPassword(Dio dio, int userId, String password) =>
    dio.post<void>(
      '$usersPath/$userId/password',
      data: {'new_password': password},
    );

Future<void> setUserRole(Dio dio, int userId, String role) =>
    dio.patch<void>('$usersPath/$userId', data: {'role': role});

Future<void> setUserDisabled(Dio dio, int userId, bool disabled) =>
    dio.patch<void>('$usersPath/$userId', data: {'disabled': disabled});

/// 普通用户是否允许搜索、下载在线字幕。管理员始终允许，不经过这个开关。
Future<void> setUserSubtitleDownload(Dio dio, int userId, bool allowed) =>
    dio.patch<void>(
      '$usersPath/$userId',
      data: {'allow_subtitle_download': allowed},
    );

Future<void> deleteUser(Dio dio, int userId) =>
    dio.delete<void>('$usersPath/$userId');

Future<void> saveUserLibraries(Dio dio, int userId, UserLibraryAccess access) =>
    dio.put<void>(
      '$usersPath/$userId/libraries',
      data: {
        'library_ids': access.ids.toList()..sort(),
        'all_libraries': access.all,
      },
    );

Future<void> revokeUserSession(Dio dio, int userId, int sessionId) =>
    dio.delete<void>('$usersPath/$userId/sessions/$sessionId');

Future<void> revokeAllUserSessions(Dio dio, int userId) =>
    dio.delete<void>('$usersPath/$userId/sessions');
