import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';
import 'package:xml/xml.dart';

part 'dlna_discovery.dart';

/// 投屏协议。DLNA 见 ADR 0018，AirPlay 见 ADR 0023。
enum CastProtocol {
  dlna('DLNA'),
  airplay('AirPlay');

  const CastProtocol(this.label);

  final String label;
}

/// 局域网里能投屏的电视（DLNA 媒体渲染器或 AirPlay 接收端）。
class CastDevice {
  const CastDevice({
    required this.id,
    required this.name,
    required this.controlUrl,
    required this.serviceType,
    this.protocol = CastProtocol.dlna,
    this.connectionUrl,
    this.connectionType,
  });

  final CastProtocol protocol;

  /// DLNA 的 ConnectionManager 服务，搜索时问它电视能放什么格式，用来排除音箱
  final Uri? connectionUrl;
  final String? connectionType;

  /// UDN，同一台电视对多种搜索都会应答，按它去重
  final String id;
  final String name;

  /// DLNA 是 AVTransport 服务的控制地址，AirPlay 是接收端的根地址
  final Uri controlUrl;

  /// 应答里的服务类型，SOAP 调用原样带上（AVTransport:1 或更高版本）
  final String serviceType;
}

class CastException implements Exception {
  const CastException(this.message, {this.fault = false});

  final String message;

  /// 电视收到了请求但拒绝执行（SOAP 错误），而不是连不上
  final bool fault;

  @override
  String toString() => message;
}

/// 推地址、开始播放这类动作，电视要先去拉流探测格式才应答，
/// 经过 302 跳转到网盘直链时常常超过局域网里普通动作的等待时间（5 秒）。
const castLoadTimeout = Duration(seconds: 30);

/// 请求电视时的网络错误。超时不是网络不通：电视收到了请求，只是还没应答，
/// 多半在加载视频，而且可能已经开始放了。
CastException castNetworkError(DioException e, String action) {
  debugPrint(
    'cast: $action failed: ${e.type} ${e.message} (${e.requestOptions.uri})',
  );
  return e.type == DioExceptionType.receiveTimeout
      ? const CastException('电视没有及时回应，可能还在加载视频，稍后再试')
      : const CastException('连不上电视，请确认手机和电视在同一个 Wi-Fi');
}

enum CastState { connecting, playing, paused, stopped }

class CastStatus {
  const CastStatus({required this.state, this.position, this.duration});

  final CastState state;
  final Duration? position;

  /// 电视还没读到片长时为空
  final Duration? duration;
}

/// 投屏控制：推地址、播放暂停、拖动、查询进度。
abstract interface class CastRenderer {
  Future<void> load(String url, String title);
  Future<void> play();
  Future<void> pause();
  Future<void> stop();
  Future<void> seek(Duration position);
  Future<CastStatus> status();
}

abstract interface class CastDiscovery {
  /// 搜索 [timeout] 这么久，找到一台推一台；同一台只推一次。
  Stream<CastDevice> search({Duration timeout});
}

// ---- AVTransport 控制 ----

class DlnaRenderer implements CastRenderer {
  DlnaRenderer(
    this.device,
    this._dio, {
    this.playRetryDelay = const Duration(seconds: 1),
  });

  final CastDevice device;
  final Dio _dio;

  /// 电视还在加载刚推上来的地址时会拒绝播放，隔这么久再试
  final Duration playRetryDelay;

  @override
  Future<void> load(String url, String title) async {
    try {
      await _call(
        'SetAVTransportURI',
        args: {
          'CurrentURI': url,
          'CurrentURIMetaData': didlMetadata(url, title),
        },
        receiveTimeout: castLoadTimeout,
      );
    } on CastException catch (e) {
      // 有的电视解析不了描述信息就整条拒绝，不带描述再试一次
      if (!e.fault) rethrow;
      await _call(
        'SetAVTransportURI',
        args: {'CurrentURI': url, 'CurrentURIMetaData': ''},
        receiveTimeout: castLoadTimeout,
      );
    }
  }

  @override
  Future<void> play() async {
    // 电视还在加载刚推上来的地址时会拒绝播放（701 等），稍等再试
    for (var attempt = 1; ; attempt++) {
      try {
        await _call(
          'Play',
          args: {'Speed': '1'},
          receiveTimeout: castLoadTimeout,
        );
        return;
      } on CastException catch (e) {
        if (!e.fault || attempt >= 4) rethrow;
        await Future<void>.delayed(playRetryDelay);
      }
    }
  }

  @override
  Future<void> pause() => _call('Pause');

  @override
  Future<void> stop() => _call('Stop');

  @override
  Future<void> seek(Duration position) => _call(
    'Seek',
    args: {'Unit': 'REL_TIME', 'Target': formatUpnpTime(position)},
  );

  @override
  Future<CastStatus> status() async {
    final transport = await _call('GetTransportInfo');
    final position = await _call('GetPositionInfo');
    return CastStatus(
      state: switch (transport['CurrentTransportState']) {
        'PLAYING' => CastState.playing,
        'PAUSED_PLAYBACK' || 'PAUSED_RECORDING' => CastState.paused,
        'TRANSITIONING' => CastState.connecting,
        _ => CastState.stopped,
      },
      position: parseUpnpTime(position['RelTime']),
      duration: switch (parseUpnpTime(position['TrackDuration'])) {
        final d? when d > Duration.zero => d,
        _ => null,
      },
    );
  }

  Future<Map<String, String>> _call(
    String action, {
    Map<String, String> args = const {},
    Duration? receiveTimeout,
  }) async {
    final Response<String> response;
    try {
      response = await _dio.postUri<String>(
        device.controlUrl,
        data: soapEnvelope(device.serviceType, action, args),
        options: Options(
          responseType: ResponseType.plain,
          validateStatus: (_) => true,
          receiveTimeout: receiveTimeout,
          // 电视的 UPnP 服务多半不会通知客户端空闲连接已经关了，复用旧连接会得到
          // 「Connection closed before full header was received」，每个动作用新连接
          persistentConnection: false,
          headers: {
            'Content-Type': 'text/xml; charset="utf-8"',
            'SOAPACTION': '"${device.serviceType}#$action"',
          },
        ),
      );
    } on DioException catch (e) {
      throw castNetworkError(e, action);
    }
    final status = response.statusCode ?? 0;
    final body = response.data ?? '';
    if (status != 200) {
      debugPrint(
        'cast: $action refused: HTTP $status ${body.trim()} '
        '(${device.controlUrl})',
      );
      throw CastException(
        '电视拒绝了操作（${soapFault(body) ?? 'HTTP $status'}）',
        fault: true,
      );
    }
    return soapResult(body, action);
  }
}

@visibleForTesting
String soapEnvelope(
  String serviceType,
  String action,
  Map<String, String> args, {
  bool instance = true,
}) {
  final buffer = StringBuffer()
    ..write('<?xml version="1.0" encoding="utf-8"?>')
    ..write(
      '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
      's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">',
    )
    ..write('<s:Body><u:$action xmlns:u="${_escape(serviceType)}">');
  // ConnectionManager 的动作没有 InstanceID，多带一个参数严格的电视会拒绝
  if (instance) buffer.write('<InstanceID>0</InstanceID>');
  args.forEach(
    (name, value) => buffer.write('<$name>${_escape(value)}</$name>'),
  );
  buffer.write('</u:$action></s:Body></s:Envelope>');
  return buffer.toString();
}

/// 动作应答里的输出参数。
@visibleForTesting
Map<String, String> soapResult(String body, String action) {
  try {
    final result = XmlDocument.parse(body)
        .findAllElements('${action}Response', namespaceUri: '*')
        .firstOrNull;
    return {
      for (final arg in result?.childElements ?? const <XmlElement>[])
        arg.localName: arg.innerText.trim(),
    };
  } on XmlException {
    return const {};
  }
}

/// SOAP 错误里的 UPnP 错误码和说明，如「718 Invalid InstanceID」。
@visibleForTesting
String? soapFault(String body) {
  try {
    final error = XmlDocument.parse(body)
        .findAllElements('UPnPError', namespaceUri: '*')
        .firstOrNull;
    if (error == null) return null;
    String field(String name) =>
        error
            .findElements(name, namespaceUri: '*')
            .firstOrNull
            ?.innerText
            .trim() ??
        '';
    final fault = '${field('errorCode')} ${field('errorDescription')}'.trim();
    return fault.isEmpty ? null : fault;
  } on XmlException {
    return null;
  }
}

/// 告诉电视这是视频以及它的格式。有的电视没有它就不播。
@visibleForTesting
String didlMetadata(String url, String title) {
  final path = Uri.tryParse(url)?.path.toLowerCase() ?? '';
  final extension = path.contains('.') ? path.split('.').last : '';
  final mime = switch (extension) {
    'mp4' || 'm4v' => 'video/mp4',
    'mkv' => 'video/x-matroska',
    'ts' || 'm2ts' => 'video/mp2t',
    'avi' => 'video/x-msvideo',
    'mov' => 'video/quicktime',
    'webm' => 'video/webm',
    'flv' => 'video/x-flv',
    'wmv' => 'video/x-ms-wmv',
    _ => '*',
  };
  return '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
      'xmlns:dc="http://purl.org/dc/elements/1.1/" '
      'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
      '<item id="0" parentID="-1" restricted="1">'
      '<dc:title>${_escape(title)}</dc:title>'
      '<upnp:class>object.item.videoItem</upnp:class>'
      '<res protocolInfo="http-get:*:$mime:*">${_escape(url)}</res>'
      '</item></DIDL-Lite>';
}

/// UPnP 时间 `H+:MM:SS[.F]`。电视不支持时给的是 NOT_IMPLEMENTED，返回空。
@visibleForTesting
Duration? parseUpnpTime(String? value) {
  final match = RegExp(r'^(\d+):(\d{1,2}):(\d{1,2})(?:\.(\d+))?$')
      .firstMatch(value?.trim() ?? '');
  if (match == null) return null;
  final fraction = match.group(4);
  return Duration(
    hours: int.parse(match.group(1)!),
    minutes: int.parse(match.group(2)!),
    seconds: int.parse(match.group(3)!),
    milliseconds: fraction == null
        ? 0
        : (double.parse('0.$fraction') * 1000).round(),
  );
}

@visibleForTesting
String formatUpnpTime(Duration value) {
  String two(int n) => n.toString().padLeft(2, '0');
  final seconds = value.isNegative ? 0 : value.inSeconds;
  return '${two(seconds ~/ 3600)}:${two(seconds ~/ 60 % 60)}:${two(seconds % 60)}';
}

String _escape(String value) => value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&apos;');
