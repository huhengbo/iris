// 老系统（Android 6 等）的根证书库里没有 ISRG Root X1，Let's Encrypt 签发的站点证书
// 因此校验失败。只在 Android 上把这个公开根证书补进默认信任库；其他平台系统已带。
export 'trust_roots_stub.dart' if (dart.library.io) 'trust_roots_io.dart';
