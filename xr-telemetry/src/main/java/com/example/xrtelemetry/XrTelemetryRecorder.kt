package com.example.xrtelemetry

import android.content.Context
import android.os.Build
import android.os.SystemClock
import androidx.xr.arcore.Hand
import androidx.xr.arcore.HandJointType
import androidx.xr.compose.subspace.layout.SpatialMoveEvent
import androidx.xr.compose.subspace.layout.SpatialMoveEventType
import androidx.xr.compose.unit.IntVolumeSize
import androidx.xr.runtime.Session
import androidx.xr.runtime.TrackingState
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.Locale
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class XrTelemetryRecorder(
    private val context: Context,
    private val config: XrTelemetryConfig = XrTelemetryConfig(),
) : AutoCloseable {
    private val lock = Any()
    private val writer = Executors.newSingleThreadExecutor()

    @Volatile
    private var xrSession: Session? = null

    private var activeSession: ActiveSession? = null
    private var lastHandSnapshotAtMs: Long = 0L

    fun attachSession(session: Session?) {
        xrSession = session
    }

    fun startSession(
        appName: String,
        scene: String,
        metadata: Map<String, String> = emptyMap(),
    ): String {
        val exportDirectory = exportDirectory()
        val sessionId = UUID.randomUUID().toString()
        val timestamp = System.currentTimeMillis()
        val fileName = "${config.sessionFilePrefix}_${timestamp}_${
            sessionId.substring(0, 8)
        }.json"
        val active =
            ActiveSession(
                sessionId = sessionId,
                appName = appName,
                scene = scene,
                file = File(exportDirectory, fileName),
                startedAtEpochMs = timestamp,
                startedAtElapsedMs = SystemClock.elapsedRealtime(),
                metadata = metadata.toMutableMap(),
            )
        synchronized(lock) {
            activeSession = active
            lastHandSnapshotAtMs = 0L
        }
        persistAsync()
        return sessionId
    }

    fun updateScene(scene: String) {
        synchronized(lock) {
            activeSession?.scene = scene
        }
        persistAsync()
    }

    fun currentSessionFile(): File? = synchronized(lock) { activeSession?.file }

    fun updateSessionMetadata(entries: Map<String, String?>) {
        synchronized(lock) {
            val metadata = activeSession?.metadata ?: return
            entries.forEach { (key, value) ->
                val normalized = value?.trim().orEmpty()
                if (normalized.isEmpty()) {
                    metadata.remove(key)
                } else {
                    metadata[key] = normalized
                }
            }
        }
        persistAsync()
    }

    fun logAnalysisLensChange(
        lensId: String,
        enabled: Boolean,
        activeLenses: Collection<String>,
        source: String = "analysis_lens_control",
    ) {
        val activeLensValue = activeLenses.joinToString(",")
        updateSessionMetadata(mapOf("analysis_lenses" to activeLensValue))
        appendEvent(
            JSONObject()
                .put("t", relativeSeconds())
                .put("type", "analysis_lens_change")
                .put("lens_id", lensId)
                .put("enabled", enabled)
                .put("active_lenses", activeLensValue)
                .put("source", source)
        )
    }

    fun logClick(component: String, target: String? = null, source: String = "click") {
        logUiInteraction(
            component = component,
            action = "click",
            target = target,
            source = source,
        )
    }

    /**
     * Records a tap that landed on no interactive control (a "wrong spot" click).
     * These are the misses that never reach a button's onClick, so they are the
     * only way the analyzer can see repeated clicking in the wrong place.
     */
    fun logMissedTap(region: String, x: Float? = null, y: Float? = null) {
        val extras = mutableMapOf<String, Any?>()
        x?.let { extras["tap_x"] = round(it) }
        y?.let { extras["tap_y"] = round(it) }
        logUiInteraction(
            component = region,
            action = "click",
            source = "background_tap",
            extras = extras,
        )
    }

    fun logUiInteraction(
        component: String,
        action: String,
        target: String? = null,
        source: String = "ui",
        issueFlag: String? = null,
        extras: Map<String, Any?> = emptyMap(),
    ) {
        val payload =
            JSONObject()
                .put("t", relativeSeconds())
                .put("type", "ui_interaction")
                .put("component", component)
                .put("action", action)
                .put("source", source)
        target?.let { payload.put("target", it) }
        issueFlag?.let { payload.put("issue_flag", it) }
        putExtras(payload, extras)
        appendEvent(payload)
        maybeCaptureHandSnapshot()
    }

    fun logRotation(
        target: String,
        source: String,
        rotation: TelemetryQuaternion,
        extras: Map<String, Any?> = emptyMap(),
    ) {
        val payload =
            JSONObject()
                .put("t", relativeSeconds())
                .put("type", "rotation_change")
                .put("target", target)
                .put("source", source)
                .put("rotation", quaternionToJson(rotation))
        putExtras(payload, extras)
        appendEvent(payload)
        maybeCaptureHandSnapshot()
    }

    fun logTransform(
        target: String,
        source: String,
        translation: TelemetryVector3? = null,
        scale: Float? = null,
        extras: Map<String, Any?> = emptyMap(),
    ) {
        val payload =
            JSONObject()
                .put("t", relativeSeconds())
                .put("type", "transform_change")
                .put("target", target)
                .put("source", source)
        translation?.let { payload.put("translation", vectorToJson(it)) }
        scale?.let { payload.put("scale", round(it)) }
        putExtras(payload, extras)
        appendEvent(payload)
        maybeCaptureHandSnapshot()
    }

    fun logSpatialMove(
        target: String,
        moveEvent: SpatialMoveEvent,
        source: String = "transforming_movable",
    ) {
        val payload =
            JSONObject()
                .put("t", relativeSeconds())
                .put("type", "spatial_transform")
                .put("target", target)
                .put("source", source)
                .put("phase", movePhase(moveEvent.type))
                .put("pose", poseToJson(moveEvent.pose))
                .put("previous_pose", poseToJson(moveEvent.previousPose))
                .put("scale", round(moveEvent.scale))
                .put("previous_scale", round(moveEvent.previousScale))
                .put("size", sizeToJson(moveEvent.size))
        appendEvent(payload)
        if (moveEvent.type != SpatialMoveEventType.Moving) {
            maybeCaptureHandSnapshot()
        }
    }

    fun logSpatialResize(
        target: String,
        phase: String,
        size: IntVolumeSize,
        source: String = "resizable",
    ) {
        val payload =
            JSONObject()
                .put("t", relativeSeconds())
                .put("type", "spatial_resize")
                .put("target", target)
                .put("source", source)
                .put("phase", phase)
                .put("size", sizeToJson(size))
        appendEvent(payload)
    }

    fun stopSession() {
        synchronized(lock) {
            activeSession?.endedAtEpochMs = System.currentTimeMillis()
        }
        persistSync()
    }

    override fun close() {
        stopSession()
        writer.shutdown()
        try {
            writer.awaitTermination(2, TimeUnit.SECONDS)
        } catch (_: InterruptedException) {
            Thread.currentThread().interrupt()
        }
    }

    private fun appendEvent(payload: JSONObject) {
        synchronized(lock) {
            activeSession?.events?.add(payload.toString())
        }
        persistAsync()
    }

    private fun maybeCaptureHandSnapshot() {
        val session = xrSession ?: return
        val now = SystemClock.elapsedRealtime()
        synchronized(lock) {
            if (now - lastHandSnapshotAtMs < config.handSnapshotThrottleMs) {
                return
            }
            lastHandSnapshotAtMs = now
        }

        captureHandSnapshot(session, "left") { Hand.left(session) }
        captureHandSnapshot(session, "right") { Hand.right(session) }
    }

    private fun captureHandSnapshot(
        session: Session,
        handName: String,
        resolveHand: () -> Hand?,
    ) {
        runCatching {
            val hand = resolveHand() ?: return
            val state = hand.state.value
            if (state.trackingState != TrackingState.TRACKING) {
                return
            }

            val jointLookup = state.handJoints
            val trackedJoints =
                listOf(
                    "wrist" to HandJointType.WRIST,
                    "palm" to HandJointType.PALM,
                    "thumb_tip" to HandJointType.THUMB_TIP,
                    "index_tip" to HandJointType.INDEX_TIP,
                    "middle_tip" to HandJointType.MIDDLE_TIP,
                )

            val jointsJson = JSONObject()
            var jointCount = 0
            for ((name, jointType) in trackedJoints) {
                val pose = jointLookup[jointType] ?: continue
                jointsJson.put(
                    name,
                    JSONObject()
                        .put("x", round(pose.translation.x))
                        .put("y", round(pose.translation.y))
                        .put("z", round(pose.translation.z))
                )
                jointCount += 1
            }
            if (jointCount == 0) {
                return
            }

            appendEvent(
                JSONObject()
                    .put("t", relativeSeconds())
                    .put("type", "hand_pose")
                    .put("hand", handName)
                    .put("source", "snapshot")
                    .put("joints", jointsJson)
            )
        }
    }

    private fun relativeSeconds(): Double {
        val startedAt = synchronized(lock) { activeSession?.startedAtElapsedMs } ?: return 0.0
        val deltaMs = SystemClock.elapsedRealtime() - startedAt
        return round(deltaMs / 1000.0)
    }

    private fun persistAsync() {
        writer.execute { writeSnapshot() }
    }

    private fun persistSync() {
        writer.submit { writeSnapshot() }.get(2, TimeUnit.SECONDS)
    }

    private fun writeSnapshot() {
        val snapshot = synchronized(lock) { activeSession?.snapshot() } ?: return
        snapshot.file.parentFile?.mkdirs()
        snapshot.file.writeText(snapshot.toJson().toString(2))
    }

    private fun exportDirectory(): File =
        context.getExternalFilesDir(config.exportDirectoryName)
            ?: File(context.filesDir, config.exportDirectoryName)

    private fun movePhase(type: SpatialMoveEventType): String =
        when (type) {
            SpatialMoveEventType.Start -> "start"
            SpatialMoveEventType.End -> "end"
            else -> "moving"
        }

    private fun poseToJson(pose: androidx.xr.runtime.math.Pose): JSONObject =
        JSONObject()
            .put("translation", vectorToJson(pose.translation))
            .put(
                "rotation",
                JSONObject()
                    .put("x", round(pose.rotation.x))
                    .put("y", round(pose.rotation.y))
                    .put("z", round(pose.rotation.z))
                    .put("w", round(pose.rotation.w))
            )

    private fun vectorToJson(vector: TelemetryVector3): JSONObject =
        JSONObject()
            .put("x", round(vector.x))
            .put("y", round(vector.y))
            .put("z", round(vector.z))

    private fun vectorToJson(vector: androidx.xr.runtime.math.Vector3): JSONObject =
        JSONObject()
            .put("x", round(vector.x))
            .put("y", round(vector.y))
            .put("z", round(vector.z))

    private fun quaternionToJson(quaternion: TelemetryQuaternion): JSONObject =
        JSONObject()
            .put("x", round(quaternion.x))
            .put("y", round(quaternion.y))
            .put("z", round(quaternion.z))
            .put("w", round(quaternion.w))

    private fun sizeToJson(size: IntVolumeSize): JSONObject =
        JSONObject()
            .put("width", size.width)
            .put("height", size.height)
            .put("depth", size.depth)

    private fun putExtras(payload: JSONObject, extras: Map<String, Any?>) {
        for ((key, value) in extras) {
            if (value != null) {
                payload.put(key, value)
            }
        }
    }

    private fun round(value: Float): Double = round(value.toDouble())

    private fun round(value: Double): Double =
        String.format(Locale.US, "%.4f", value).toDouble()

    private data class ActiveSession(
        val sessionId: String,
        val appName: String,
        var scene: String,
        val file: File,
        val startedAtEpochMs: Long,
        val startedAtElapsedMs: Long,
        val metadata: MutableMap<String, String>,
        val events: MutableList<String> = mutableListOf(),
        var endedAtEpochMs: Long? = null,
    ) {
        fun snapshot(): ActiveSessionSnapshot =
            ActiveSessionSnapshot(
                sessionId = sessionId,
                appName = appName,
                scene = scene,
                file = file,
                startedAtEpochMs = startedAtEpochMs,
                endedAtEpochMs = endedAtEpochMs,
                metadata = metadata.toMap(),
                events = events.toList(),
            )
    }

    private data class ActiveSessionSnapshot(
        val sessionId: String,
        val appName: String,
        val scene: String,
        val file: File,
        val startedAtEpochMs: Long,
        val endedAtEpochMs: Long?,
        val metadata: Map<String, String>,
        val events: List<String>,
    ) {
        fun toJson(): JSONObject {
            val root =
                JSONObject()
                    .put("session_id", sessionId)
                    .put("app", appName)
                    .put("scene", scene)
                    .put("started_at_epoch_ms", startedAtEpochMs)
                    .put(
                        "device",
                        JSONObject()
                            .put("manufacturer", Build.MANUFACTURER)
                            .put("model", Build.MODEL)
                            .put("sdk_int", Build.VERSION.SDK_INT)
                    )
            endedAtEpochMs?.let { root.put("ended_at_epoch_ms", it) }
            if (metadata.isNotEmpty()) {
                val metadataJson = JSONObject()
                metadata.forEach { (key, value) -> metadataJson.put(key, value) }
                root.put("metadata", metadataJson)
            }

            val eventsJson = JSONArray()
            events.forEach { eventsJson.put(JSONObject(it)) }
            root.put("events", eventsJson)
            return root
        }
    }
}
