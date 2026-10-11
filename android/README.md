# NgpCraft for Android 1.3

The native Android application is distributed as a signed universal APK through this repository's [Releases](https://github.com/Tixul/Ngpcraft_emulator/releases). Version 1.3 is prepared locally; the APK must be attached manually before it is available there.

Android 7.0 or later is required (API 24). The APK includes arm64-v8a, armeabi-v7a and x86_64. Download `NgpCraft-1.3.apk`, open it on the device and allow installation from that source when Android requests it.

Features include a ROM library, touch controls, physical gamepads, display filters, cartridge saves, save states and two-player modes. Supply your own game ROMs; a clean-room HLE BIOS is embedded.

## New package name in 1.3

The app is now `io.github.tixul.ngpcraft` (it was `com.example.ngpcraft_emulator`). The name is now fixed, so frontends can refer to it. Android treats 1.3 as a different app: it installs next to 1.2 rather than replacing it, and it does not see 1.2's saves, save states or imported ROMs. You can keep 1.2 installed to finish games saved there, and uninstall it afterwards.

## Launching from a frontend (ES-DE, Daijishō…)

Point the frontend at `io.github.tixul.ngpcraft/.MainActivity`. The game can be passed as a `content://` URI or a path, as data (with or without `android.intent.action.VIEW`) or as the RetroArch-style `ROM` extra. Quitting the game returns to the frontend.

Daijishō custom player for the Neo Geo Pocket Color platform:

```
-n io.github.tixul.ngpcraft/.MainActivity
-a android.intent.action.VIEW
-d {file.uri}
--activity-clear-task
--activity-clear-top
```

ES-DE command (with a find rule for `io.github.tixul.ngpcraft/.MainActivity`):

```
%EMULATOR_NGPCRAFT% %ACTIVITY_CLEAR_TASK% %ACTIVITY_CLEAR_TOP% %ACTION%=android.intent.action.VIEW %DATA%=%ROMSAF%
```

A plain file path (such as Daijishō's `{file.path}`) can only be read inside the ROM folder chosen in NgpCraft with **ROM folder**, because the app does not ask for access to all files. Choosing the frontend's whole `ROMs` folder there is enough. A `content://` URI does not need this.

## Version history

1.3 (versionCode 4): fixed package name, frontend launching, core synchronized with the desktop. Checked on an Android 16 x86_64 emulator with launch commands in the formats ES-DE and Daijishō use; not yet with the real frontend apps, on an ARM device or with an SD-card path.

1.2 (versionCode 3) fixed a BIOS boot loop (the post-intro handoff now matches the desktop, and first-time setup stays interactive), kept battery RAM, flash and the clock across that handoff, and added a menu that follows controller hotplug. Save states became version 2; version 1 states still load, and cartridge `.srm` saves are unchanged. 1.1 cannot join a 1.2 mirror session.

The Android Studio project is maintained separately; its sources are not included in this folder. The local APKs here are ignored by Git and are ready for manual upload to a GitHub release.
