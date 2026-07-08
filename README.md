# Hello Android XR
<img src="readme/hello_android_xr.gif" alt="Hello Android XR app" width="824" />

This repository contains an Android Studio project that provides a straightforward example of the
basic functionality afforded to Android apps in Android XR, including 3D object rendering, material
and texture overrides, animation, and changing object position.

For more information, please [read the documentation](https://developer.android.com/develop/xr).

# Features

In the sample you can see an implementation of:

- Spatial Panels
- Orbiters
- Environments
- 3D object rendering (Bugdroid model)
- Material and texture overrides
- Animation of the 3D object
- Changing position of the 3D object
- XR telemetry capture for clicks, model transforms, panel moves/resizes, and hand snapshots
- and more

# Telemetry

This repo now includes a reusable Android library module at `xr-telemetry/`.

- Build a distributable package with `.\gradlew.bat :xr-telemetry:bundleTelemetryPackage`
- Import the module into another Android Studio project as source or by using the generated AAR
- Exported session JSON can be analyzed with `hci_for_glasses/analyze_sessions.py`
- `analyze_sessions.py` now supports an AI-assisted pass that reads messy telemetry like a UX researcher when `OPENAI_API_KEY` is configured
- Specialization lenses now ship in `hci_for_glasses/lenses/` and can be toggled before a run or live during a session
- The watcher/dashboard flow now produces a latest-session JSON alias plus an all-sessions HTML review dashboard

See [xr-telemetry/README.md](xr-telemetry/README.md) for the packaging and analysis workflow.
That document also includes the `watch_emulator_session.py` flow for automatic
session pull + analysis after an emulator session ends, along with the rubric
rule-disable workflow for findings marked not important.

# 💻 Development Environment

**Hello Android XR** uses the Gradle build system and can be imported directly into Android Studio.
Ensure you have the latest Canary version available, and update the XR emulator image in Android
Studio's SDK Manager before creating a new XR Emulator. The Canary version of Android Studio is
available [here](https://developer.android.com/studio/preview).

# Additional Resources

- https://developer.android.com/xr
- https://developer.android.com/develop/xr
- https://developer.android.com/design/ui/xr
- https://developer.android.com/develop/xr#bootcamp

# License

**Hello Android XR** is distributed under the terms of the Apache License (Version 2.0). See the
[license](LICENSE) for more information.
