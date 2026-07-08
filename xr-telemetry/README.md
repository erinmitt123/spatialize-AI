# XR Telemetry Module

`xr-telemetry` is a reusable Android library module for capturing XR interaction sessions as
JSON. The sample app already uses it to record:

- `ui_interaction` click/select events
- `rotation_change` events for the Bugdroid model
- `transform_change` events for scale and offset changes
- `spatial_transform` and `spatial_resize` events for movable/resizable spatial panels
- throttled `hand_pose` snapshots when XR hand tracking is available

## Build the package

From the repo root:

```powershell
.\gradlew.bat :xr-telemetry:bundleTelemetryPackage
```

The distributable zip is written to:

```text
xr-telemetry/build/distributions/xr-telemetry-package.zip
```

That archive contains:

- the `xr-telemetry` Gradle module source
- the built `xr-telemetry-release.aar`

## Import into another Android Studio project

### Option 1: import the module source

1. Copy the `xr-telemetry` folder into the target project.
2. Add `include(":xr-telemetry")` to the target project's `settings.gradle(.kts)`.
3. Add `implementation(project(":xr-telemetry"))` to the app module dependencies.

### Option 2: consume the AAR directly

1. Copy `aar/xr-telemetry-release.aar` into the target project's `app/libs/`.
2. Add `implementation(files("libs/xr-telemetry-release.aar"))` to the app module dependencies.

## Runtime session export

The recorder writes session files to the app-specific external files directory:

```text
/sdcard/Android/data/<applicationId>/files/xr-telemetry/sessions/
```

For the sample app that is:

```text
/sdcard/Android/data/com.example.helloandroidxr/files/xr-telemetry/sessions/
```

## Analyze exported sessions

Pull sessions from a connected emulator or device:

```powershell
adb pull /sdcard/Android/data/com.example.helloandroidxr/files/xr-telemetry/sessions hci_for_glasses/device_sessions
```

Then run:

```powershell
C:\Users\Erin Mitt\AppData\Local\Python\bin\python.exe hci_for_glasses\analyze_sessions.py --input-dir hci_for_glasses\device_sessions
```

That command now writes:

- a machine-readable JSON report
- a companion HTML dashboard with `Latest Session`, `All Sessions`, and review-state tabs
- lens-aware report data including active specialization lenses, retrieved lens focus rules, and any lens-specific AI findings

Packaged specialization lenses live in:

```text
hci_for_glasses/lenses/
```

Current lens ids:

- `medical`
- `manufacturing`
- `animator`
- `low_vision_accessibility`

To enable the AI judgment pass, set an API key first:

```powershell
$env:OPENAI_API_KEY="your_api_key_here"
```

Then run the analyzer normally, or force a mode explicitly:

```powershell
C:\Users\Erin Mitt\AppData\Local\Python\bin\python.exe hci_for_glasses\analyze_sessions.py --input-dir hci_for_glasses\device_sessions --analysis-mode hybrid
```

To focus analysis on one or more specialization lenses for that run:

```powershell
C:\Users\Erin Mitt\AppData\Local\Python\bin\python.exe hci_for_glasses\analyze_sessions.py --input-dir hci_for_glasses\device_sessions --lens medical --lens low_vision_accessibility
```

What the AI layer adds:

- reads multi-signal telemetry instead of only fixed thresholds
- writes a `researcher_summary` per session
- lets severity be judged in context instead of always using the rubric default
- falls back to heuristic-only analysis automatically if `OPENAI_API_KEY` is not set or the API call fails

## Auto-watch one emulator session

If you want the host machine to automatically mirror emulator sessions,
analyze them as they end, and open the readable report, run:

```powershell
C:\Users\Erin Mitt\AppData\Local\Python\bin\python.exe hci_for_glasses\watch_emulator_session.py
```

To preselect specialization lenses for that watcher run:

```powershell
C:\Users\Erin Mitt\AppData\Local\Python\bin\python.exe hci_for_glasses\watch_emulator_session.py --lens medical --lens manufacturing
```

If `OPENAI_API_KEY` is present in the same PowerShell session, the watcher-triggered analyses inherit it automatically.

What it does:

- watches `/sdcard/Android/data/com.example.helloandroidxr/files/xr-telemetry/sessions/`
- mirrors each tracked `session_*.json` into `hci_for_glasses/device_sessions/watched/`
- attaches to the latest active session if one is already running, otherwise waits for the next new session
- if the newest session already ended before the watcher starts, it analyzes that completed session immediately
- waits until the session ends or the emulator disconnects
- writes a per-session JSON analysis report into `hci_for_glasses/device_sessions/reports/`
- writes a per-session HTML report beside it
- writes `all_sessions_analysis.json` and `all_sessions_analysis.html` from every mirrored session
- updates `latest_analysis.json` and `latest_analysis.html` on every completed session
- opens the dashboard HTML automatically on Windows

Review actions in the dashboard:

- `Mark Completed` hides a finding from the open backlog and moves it into the Completed tab
- `Not Important` hides a finding from the open backlog and shows a command that can disable that rule in the rubric JSON for future analyses

Live lens toggles:

- the sample app now includes `Specialization Lenses` controls in the main content flow
- toggling a lens while the session is running writes an `analysis_lens_change` event and updates session metadata
- the analyzer uses those recorded lens changes as part of its local retrieval step, so the final report reflects which lens contexts were active during the session

To disable a rule directly from PowerShell without using the dashboard prompt:

```powershell
C:\Users\Erin Mitt\AppData\Local\Python\bin\python.exe hci_for_glasses\manage_rubric_rules.py --disable-rule touch_target_size
```

Important:

- leave the watcher running if you want it to process multiple emulator runs
- start the watcher before you close the emulator if you want the last disconnected session mirrored automatically
- once the emulator is already offline, `adb pull` cannot fetch the session anymore
