plugins {
    id("com.android.application")
}

android {
    namespace = "app.edgespeak"
    compileSdk = 36

    defaultConfig {
        applicationId = "app.edgespeak"
        minSdk = 30 // 辅助功能截图（悬浮取词的 OCR）需要 Android 11
        targetSdk = 36
        versionCode = 1
        versionName = "1.0.0"
        // OCR 的原生库每种 CPU 一份：手机基本都是 arm64，x86_64 留给电脑上的模拟器
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("debug") // 自己安装用；上架前换成正式签名
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    packaging {
        resources.excludes += listOf("META-INF/DEPENDENCIES", "META-INF/LICENSE*", "META-INF/NOTICE*")
    }
}

dependencies {
    implementation("com.worksap.nlp:sudachi:0.7.5")
    implementation("com.google.mlkit:text-recognition-japanese:16.0.1")
}
