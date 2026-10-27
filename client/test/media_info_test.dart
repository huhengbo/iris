import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:iris/features/detail/media_info_view.dart';
import 'package:iris/features/library/browse_api.dart';
import 'package:iris/features/play/play_api.dart';

/// mpv 的 track-list 原样：内封轨之外还有封面图和本系统挂上去的外挂字幕
final _trackList = jsonEncode([
  {
    'id': 1,
    'type': 'video',
    'codec': 'hevc',
    'demux-w': 3840,
    'demux-h': 1608,
    'selected': true,
    'external': false,
  },
  {'id': 2, 'type': 'video', 'codec': 'mjpeg', 'image': true, 'albumart': true},
  {
    'id': 1,
    'type': 'audio',
    'lang': 'chi',
    'title': '国语',
    'codec': 'eac3',
    'demux-channel-count': 6,
    'default': true,
    'external': false,
  },
  {
    'id': 2,
    'type': 'audio',
    'lang': 'eng',
    'codec': 'truehd',
    'demux-channel-count': 8,
  },
  {
    'id': 1,
    'type': 'sub',
    'lang': 'chi',
    'title': '简体',
    'codec': 'subrip',
    'default': true,
  },
  {
    'id': 2,
    'type': 'sub',
    'lang': 'eng',
    'codec': 'hdmv_pgs_subtitle',
    'forced': true,
    'title': 'x' * 300,
  },
  {'id': 3, 'type': 'sub', 'lang': 'chi', 'codec': 'ass', 'external': true},
]);

void main() {
  group('mediaInfoFromTrackList', () {
    test(
      'keeps embedded tracks and skips cover art and external subtitles',
      () {
        final body = mediaInfoFromTrackList(
          _trackList,
          duration: const Duration(minutes: 125, milliseconds: 400),
          gamma: 'pq',
        );
        expect(body['duration'], 7500);
        expect(body['width'], 3840);
        expect(body['height'], 1608);
        expect(body['video_codec'], 'hevc');
        expect(body['hdr'], 'hdr10');
        expect(body['audio'], [
          {
            'language': 'chi',
            'title': '国语',
            'codec': 'eac3',
            'channels': 6,
            'default': true,
          },
          {
            'language': 'eng',
            'title': null,
            'codec': 'truehd',
            'channels': 8,
            'default': false,
          },
        ]);
        final subtitles = body['subtitles']! as List<Map<String, Object?>>;
        expect(subtitles, hasLength(2));
        expect(subtitles.first, {
          'language': 'chi',
          'title': '简体',
          'codec': 'subrip',
          'forced': false,
          'default': true,
        });
        // 过长的标题截断，不让服务端拒掉整次上报
        expect(subtitles.last['title'], 'x' * 200);
        expect(subtitles.last['forced'], true);
      },
    );

    test('dolby vision wins over gamma; falls back to decoder size', () {
      final body = mediaInfoFromTrackList(
        jsonEncode([
          {'type': 'video', 'codec': 'hevc', 'dolby-vision-profile': 8},
        ]),
        duration: Duration.zero,
        gamma: 'pq',
        width: 1920,
        height: 1080,
      );
      expect(body['hdr'], 'dolby_vision');
      expect(body['width'], 1920);
      expect(body['height'], 1080);
      expect(body['duration'], isNull);
      expect(body['audio'], isEmpty);
    });

    test('SDR and over-long codes are left empty', () {
      final body = mediaInfoFromTrackList(
        jsonEncode([
          {'type': 'video', 'codec': 'h264'},
          {'type': 'audio', 'lang': 'x' * 17, 'demux-channel-count': 0},
        ]),
        duration: const Duration(minutes: 1),
        gamma: 'bt.1886',
      );
      expect(body['hdr'], isNull);
      expect(body['audio'], [
        {
          'language': null,
          'title': null,
          'codec': null,
          'channels': null,
          'default': false,
        },
      ]);
    });
  });

  group('summaries', () {
    test('video line uses common names', () {
      expect(
        videoSummary(
          const MediaInfo(
            width: 3840,
            height: 1608,
            videoCodec: 'hevc',
            hdr: 'dolby_vision',
          ),
        ),
        '4K · HEVC · 杜比视界',
      );
      expect(
        videoSummary(
          const MediaInfo(width: 1280, height: 534, videoCodec: 'h264'),
        ),
        '720p · H.264',
      );
      expect(videoSummary(const MediaInfo()), '');
    });

    test('audio and subtitle lines name the language', () {
      expect(
        audioSummary(
          const MediaAudio(
            language: 'chi',
            title: '国语',
            codec: 'eac3',
            channels: 6,
            isDefault: true,
          ),
        ),
        '中文 E-AC3 5.1（国语 · 默认）',
      );
      expect(
        audioSummary(const MediaAudio(language: 'und', codec: 'aac')),
        '未知语言 AAC',
      );
      expect(
        embeddedSubtitleSummary(
          const MediaEmbeddedSubtitle(
            language: 'eng',
            codec: 'hdmv_pgs_subtitle',
            forced: true,
          ),
        ),
        '英语（PGS · 强制）',
      );
      expect(languageName('zh-Hans'), '中文');
      expect(languageName('swe'), 'swe');
    });
  });
}
