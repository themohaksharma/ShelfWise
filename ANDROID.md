# ShelfWise Android demo

The Capacitor Android app loads the existing ShelfWise web application from your Mac over the local Wi-Fi. It does not contain a copy of the SQLite backend and is not an offline/standalone app.

## Run the Mac backend for LAN access

On the Mac, from `/Users/mohak/Documents/ShelfWise`:

```bash
python3 server.py
```

The server listens on port `8765` and binds to `0.0.0.0` for this LAN demo. Do not configure router port forwarding. Only connect the phone and Mac on a trusted Wi-Fi network. macOS may ask whether Python can accept incoming connections; allow it for the local network.

Find the Mac Wi-Fi address:

```bash
ipconfig getifaddr en0
```

In ShelfWise on Android, enter `http://<that-address>:8765` (replace the placeholder with the actual address). The app checks `/healthz`, saves the address on the phone, and opens `/app?demo=1`. Do not enter `localhost`: on the phone it refers to the phone itself. If the Mac address changes, update it from the app's connection screen.

## Build a debug APK

Requires Node 20+, Java 21, Android SDK Platform 35, and Android Build Tools 35.0.0 with Android SDK licenses accepted on the build machine.

```bash
npm ci
npx cap sync android
cd android
./gradlew assembleDebug
```

APK output: `android/app/build/outputs/apk/debug/app-debug.apk`.

Install by copying the APK to the phone and opening it, then approve Android's “Install unknown apps” prompt for the file manager/browser used. Alternatively, connect with USB debugging enabled and use:

```bash
adb install -r android/app/build/outputs/apk/debug/app-debug.apk
```

GitHub Actions also builds the debug APK and stores it as a 30-day workflow artifact when Android-related changes are pushed or the workflow is manually run. Download the `ShelfWise-debug-apk` artifact from the workflow run.

## Camera and photos

The Android manifest declares camera and image-reading permissions. Gallery selection uses the existing browser file input in the WebView. Browser barcode capture depends on the WebView camera implementation and the LAN HTTP origin; test it on the target phone before a demo. Manual barcode/SKU entry remains available. The LAN demo must not be exposed through public router forwarding.

## Future production build

Set the saved backend URL to an HTTPS ShelfWise host. A production release needs an HTTPS backend and a signed Android release key. Do not commit the signing key or passwords. Configure signing values in local Gradle properties or CI secrets, then generate with `cd android && ./gradlew bundleRelease` (AAB) or `./gradlew assembleRelease` (APK). The debug APK is not signed for Play Store distribution.
