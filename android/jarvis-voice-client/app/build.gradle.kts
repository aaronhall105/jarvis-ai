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
        versionCode = 190208
        versionName = "19.0.0-alpha19-wear-v1.9"

        testInstrumentationRunner = "android.test.InstrumentationTestRunner"

        ndk {
            abiFilters.add("arm64-v8a")
        }
    }

    buildTypes {
        release {
            val signingStore = providers.environmentVariable("JARVIS_SIGNING_STORE_FILE")
            if (signingStore.isPresent) {
                signingConfig = signingConfigs.create("jarvisRelease") {
                    storeFile = file(signingStore.get())
                    storePassword = providers.environmentVariable("JARVIS_SIGNING_STORE_PASSWORD").get()
                    keyAlias = providers.environmentVariable("JARVIS_SIGNING_KEY_ALIAS").get()
                    keyPassword = providers.environmentVariable("JARVIS_SIGNING_KEY_PASSWORD").get()
                    enableV1Signing = true
                    enableV2Signing = true
                    enableV3Signing = true
                    enableV4Signing = true
                }
            }
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
    implementation(project(":wearprotocol"))
    implementation("com.google.android.gms:play-services-wearable:20.0.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("androidx.work:work-runtime:2.11.0")
    implementation("androidx.activity:activity:1.12.3")
    implementation(files("libs/sherpa-onnx-1.13.2.aar"))

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")
}
