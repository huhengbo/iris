import 'package:dio/dio.dart';

import 'api/api_client.dart';
import 'api/api_error.dart';

const systemInfoPath = '/api/system/info';

/// 服务端的产品标识，用来确认地址指向的是本服务（ADR 0017）
const serverProduct = 'iris';

/// 客户端支持的接口版本；服务器更新时客户端提示升级
const supportedApiVersion = 1;

/// 服务器的自我描述。server_id 不随地址变化，令牌按它分开保存。
class ServerInfo {
  const ServerInfo({
    required this.id,
    required this.name,
    required this.apiVersion,
  });

  factory ServerInfo.fromJson(Map<String, dynamic> json) => ServerInfo(
    id: json['server_id'] as String,
    name: json['server_name'] as String,
    apiVersion: json['api_version'] as int,
  );

  final String id;
  final String name;
  final int apiVersion;
}

Future<ServerInfo> fetchServerInfo(Dio dio) async {
  final response = await dio.get<Map<String, dynamic>>(
    systemInfoPath,
    options: Options(extra: {skipAuthExtra: true}),
  );
  final data = response.data!;
  if (data['product'] != serverProduct) {
    throw const ApiError(code: 'not_iris', message: '这个地址不是光圈服务器');
  }
  final info = ServerInfo.fromJson(data);
  if (info.apiVersion > supportedApiVersion) {
    throw const ApiError(code: 'client_outdated', message: '服务器版本较新，请升级客户端');
  }
  return info;
}

/// 把接口返回的相对地址（如 /api/images/…）拼成当前服务器上的完整地址。
String resolveServerUrl(String baseUrl, String path) =>
    Uri.parse(baseUrl).resolve(path).toString();
