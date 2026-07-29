plugins {
    id("com.android.application")
}

android {
    namespace = "com.aaron.jarvisvoice"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.aaron.jarvisvoice"
        minSdk = 31
        targetSdk = 36
        versionCode = 18302
        versionName = "18.3.2"

        testInstrumentationRunner = "android.test.InstrumentationTestRunner"

        ndk {
            abiFilters.add("arm64-v8a")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

dependencies {
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation(files("libs/sherpa-onnx-1.13.2.aar"))

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
