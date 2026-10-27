import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/providers.dart';

class WebDavInfo {
  const WebDavInfo({required this.enabled, required this.username, this.url});

  factory WebDavInfo.fromJson(Map<String, dynamic> json) => WebDavInfo(
    enabled: json['enabled'] as bool,
    username: json['username'] as String,
    url: json['url'] as String?,
  );

  final bool enabled;
  final String username;
  final String? url;
}

final webDavProvider = FutureProvider.autoDispose<WebDavInfo>((ref) async {
  final response = await ref
      .watch(apiClientProvider)
      .get<Map<String, dynamic>>('/api/me/webdav');
  return WebDavInfo.fromJson(response.data!);
});

Future<WebDavInfo> saveWebDav(Dio dio, {required bool enabled}) async {
  final response = await dio.patch<Map<String, dynamic>>(
    '/api/me/webdav',
    data: {'enabled': enabled},
  );
  return WebDavInfo.fromJson(response.data!);
}
