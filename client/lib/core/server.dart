import 'package:dio/dio.dart';

import 'api/api_client.dart';
import 'api/api_error.dart';

const systemInfoPath = '/api/system/info';

/// 服务端的产品标识，用来确认地址指向的是本服务（ADR 0017）
const serverProduct = 'iris';

/// 客户端支持的接口版本；服务器更新时客户端提示升级
const supportedApiVersion = 1;

/// AI 助手哪些入口可用。没有配置对话模型时全部为 false，客户端不显示任何 AI 入口（ADR 0024）。
class AiAvailability {
  const AiAvailability({
    this.chat = false,
    this.askSearch = false,
    this.semantic = false,
  });

  /// 旧版本服务器没有这个字段，按不可用处理。
  factory AiAvailability.fromJson(Map<String, dynamic>? json) => json == null
      ? const AiAvailability()
      : AiAvailability(
          chat: json['chat'] as bool? ?? false,
          askSearch: json['ask_search'] as bool? ?? false,
          semantic: json['semantic'] as bool? ?? false,
        );

  /// AI 对话
  final bool chat;

  /// 搜索页的 AI 区块
  final bool askSearch;

  /// 语义召回（「像某部片那样」）
  final bool semantic;
}

/// 服务器的自我描述。server_id 不随地址变化，令牌按它分开保存。
class ServerInfo {
  const ServerInfo({
    required this.id,
    required this.name,
    required this.apiVersion,
    this.ai = const AiAvailability(),
  });

  factory ServerInfo.fromJson(Map<String, dynamic> json) => ServerInfo(
    id: json['server_id'] as String,
    name: json['server_name'] as String,
    apiVersion: json['api_version'] as int,
    ai: AiAvailability.fromJson(json['ai'] as Map<String, dynamic>?),
  );

  final String id;
  final String name;
  final int apiVersion;
  final AiAvailability ai;
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
