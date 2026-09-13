import java.util.Properties

plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

// The Python project's version is the app version.
val appVersion: String = Regex("""(?m)^version\s*=\s*"([^"]+)"""")
    .find(rootDir.resolve("../../pyproject.toml").readText())!!
    .groupValues[1]
val versionParts = appVersion.split(".").map { part -> part.takeWhile(Char::isDigit).toIntOrNull() ?: 0 }

// Release signing key; build.sh creates keystore.properties on the first build.
val keystoreFile = rootDir.resolve("keystore.properties")
val keystore = Properties().apply { if (keystoreFile.isFile) keystoreFile.inputStream().use(::load) }

android {
    namespace = "io.github.professorlayton322.spellbook"
    compileSdk = 35

    defaultConfig {
        applicationId = "io.github.professorlayton322.spellbook"
        minSdk = 24
        targetSdk = 35
        versionCode = versionParts.getOrElse(0) { 0 } * 10000 + versionParts.getOrElse(1) { 0 } * 100 + versionParts.getOrElse(2) { 0 }
        versionName = appVersion
        ndk {
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    signingConfigs {
        if (keystoreFile.isFile) {
            create("release") {
                storeFile = rootDir.resolve(keystore.getProperty("storeFile"))
                storePassword = keystore.getProperty("storePassword")
                keyAlias = keystore.getProperty("keyAlias")
                keyPassword = keystore.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        checkReleaseBuilds = false
    }
}

chaquopy {
    defaultConfig {
        version = "3.13"
        providers.gradleProperty("spellbook.buildPython").orNull?.let { buildPython(it) }
        pip {
            install("-r", "requirements.txt")
        }
        // Templates, static files, and CA certificates are opened as files on disk.
        extractPackages("spellbook_builder", "certifi", "reportlab")
    }
    sourceSets {
        getByName("main") {
            srcDir("../../../src")
        }
    }
}
