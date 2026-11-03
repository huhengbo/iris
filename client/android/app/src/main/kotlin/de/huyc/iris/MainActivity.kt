package de.huyc.iris

import android.app.UiModeManager
import android.content.Context
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.media.AudioManager
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.WindowManager
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import kotlin.math.roundToInt

class MainActivity : FlutterActivity() {
    private var multicastLock: WifiManager.MulticastLock? = null
    private var updateChannel: UpdateChannel? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // 系统启动屏默认会淡出，盖住开机动画的开头；Flutter 第一帧在同一位置画着
        // 同一枚光圈（lib/app/launch_splash.dart），直接移除就接得上
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            splashScreen.setOnExitAnimationListener { it.remove() }
        }
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        // Flutter 端据此进入 TV 模式（lib/app/tv.dart）；投屏搜索电视时拿组播锁（lib/features/play/dlna.dart）；
        // 播放器竖滑调亮度和音量（lib/features/play/player_gestures.dart）
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "iris/device")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "isTelevision" -> result.success(isTelevision())
                    "acquireMulticastLock" -> {
                        acquireMulticastLock()
                        result.success(null)
                    }
                    "releaseMulticastLock" -> {
                        releaseMulticastLock()
                        result.success(null)
                    }
                    "getBrightness" -> result.success(brightness())
                    "setBrightness" -> {
                        // 0 在部分机型上会直接黑屏，留一点底
                        val value = (call.arguments as Number).toFloat().coerceIn(0.01f, 1f)
                        setWindowBrightness(value)
                        result.success(null)
                    }
                    "resetBrightness" -> {
                        setWindowBrightness(WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE)
                        result.success(null)
                    }
                    "getVolume" -> result.success(volume())
                    "setVolume" -> {
                        setVolume((call.arguments as Number).toDouble())
                        result.success(volume())
                    }
                    else -> result.notImplemented()
                }
            }
        // 应用内升级：取架构、校验和调起安装器（lib/features/update/）
        updateChannel = UpdateChannel(this, flutterEngine.dartExecutor.binaryMessenger)
    }

    override fun onDestroy() {
        releaseMulticastLock()
        updateChannel?.dispose()
        updateChannel = null
        super.onDestroy()
    }

    private fun acquireMulticastLock() {
        if (multicastLock?.isHeld == true) return
        val wifi = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
        multicastLock = wifi.createMulticastLock("iris-cast").apply {
            setReferenceCounted(false)
            acquire()
        }
    }

    private fun releaseMulticastLock() {
        multicastLock?.takeIf { it.isHeld }?.release()
        multicastLock = null
    }

    /** 本窗口的亮度；跟随系统时读系统亮度。多数机型满档是 255，个别更高，只当起点用 */
    private fun brightness(): Double {
        val own = window.attributes.screenBrightness
        if (own >= 0) return own.toDouble()
        val system = Settings.System.getInt(contentResolver, Settings.System.SCREEN_BRIGHTNESS, 128)
        return (system / 255.0).coerceIn(0.0, 1.0)
    }

    /** 只改本窗口，不动系统亮度，也就不需要写系统设置的权限 */
    private fun setWindowBrightness(value: Float) {
        window.attributes = window.attributes.apply { screenBrightness = value }
    }

    private val audio by lazy { getSystemService(Context.AUDIO_SERVICE) as AudioManager }

    private fun volume(): Double {
        val max = audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
        return if (max > 0) audio.getStreamVolume(AudioManager.STREAM_MUSIC) / max.toDouble() else 0.0
    }

    /** 媒体音量按档位取整；flags 为 0，不弹系统音量条，播放器自己显示 */
    private fun setVolume(value: Double) {
        val max = audio.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
        val index = (value * max).roundToInt().coerceIn(0, max)
        audio.setStreamVolume(AudioManager.STREAM_MUSIC, index, 0)
    }

    private fun isTelevision(): Boolean {
        val uiMode = getSystemService(Context.UI_MODE_SERVICE) as UiModeManager
        return uiMode.currentModeType == Configuration.UI_MODE_TYPE_TELEVISION ||
            packageManager.hasSystemFeature(PackageManager.FEATURE_LEANBACK)
    }
}
