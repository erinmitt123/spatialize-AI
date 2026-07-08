package com.example.xrtelemetry

data class XrTelemetryConfig(
    val exportDirectoryName: String = "xr-telemetry/sessions",
    val sessionFilePrefix: String = "session",
    val handSnapshotThrottleMs: Long = 400L,
)

data class TelemetryVector3(
    val x: Float,
    val y: Float,
    val z: Float,
)

data class TelemetryQuaternion(
    val x: Float,
    val y: Float,
    val z: Float,
    val w: Float,
)
