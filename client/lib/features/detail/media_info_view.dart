import '../library/browse_api.dart';

/// 如「4K · HEVC · 杜比视界」
String videoSummary(MediaInfo info) => [
  ?_resolution(info.width, info.height),
  if (info.videoCodec case final codec?) _codecs[codec] ?? codec.toUpperCase(),
  ?switch (info.hdr) {
    'dolby_vision' => '杜比视界',
    'hdr10' => 'HDR10',
    'hlg' => 'HLG',
    _ => null,
  },
].join(' · ');

/// 如「英语 TrueHD 7.1（默认）」，片源给音轨起了名字时附在后面
String audioSummary(MediaAudio track) {
  final parts = [
    languageName(track.language) ?? '未知语言',
    if (track.codec case final codec?) _codecs[codec] ?? codec.toUpperCase(),
    ?switch (track.channels) {
      null => null,
      1 => '单声道',
      2 => '2.0',
      6 => '5.1',
      8 => '7.1',
      final n => '$n 声道',
    },
  ];
  return _withNotes(parts.join(' '), [?track.title, if (track.isDefault) '默认']);
}

/// 如「中文（简体 · PGS）」
String embeddedSubtitleSummary(MediaEmbeddedSubtitle track) =>
    _withNotes(languageName(track.language) ?? '未知语言', [
      ?track.title,
      if (track.codec case final codec?) _codecs[codec] ?? codec.toUpperCase(),
      if (track.forced) '强制',
    ]);

String _withNotes(String text, List<String> notes) =>
    notes.isEmpty ? text : '$text（${notes.join(' · ')}）';

/// 宽银幕片源高度不够标准，按宽度也算一档，如 3840×1608 仍是 4K
String? _resolution(int? width, int? height) {
  final w = width ?? 0;
  final h = height ?? 0;
  if (w >= 3800 || h >= 2100) return '4K';
  if (w >= 1900 || h >= 1060) return '1080p';
  if (w >= 1260 || h >= 700) return '720p';
  return h > 0 ? '${h}p' : null;
}

/// 片源里常见的语言代码（ISO 639-2 或 639-1）。认不出的原样显示，und 当作不知道
String? languageName(String? code) {
  if (code == null || code.isEmpty) return null;
  final key = code.toLowerCase();
  if (key == 'und') return null;
  return _languages[key] ?? _languages[key.split(RegExp('[-_]')).first] ?? code;
}

const _languages = {
  'chi': '中文',
  'zho': '中文',
  'zh': '中文',
  'yue': '粤语',
  'eng': '英语',
  'en': '英语',
  'jpn': '日语',
  'ja': '日语',
  'kor': '韩语',
  'ko': '韩语',
  'fre': '法语',
  'fra': '法语',
  'fr': '法语',
  'ger': '德语',
  'deu': '德语',
  'de': '德语',
  'spa': '西班牙语',
  'es': '西班牙语',
  'ita': '意大利语',
  'it': '意大利语',
  'rus': '俄语',
  'ru': '俄语',
  'por': '葡萄牙语',
  'pt': '葡萄牙语',
  'tha': '泰语',
  'th': '泰语',
};

/// mpv 的编码名到常见叫法，没列出的转大写
const _codecs = {
  'h264': 'H.264',
  'hevc': 'HEVC',
  'mpeg2video': 'MPEG-2',
  'eac3': 'E-AC3',
  'truehd': 'TrueHD',
  'opus': 'Opus',
  'vorbis': 'Vorbis',
  'subrip': 'SRT',
  'hdmv_pgs_subtitle': 'PGS',
  'dvd_subtitle': 'VobSub',
  'mov_text': 'TX3G',
  'webvtt': 'WebVTT',
};
