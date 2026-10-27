import java.util.Properties

// 兼容 Android 6 电视盒子（API 23）。不能直接写 minSdk = 23：Flutter 的工程迁移会把低于
// flutter.minSdkVersion 的字面量改回默认值，经变量间接引用则不会被改。
val legacyTvMinSdk = 23

plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

// release 签名：密钥在仓库根目录的 secrets/android/（不进版本库）。没有时退回 debug 签名，
// 只影响能否覆盖安装，不影响功能。storeFile 相对 key.properties 所在目录。
val releaseKeyFile = rootProject.file("../../secrets/android/key.properties")
val releaseKey = Properties().apply {
    if (releaseKeyFile.exists()) releaseKeyFile.reader(Charsets.UTF_8).use { load(it) }
}

android {
    namespace = "de.huyc.iris"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        applicationId = "de.huyc.iris"
        minSdk = legacyTvMinSdk
        targetSdk = flutter.targetSdkVersion
        // Uses the version code from pubspec.yaml. When using split APKs, 1000 * ABI_VERSION
        // is added automatically by Flutter. (https://developer.android.com/studio/build/configure-apk-splits#configure-APK-versions)
        // You can force using the value of versionCode by specifying the `-P force-version-code-ignoring-abi=true`
        // flag during build.
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    signingConfigs {
        if (releaseKey.isNotEmpty()) {
            create("release") {
                storeFile = releaseKeyFile.parentFile.resolve(releaseKey.getProperty("storeFile"))
                storePassword = releaseKey.getProperty("storePassword")
                keyAlias = releaseKey.getProperty("keyAlias")
                keyPassword = releaseKey.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}
