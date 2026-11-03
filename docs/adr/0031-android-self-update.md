# ADR 0031：Android 应用内升级走 GitHub Releases 清单

- 状态：已接受（2026-11-03）
- 补充：ADR 0010

## 背景

Android / Android TV 的 APK 现在只能到 Releases 页手动下载，已经安装的用户不知道有新版本，每次都要回来找。要做应用内检查更新和安装。约束有几条：

- APK 按 ABI 拆分（`arm64-v8a`、`armeabi-v7a`、`x86_64`），客户端得选对包。
- 要兼容 Android 6（API 23）的电视盒子，不能为了升级功能抬高 minSdk。
- 仓库公开，发布和托管尽量不增加服务器与运维成本，后端不参与。
- 国内访问 GitHub 不稳定。

## 决策

### 清单放在 Releases

- 每次发布，流水线用 `scripts/version.py manifest` 从构建产物生成 `update.json`，和 APK 一起作为 Release asset 上传。客户端只请求固定地址 `https://github.com/huhengbo/iris/releases/latest/download/update.json`，由 GitHub 的 `latest` 重定向到最新正式 Release；不走 GitHub API，避免匿名 60 次/小时的限流，也不需要 token。
- 清单格式（字段名是客户端契约）：

```json
{
  "version": "0.3.0",
  "version_code": 300,
  "notes": "CHANGELOG 中该版本章节的 Markdown 正文（不含「获取」段落）",
  "min_version": null,
  "apks": {
    "arm64-v8a": {"url": "https://github.com/huhengbo/iris/releases/download/v0.3.0/app-arm64-v8a-release.apk", "size": 12345, "sha256": "小写十六进制"},
    "armeabi-v7a": {"url": "…", "size": 12345, "sha256": "…"},
    "x86_64": {"url": "…", "size": 12345, "sha256": "…"}
  }
}
```

- `version` 与根目录 `VERSION`、tag 一致；`version_code` 用 `x*10000 + y*100 + z`，只供参考——`--split-per-abi` 时 Flutter 会在 build-number 基础上再加 1000×ABI 序号，数值不再可比。客户端只比较 `version` 的 semver。
- `notes` 取 CHANGELOG 对应版本的正文，去掉发布说明自动追加的「获取」段落。
- `apks` 的 key 是 ABI，从 `app-<abi>-release.apk` 文件名解析，目录里有几个写几个；每项带下载地址、字节数和 sha256。
- `min_version` 预留给强制更新：低于它的版本不允许跳过。第一期保留字段、CI 一律写 `null`，客户端把 `null` 当作不强制。

### 下载与安装

- 客户端按设备的 ABI（`Build.SUPPORTED_ABIS` 的顺序）在 `apks` 里选第一个存在的包；都匹配不上时提示去 Releases 手动下载，不装其他架构的包。
- 下载完校验 sha256（在原生层计算，不占 UI 线程；同版本已下载且校验通过时直接复用），再通过 FileProvider 把文件暴露成 `content://` URI，用系统安装器（`ACTION_VIEW` + `application/vnd.android.package-archive`）安装。需要 `REQUEST_INSTALL_PACKAGES` 权限，并引导用户在系统设置里允许本应用安装未知来源应用。
- 不引入第三方 Flutter 升级/安装插件（如 `ota_update`、`flutter_app_update`、`install_plugin`）：它们大多要求更高的 minSdk、额外权限或 Play 服务，有的还不支持遥控器操作；FileProvider 加系统安装器是 Android 自带能力，能保住 minSdk 23，也少一个依赖。
- 只允许升级到更高的 semver：相同或更低版本不提示，不提供降级。签名必须一致：CI 没配置签名密钥时产物是 debug 签名，和 release 签名的版本不能互相覆盖，这一点和手动下载相同。
- 启动后自动检查，每 24 小时最多一次，可在「设置 → 本机 → 常规」关闭；用户选「跳过此版本」后该版本不再自动弹窗，「关于 → 检查更新」手动检查时仍会提示。
- 检查更新失败（断网、GitHub 不可达、清单解析失败）只记录，不影响正常使用，下次再查；不因为升级检查阻塞启动。

### 国内网络

- 「设置 → 本机 → 常规」提供可选的下载加速前缀，留空表示直连 GitHub。请求清单和 APK 时把原始 GitHub 地址拼在前缀后面。
- 前缀须以 http:// 或 https:// 开头（局域网里的镜像可能是 http），由用户自己填写并承担信任；清单和 APK 都经过前缀，sha256 只防传输损坏、防不了恶意镜像；真正兜底的是系统安装器的签名校验——签名不同的包不能覆盖已装的版本；服务端不下发、不代理，避免每个自建部署都要维护 APK 和带宽。

## 备选方案

- **GitHub API**（`/repos/.../releases/latest`）：能拿到结构化数据和发布时间，但匿名调用按 IP 限流 60 次/小时，家里多人共用一个出口很容易打满，还要处理 token。用 Release 里的静态 `update.json` 没有这个问题，代价是清单要由流水线生成、字段自己维护。
- **服务器中转**：客户端从自己的光圈服务器拿清单和 APK。好处是走用户已经能访问的地址，还能按用户灰度。但这要求每个部署升级后端，占用服务器带宽和磁盘，自建用户不一定愿意镜像几十 MB 的 APK；而且服务器和 GitHub 发布没有关系，反而多一处要同步的状态。留作以后需要灰度或统计时再做。
- **Google Play / F-Droid**：本项目不在应用商店分发，电视盒子也多半没有 Play 服务。
- **APK 内嵌第三方升级 SDK**：同样要联网、要权限，还会带来额外的隐私和体积成本，不如一个静态 JSON。

## 取舍

- GitHub 在国内的可达性靠用户配置加速前缀解决，不是开箱即用；前缀失效时要能改回直连。
- `latest/download/update.json` 依赖「最新 Release 一定是正式版」：流水线只在 tag 推送、APK 构建完成后才创建 Release，预发布版本不要用 `latest`。
- 全量 APK（每个架构几十 MB），不做增量升级；对电视盒子来说可以接受，实现也简单。
- 清单和 APK 都由 GitHub 的 CDN 提供，发布后短时间内可能读到旧缓存；客户端以拿到的版本号为准，稍后重试即可。
- `min_version` 现在不启用，等确有必须强制升级的兼容性问题（例如服务端接口不兼容）再在流水线里填值。

## 结果

- 发布流水线多生成并上传一个 `update.json`，后端和部署方式不变。
- 客户端可以定期检查更新、按架构下载、校验后调起系统安装器；Android 6 仍可用。
- 强制更新和服务器中转留了位置（`min_version`、加速前缀），以后按需补。
