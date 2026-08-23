plugins { id("com.android.application") }

android {
    namespace = "com.aaron.jarvisvoice"
    compileSdk = 36
    defaultConfig {
        applicationId = "com.aaron.jarvisvoice"
        minSdk = 30
        targetSdk = 36
        versionCode = 190227
        versionName = "19.0.0-alpha19-developer-v1.6.1"
    }
    buildTypes {
        release {
            val signingStore = providers.environmentVariable("JARVIS_SIGNING_STORE_FILE")
            if (signingStore.isPresent) {
                signingConfig = signingConfigs.create("jarvisWearRelease") {
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
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    testOptions { unitTests.isReturnDefaultValues = true }
}

dependencies {
    implementation(project(":wearprotocol"))
    implementation("com.google.android.gms:play-services-wearable:20.0.1")
    implementation("androidx.activity:activity:1.13.0")
    implementation("androidx.core:core:1.18.0")
    implementation("androidx.wear:wear:1.4.0")
    implementation("androidx.wear:wear-input:1.2.0")
    implementation("androidx.wear.tiles:tiles:1.6.2")
    implementation("androidx.wear.protolayout:protolayout:1.4.2")
    implementation("androidx.wear.protolayout:protolayout-material:1.4.2")
    implementation("com.google.guava:guava:33.7.1-android")
    testImplementation("junit:junit:4.13.2")
}
