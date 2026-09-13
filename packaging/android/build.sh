#!/usr/bin/env bash
# Build the signed Android APK into dist/.
#
# Needs JDK 17 (JAVA_HOME), the Android SDK (ANDROID_HOME), and Python 3.13 as
# python3.13 on PATH (or pass -Pspellbook.buildPython=/path/to/python3.13).
# Extra arguments are passed to Gradle.
set -euo pipefail
cd "$(dirname "$0")"

: "${ANDROID_HOME:?Set ANDROID_HOME to the Android SDK directory}"
keytool="${JAVA_HOME:+$JAVA_HOME/bin/}keytool"

if [ ! -f keystore.properties ]; then
  # Every update must be signed with this same key, or Android refuses to install it.
  password=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
  "$keytool" -genkeypair -keystore release.keystore -alias spellbook -keyalg RSA -keysize 4096 -validity 10000 \
    -storepass "$password" -keypass "$password" -dname "CN=DnD 3.5 Spellbook"
  printf 'storeFile=release.keystore\nstorePassword=%s\nkeyAlias=spellbook\nkeyPassword=%s\n' "$password" "$password" > keystore.properties
  echo "Created release.keystore and keystore.properties. Back both up: later versions must be signed with them."
fi

./gradlew --no-daemon assembleRelease "$@"

version=$(sed -n 's/^version = "\(.*\)"/\1/p' ../../pyproject.toml)
mkdir -p ../../dist
cp app/build/outputs/apk/release/app-release.apk "../../dist/DnD35Spellbook-$version.apk"
echo "Built dist/DnD35Spellbook-$version.apk"
