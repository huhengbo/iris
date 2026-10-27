/// 既不是 IO 也不是 Web 的平台不上报设备名和平台。
({String? name, String? platform}) deviceLabel() =>
    (name: null, platform: null);
