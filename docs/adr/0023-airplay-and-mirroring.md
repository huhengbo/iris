# ADR 0023：投屏增加 AirPlay 和系统屏幕镜像

- 状态：已接受（真机待验证）；屏幕镜像入口 2026-10-08 已移除，见下
- 日期：2026-10-07
- 补充：ADR 0018

## 背景

- ADR 0018 只做了 DLNA。实际遇到小米电视搜不到：小米手机自带的投屏走的不一定是 DLNA，电视的 DLNA 接收端也可能没开。
- 乐播这类投屏应用是多协议并用（私有协议、DLNA、AirPlay、Miracast），任何一种能连上就能投。

## 决策

### 屏幕镜像入口已移除（2026-10-08）
选电视弹层里的「屏幕镜像」入口去掉了：多出一行，用户有系统自带的投屏入口，应用里没有必要再放一个。下面「屏幕镜像」一节只保留当时的取舍，对应的代码已删除。

### 三种方式并存
- **DLNA**：不变，见 ADR 0018。
- **AirPlay**：搜索和选电视的界面里和 DLNA 电视并列，名字下面标出协议。
- **屏幕镜像（Miracast 等）**：选电视的弹层里单独一项，打开系统的投屏设置（`Settings.ACTION_CAST_SETTINGS`），由系统把整个屏幕镜像出去。

### AirPlay
- 发现：mDNS 查 `_airplay._tcp.local`，取 SRV（端口）、TXT（`deviceid`、`features`）和 A 记录。`features` 里没有视频位（第 0 位）也没有 HLS 视频位（第 4 位）的是音箱，不列出。只在局域网网卡上收发，和 SSDP 一样。
- 控制：AirPlay 视频推送的 HTTP 接口，端口一般是 7000：
  - `POST /play`，`Content-Type: text/parameters`，正文 `Content-Location: <地址>`，推上去就开始放；
  - `POST /rate?value=0|1` 暂停和继续，`POST /scrub?position=秒` 拖动，`POST /stop` 停止；
  - `GET /playback-info` 每秒查一次，读 XML plist 里的 `rate`、`duration`、`position`。
- 推的地址和 DLNA 相同（ADR 0015 的 `/play/{令牌}/…`），所以进度上报、续播等和 DLNA 共用投屏页。
- 搜索用到的组播锁和 DLNA 共用，按计数持有。

### 屏幕镜像
- Android 应用拿不到 Miracast 的发送接口，所以只能打开系统设置，镜像期间手机要一直亮着，画质和续航受限。它的好处是系统自带的投屏能搜到的电视，这一项也能连。
- 镜像由系统接管，应用里没有选中电视，也不会有投屏控制页和进度上报。

## 后果

- AirPlay 走的是旧的、公开的视频推送接口。Apple TV 在新版 tvOS 上要配对，这种接收端连不上；电视里内置的 AirPlay 接收端（设置成「同一网络下的所有人」）才可能可用。
- 苹果设备放不了 mkv 等容器，电视拉流后能不能播由接收端决定，和 DLNA 一样。
- `/playback-info` 只解析 XML plist，接收端返回二进制 plist 时读不到状态，投屏页会提示电视没有开始播放。
- 没有外挂字幕支持，和 DLNA 一样。

## 不做
- AirPlay 配对和 AirPlay 2 的加密通道。
- 应用内的 Miracast 发送端。
- 乐播等厂商私有协议（需要商业 SDK 授权）。
