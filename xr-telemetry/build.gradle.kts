plugins {
    alias(libs.plugins.android.library)
}

android {
    namespace = "com.example.xrtelemetry"
    compileSdk = 36

    defaultConfig {
        minSdk = 24
        consumerProguardFiles("consumer-rules.pro")
    }
}

java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(17)
    }
}

dependencies {
    implementation(libs.androidx.arcore)
    implementation(libs.androidx.compose)
    implementation(libs.androidx.scenecore)
}

tasks.register<Zip>("bundleTelemetryPackage") {
    dependsOn("assembleRelease")

    archiveBaseName.set("xr-telemetry-package")
    destinationDirectory.set(layout.buildDirectory.dir("distributions"))

    from(layout.projectDirectory) {
        include("build.gradle.kts")
        include("consumer-rules.pro")
        include("README.md")
        include("src/**")
    }

    from(layout.buildDirectory.dir("outputs/aar")) {
        include("xr-telemetry-release.aar")
        into("aar")
    }
}
