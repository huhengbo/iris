package de.huyc.iris

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.FileProvider
import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.MethodChannel
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.Executors

/**
 * 应用内升级的原生部分（lib/features/update/update_platform.dart）：给下载用的文件路径、
 * 校验 sha256、调起系统安装器。自己实现而不用第三方安装插件，那些插件的 minSdk 多在 24 以上，
 * 会破坏 Android 6 电视盒子的兼容（见 AndroidManifest 的 overrideLibrary 注释）。
 */
class UpdateChannel(private val activity: Activity, messenger: BinaryMessenger) {
    private val channel = MethodChannel(messenger, "iris/update")

    // 算几十 MB 的 sha256 不能占着主线程
    private val io = Executors.newSingleThreadExecutor()

    /** 下载目录只放安装包，FileProvider 也只暴露这一个目录（res/xml/update_paths.xml） */
    private val updatesDir get() = File(activity.cacheDir, "updates")

    init {
        channel.setMethodCallHandler { call, result ->
            when (call.method) {
                "supportedAbis" -> result.success(Build.SUPPORTED_ABIS.toList())
                "prepareFile" -> result.success(prepareFile(call.argument<String>("name")!!))
                "sha256" -> {
                    val file = File(call.argument<String>("path")!!)
                    io.execute {
                        val digest = if (file.isFile) sha256(file) else null
                        activity.runOnUiThread { result.success(digest) }
                    }
                }
                "clear" -> {
                    updatesDir.listFiles()?.forEach { it.delete() }
                    result.success(null)
                }
                "install" -> install(File(call.argument<String>("path")!!), result)
                else -> result.notImplemented()
            }
        }
    }

    fun dispose() {
        channel.setMethodCallHandler(null)
        io.shutdown()
    }

    /** 返回安装包的保存路径，并删掉目录里其他版本留下的包；同名文件留着，校验通过就不必重新下载 */
    private fun prepareFile(name: String): String {
        val dir = updatesDir.apply { mkdirs() }
        dir.listFiles()?.filter { it.name != name }?.forEach { it.delete() }
        return File(dir, name).absolutePath
    }

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(1 shl 16)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                digest.update(buffer, 0, read)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }

    /**
     * Android 8 起每个应用要单独允许“安装未知应用”：没有权限时打开设置页并返回 permission，
     * 用户回来后再点一次安装。部分电视盒子没有这个设置页，返回 no_settings 错误。
     */
    private fun install(file: File, result: MethodChannel.Result) {
        if (!file.isFile) {
            result.error("missing", "安装包不存在", null)
            return
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            !activity.packageManager.canRequestPackageInstalls()
        ) {
            try {
                activity.startActivity(
                    Intent(
                        Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                        Uri.parse("package:${activity.packageName}"),
                    ),
                )
                result.success("permission")
            } catch (_: ActivityNotFoundException) {
                result.error("no_settings", "系统没有“安装未知应用”的设置页", null)
            }
            return
        }
        val uri = FileProvider.getUriForFile(activity, "${activity.packageName}.updates", file)
        val intent = Intent(Intent.ACTION_VIEW)
            .setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        try {
            activity.startActivity(intent)
            result.success("installing")
        } catch (_: ActivityNotFoundException) {
            result.error("no_installer", "找不到系统安装器", null)
        }
    }
}
