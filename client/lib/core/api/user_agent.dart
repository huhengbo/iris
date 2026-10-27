/// 从 User-Agent 认出浏览器和系统，如「Chrome · macOS」。
({String? name, String? platform}) describeUserAgent(String agent) {
  // 顺序有讲究：Edge 和 Chrome 的 UA 里都有 Safari，Edge 里还有 Chrome
  final browser = agent.contains('Edg/')
      ? 'Edge'
      : agent.contains('Firefox/')
      ? 'Firefox'
      : agent.contains('Chrome/') || agent.contains('CriOS/')
      ? 'Chrome'
      : agent.contains('Safari/')
      ? 'Safari'
      : '浏览器';
  final system = agent.contains('Android')
      ? 'Android'
      : agent.contains('iPhone') || agent.contains('iPad')
      ? 'iOS'
      : agent.contains('Mac OS X')
      ? 'macOS'
      : agent.contains('Windows')
      ? 'Windows'
      : agent.contains('Linux')
      ? 'Linux'
      : null;
  return (
    name: system == null ? browser : '$browser · $system',
    platform: 'Web',
  );
}
