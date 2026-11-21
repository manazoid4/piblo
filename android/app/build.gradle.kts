plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.google.devtools.ksp")
}

android {
    namespace = "uk.mazworks.piblo"
    compileSdk = 36

    defaultConfig {
        applicationId = "uk.mazworks.piblo"
        minSdk = 26
        targetSdk = 36
        versionCode = 3
        versionName = "0.2.0-beta"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    // CI signs debug APKs with one stable key (repo secret) so updates install in place; local builds keep the default debug key.
    val stableKeystore = System.getenv("PIBLO_KEYSTORE")?.takeIf { it.isNotBlank() }
    signingConfigs {
        if (stableKeystore != null) create("piblo") {
            storeFile = file(stableKeystore)
            storePassword = System.getenv("PIBLO_KEYSTORE_PASSWORD")
            keyAlias = "piblo"
            keyPassword = System.getenv("PIBLO_KEYSTORE_PASSWORD")
        }
    }

    buildTypes {
        debug { signingConfigs.findByName("piblo")?.let { signingConfig = it } }
        release {
            // Same key as debug (beta key in CI, default debug key locally) so a release APK installs over the beta, never an unsigned one.
            signingConfig = signingConfigs.findByName("piblo") ?: signingConfigs.getByName("debug")
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlin { compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) } }
    buildFeatures { compose = true; buildConfig = true }
    testOptions { unitTests.isReturnDefaultValues = true }
    packaging { resources.excludes += "/META-INF/{AL2.0,LGPL2.1}" }
}

ksp { arg("room.schemaLocation", "$projectDir/schemas") }

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2025.07.00")
    implementation(composeBom)
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.activity:activity-compose:1.10.1")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.9.2")
    implementation("androidx.room:room-runtime:2.7.2")
    implementation("androidx.room:room-ktx:2.7.2")
    ksp("androidx.room:room-compiler:2.7.2")
    implementation("androidx.datastore:datastore-preferences:1.1.7")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")
    // Bundled on-device OCR: no paid vision API and no runtime model download.
    implementation("com.google.mlkit:text-recognition:16.0.1")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20250517")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.2")
}
