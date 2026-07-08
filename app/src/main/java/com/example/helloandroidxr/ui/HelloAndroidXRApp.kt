/*
 * Copyright 2024 The Android Open Source Project
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     https://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package com.example.helloandroidxr.ui

import androidx.activity.ComponentActivity
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.requiredHeight
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.adaptive.currentWindowAdaptiveInfo
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.dimensionResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.xr.compose.platform.LocalSession
import androidx.window.core.layout.WindowSizeClass
import androidx.xr.compose.platform.LocalSpatialCapabilities
import androidx.xr.compose.platform.LocalSpatialConfiguration
import androidx.xr.compose.spatial.ContentEdge
import androidx.xr.compose.spatial.Orbiter
import androidx.xr.compose.spatial.Subspace
import androidx.xr.compose.subspace.SpatialColumn
import androidx.xr.compose.subspace.SpatialPanel
import androidx.xr.compose.subspace.SpatialRow
import androidx.xr.compose.subspace.draw.alpha
import androidx.xr.compose.subspace.layout.SubspaceModifier
import androidx.xr.compose.subspace.layout.fillMaxSize
import androidx.xr.compose.subspace.layout.fillMaxWidth
import androidx.xr.compose.subspace.layout.height
import androidx.xr.compose.subspace.layout.offset
import androidx.xr.compose.subspace.layout.padding
import androidx.xr.compose.subspace.layout.resizable
import androidx.xr.compose.subspace.layout.rotate
import androidx.xr.compose.subspace.layout.size
import androidx.xr.compose.subspace.layout.transformingMovable
import androidx.xr.compose.subspace.layout.width
import androidx.xr.runtime.math.Quaternion
import com.example.helloandroidxr.R
import com.example.helloandroidxr.ui.components.BugdroidControls
import com.example.helloandroidxr.ui.components.BugdroidModel
import com.example.helloandroidxr.ui.components.BugdroidSliderControls
import com.example.helloandroidxr.ui.components.AnalysisLensControls
import com.example.helloandroidxr.ui.components.EnvironmentControls
import com.example.helloandroidxr.ui.components.SearchBar
import com.example.helloandroidxr.ui.components.TextPane
import com.example.helloandroidxr.ui.theme.HelloAndroidXRTheme
import com.example.xrtelemetry.TelemetryQuaternion
import com.example.xrtelemetry.TelemetryVector3
import com.example.xrtelemetry.XrTelemetryRecorder
import com.example.helloandroidxr.viewmodel.BugdroidUiState
import com.example.helloandroidxr.viewmodel.BugdroidViewModel
import com.example.helloandroidxr.viewmodel.ModelMaterialColor
import com.example.helloandroidxr.viewmodel.ModelMaterialProperties
import com.example.helloandroidxr.viewmodel.ModelOffset
import com.example.helloandroidxr.viewmodel.ModelRotation
import com.example.helloandroidxr.viewmodel.SliderGroup
import kotlinx.coroutines.launch
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver

@Composable
fun HelloAndroidXRApp() {
    val context = LocalContext.current
    val activity = context as? ComponentActivity
    val xrSession = LocalSession.current
    val viewModel = remember { BugdroidViewModel() }
    val uiState by viewModel.uiState.collectAsState()
    val isSpatialUiEnabled = LocalSpatialCapabilities.current.isSpatialUiEnabled
    val telemetry = remember(context.applicationContext) {
        XrTelemetryRecorder(context.applicationContext)
    }
    val sceneName = if (isSpatialUiEnabled) "spatial_ui" else "non_spatial_ui"
    var isTelemetrySessionActive by remember { mutableStateOf(false) }
    var activeAnalysisLensIds by remember { mutableStateOf(emptySet<String>()) }
    val activeAnalysisLensSignature =
        activeAnalysisLensIds.toList().sorted().joinToString(",")
    val currentAnalysisLensSignature by rememberUpdatedState(activeAnalysisLensSignature)

    LaunchedEffect(xrSession) {
        telemetry.attachSession(xrSession)
    }
    LaunchedEffect(sceneName) {
        telemetry.updateScene(sceneName)
    }
    // Keyed on the activity/recorder only (NOT sceneName): a Home<->Full space
    // transition flips sceneName and would otherwise tear this effect down,
    // ending the session and starting a fresh empty one so clicks scatter across
    // sessions. Scene changes are handled by the LaunchedEffect(sceneName) above,
    // which calls updateScene on the still-active session instead.
    DisposableEffect(activity, telemetry) {
        fun startTelemetrySession() {
            if (isTelemetrySessionActive) {
                return
            }
            telemetry.startSession(
                appName = context.getString(R.string.app_name),
                scene = sceneName,
                metadata = buildMap {
                    put("package_name", context.packageName)
                    if (currentAnalysisLensSignature.isNotBlank()) {
                        put("analysis_lenses", currentAnalysisLensSignature)
                    }
                }
            )
            isTelemetrySessionActive = true
        }

        fun stopTelemetrySession() {
            if (!isTelemetrySessionActive) {
                return
            }
            telemetry.stopSession()
            isTelemetrySessionActive = false
        }

        val observer =
            LifecycleEventObserver { _, event ->
                when (event) {
                    Lifecycle.Event.ON_START -> startTelemetrySession()
                    Lifecycle.Event.ON_STOP -> stopTelemetrySession()
                    else -> Unit
                }
            }

        if (activity != null) {
            activity.lifecycle.addObserver(observer)
            if (activity.lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) {
                startTelemetrySession()
            }
        } else {
            startTelemetrySession()
        }

        onDispose {
            if (activity != null) {
                activity.lifecycle.removeObserver(observer)
            }
            stopTelemetrySession()
            telemetry.close()
        }
    }

    val onShowBugdroidToggle = {
        telemetry.logClick("toggle_bugdroid_button", target = "bugdroid")
        viewModel.updateShowBugdroid()
    }
    val onAnimateBugdroidToggle = {
        telemetry.logClick("toggle_bugdroid_animation_button", target = "bugdroid")
        viewModel.updateAnimateBugdroid()
    }
    val onSliderGroupSelected: (SliderGroup) -> Unit = { group ->
        telemetry.logUiInteraction(
            component = "slider_group_button",
            action = "select",
            target = "bugdroid",
            source = "controls_panel",
            extras = mapOf("group" to group.name.lowercase())
        )
        viewModel.updateShownSliderGroup(group)
    }
    val onResetModel = {
        telemetry.logClick("reset_model_button", target = "bugdroid")
        telemetry.logTransform(
            target = "bugdroid",
            source = "reset_model",
            translation = TelemetryVector3(0f, 0f, 400f),
            scale = 1f,
            extras = mapOf("reset" to true)
        )
        telemetry.logRotation(
            target = "bugdroid",
            source = "reset_model",
            rotation = TelemetryQuaternion(0f, 0f, 0f, 1f)
        )
        viewModel.resetModel()
    }
    val onScaleChange: (Float) -> Unit = { scale ->
        viewModel.updateScale(scale)
        telemetry.logTransform(
            target = "bugdroid",
            source = "scale_slider",
            scale = scale
        )
    }
    val onRotationChange: (ModelRotation) -> Unit = { rotation ->
        viewModel.updateRotation(rotation)
        telemetry.logRotation(
            target = "bugdroid",
            source = "rotation_slider",
            rotation = TelemetryQuaternion(
                x = rotation.x,
                y = rotation.y,
                z = rotation.z,
                w = rotation.w
            )
        )
    }
    val onOffsetChange: (ModelOffset) -> Unit = { offset ->
        viewModel.updateOffset(offset)
        telemetry.logTransform(
            target = "bugdroid",
            source = "offset_slider",
            translation = TelemetryVector3(
                x = offset.x,
                y = offset.y,
                z = offset.z
            )
        )
    }
    val onToggleAnalysisLens: (String) -> Unit = { lensId ->
        val updated =
            activeAnalysisLensIds.toMutableSet().apply {
                if (!add(lensId)) {
                    remove(lensId)
                }
            }
        val activeLenses = updated.toList().sorted()
        activeAnalysisLensIds = updated.toSet()
        telemetry.updateSessionMetadata(
            mapOf("analysis_lenses" to activeLenses.joinToString(","))
        )
        if (isTelemetrySessionActive) {
            telemetry.logAnalysisLensChange(
                lensId = lensId,
                enabled = lensId in updated,
                activeLenses = activeLenses,
            )
        }
    }

    if (isSpatialUiEnabled) {
        SpatialLayout(
            telemetry = telemetry,
            activeAnalysisLensIds = activeAnalysisLensIds,
            onToggleAnalysisLens = onToggleAnalysisLens,
            primaryContent = {
                PrimaryContent(
                    uiState = uiState,
                    onShowBugdroidToggle = onShowBugdroidToggle,
                    onAnimateBugdroidToggle = onAnimateBugdroidToggle
                )
            },
            firstSupportingContent = {
                BlockOfContentOne(
                    showBugdroid = uiState.showBugdroid,
                    onSliderGroupSelected = onSliderGroupSelected,
                    onResetModel = onResetModel
                )
            },
            secondSupportingContent = {
                BlockOfContentTwo(
                    uiState = uiState,
                    showBugdroid = uiState.showBugdroid,
                    onScaleChange = onScaleChange,
                    onRotationChange = onRotationChange,
                    onOffsetChange = onOffsetChange,
                    onMaterialColorChange = viewModel::updateMaterialColor,
                    onMaterialPropertiesChange = viewModel::updateMaterialProperties
                )
            }
        )
    } else {
        NonSpatialTwoPaneLayout(
            telemetry = telemetry,
            activeAnalysisLensIds = activeAnalysisLensIds,
            onToggleAnalysisLens = onToggleAnalysisLens,
            secondaryPane = {
                BlockOfContentOne(
                    modifier = Modifier.height(240.dp),
                    showBugdroid = uiState.showBugdroid,
                    onSliderGroupSelected = onSliderGroupSelected,
                    onResetModel = onResetModel
                )
                BlockOfContentTwo(
                    uiState = uiState,
                    showBugdroid = uiState.showBugdroid,
                    onScaleChange = onScaleChange,
                    onRotationChange = onRotationChange,
                    onOffsetChange = onOffsetChange,
                    onMaterialColorChange = viewModel::updateMaterialColor,
                    onMaterialPropertiesChange = viewModel::updateMaterialProperties
                )
            },
            primaryPane = {
                PrimaryContent(
                    uiState = uiState,
                    onShowBugdroidToggle = onShowBugdroidToggle,
                    onAnimateBugdroidToggle = onAnimateBugdroidToggle
                )
            }
        )
    }
}

/**
 * Layout that displays content in [SpatialPanel]s, should be used when spatial UI is enabled.
 */
@Composable
private fun SpatialLayout(
    telemetry: XrTelemetryRecorder,
    activeAnalysisLensIds: Set<String>,
    onToggleAnalysisLens: (String) -> Unit,
    primaryContent: @Composable () -> Unit,
    firstSupportingContent: @Composable () -> Unit,
    secondSupportingContent: @Composable () -> Unit
) {
    val animatedAlpha = remember { Animatable(0.5f) }
    LaunchedEffect(Unit) {
        launch {
            animatedAlpha.animateTo(
                1.0f,
                animationSpec = tween(durationMillis = 400, easing = FastOutSlowInEasing)
            )
        }
    }
    Subspace {
        SpatialRow(modifier = SubspaceModifier.height(816.dp).fillMaxWidth()) {
            SpatialColumn(modifier = SubspaceModifier.width(400.dp)) {
                SpatialPanel(
                    SubspaceModifier
                        .alpha(animatedAlpha.value)
                        .size(400.dp)
                        .padding(bottom = 16.dp)
                        .transformingMovable {
                            telemetry.logSpatialMove("controls_panel", it)
                        }
                        .resizable(
                            onResizeStart = {
                                telemetry.logSpatialResize("controls_panel", "start", it)
                            },
                            onResizeUpdate = {
                                telemetry.logSpatialResize("controls_panel", "ongoing", it)
                            },
                            onResizeEnd = {
                                telemetry.logSpatialResize("controls_panel", "end", it)
                                false
                            }
                        ),
                ) {
                    firstSupportingContent()
                }
                SpatialPanel(
                    SubspaceModifier
                        .alpha(animatedAlpha.value)
                        .weight(1f)
                        .transformingMovable {
                            telemetry.logSpatialMove("sliders_panel", it)
                        }
                        .resizable(
                            onResizeStart = {
                                telemetry.logSpatialResize("sliders_panel", "start", it)
                            },
                            onResizeUpdate = {
                                telemetry.logSpatialResize("sliders_panel", "ongoing", it)
                            },
                            onResizeEnd = {
                                telemetry.logSpatialResize("sliders_panel", "end", it)
                                false
                            }
                        ),
                ) {
                    secondSupportingContent()
                }
            }
            SpatialPanel(
                modifier = SubspaceModifier
                    .alpha(animatedAlpha.value)
                    .fillMaxSize()
                    .padding(end = 16.dp)
                    .transformingMovable {
                        telemetry.logSpatialMove("primary_panel", it)
                    }
                    .resizable(
                        onResizeStart = {
                            telemetry.logSpatialResize("primary_panel", "start", it)
                        },
                        onResizeUpdate = {
                            telemetry.logSpatialResize("primary_panel", "ongoing", it)
                        },
                        onResizeEnd = {
                            telemetry.logSpatialResize("primary_panel", "end", it)
                            false
                        }
                    ),
            ) {
                Column(
                    modifier = Modifier.pointerInput(Unit) {
                        // Fires only for taps that no interactive child consumed,
                        // i.e. clicks that missed every control ("wrong spot").
                        detectTapGestures { offset ->
                            telemetry.logMissedTap("primary_panel", offset.x, offset.y)
                        }
                    }
                ) {
                    TopAppBar(telemetry = telemetry)
                    AnalysisLensControls(
                        activeLensIds = activeAnalysisLensIds,
                        onToggleLens = onToggleAnalysisLens,
                        modifier = Modifier.padding(horizontal = 24.dp, vertical = 12.dp),
                    )
                    primaryContent()
                }
            }
        }
    }
}

/**
 * Layout that displays content in a 2-pane layout, should be used when spatial UI is not enabled.
 */
@Composable
private fun NonSpatialTwoPaneLayout(
    telemetry: XrTelemetryRecorder,
    activeAnalysisLensIds: Set<String>,
    onToggleAnalysisLens: (String) -> Unit,
    primaryPane: @Composable () -> Unit,
    secondaryPane: @Composable () -> Unit,
    modifier: Modifier = Modifier,
    windowSizeClass: WindowSizeClass = currentWindowAdaptiveInfo(supportLargeAndXLargeWidth = true).windowSizeClass
) {
    val animatedAlpha = remember { Animatable(0.5f) }
    LaunchedEffect(Unit) {
        launch {
            animatedAlpha.animateTo(
                1.0f,
                animationSpec = tween(durationMillis = 300, easing = FastOutSlowInEasing)
            )
        }
    }
    Column(
        modifier = modifier
            .alpha(animatedAlpha.value)
            .pointerInput(Unit) {
                // Fires only for taps that no interactive child consumed,
                // i.e. clicks that missed every control ("wrong spot").
                detectTapGestures { offset ->
                    telemetry.logMissedTap("non_spatial_content", offset.x, offset.y)
                }
            }
            .padding(16.dp)
            .systemBarsPadding()
    ) {
        TopAppBar(telemetry = telemetry)
        Spacer(Modifier.height(12.dp))
        AnalysisLensControls(
            activeLensIds = activeAnalysisLensIds,
            onToggleLens = onToggleAnalysisLens,
        )
        Spacer(Modifier.height(16.dp))
        if (windowSizeClass.isWidthAtLeastBreakpoint(WindowSizeClass.HEIGHT_DP_EXPANDED_LOWER_BOUND)) {
            TopAndBottomPaneLayout(primaryPane, secondaryPane)
        } else {
            SideBySidePaneLayout(primaryPane, secondaryPane)
        }
    }
}

/**
 * Positions the panes in a horizontal orientation
 */
@Composable
private fun SideBySidePaneLayout(
    primaryPane: @Composable () -> Unit,
    secondaryPane: @Composable () -> Unit,
    modifier: Modifier = Modifier
) {
    Row(modifier) {
        Surface(
            Modifier
                .width(400.dp)
                .clip(RoundedCornerShape(16.dp))
        ) {
            Column {
                secondaryPane()
            }
        }
        Spacer(Modifier.width(16.dp))
        Surface(modifier.clip(RoundedCornerShape(16.dp))) {
            primaryPane()
        }
    }
}

/**
 * Positions the panes in a scrollable vertical orientation
 */
@Composable
private fun TopAndBottomPaneLayout(
    primaryPane: @Composable () -> Unit,
    secondaryPane: @Composable () -> Unit,
    modifier: Modifier = Modifier
) {
    Column(modifier.verticalScroll(rememberScrollState())) {
        Surface(Modifier.requiredHeight(500.dp)) {
            primaryPane()
        }
        Spacer(Modifier.height(16.dp))
        Surface(
            Modifier
                .requiredHeight(500.dp)
                .fillMaxWidth()
                .clip(RoundedCornerShape(16.dp))
        ) {
            Column {
                secondaryPane()
            }
        }
    }
}

/**
 * Contains controls that decompose into Orbiters when spatial UI is enabled
 */
@Suppress("DEPRECATION")
@Composable
private fun TopAppBar(telemetry: XrTelemetryRecorder) {
    Row(
        horizontalArrangement = Arrangement.SpaceBetween,
        modifier = Modifier
            .height(IntrinsicSize.Min)
            .fillMaxWidth()
    ) {
        Spacer(Modifier.weight(1f))
        Orbiter(
            position = ContentEdge.Top,
            offset = dimensionResource(R.dimen.top_ornament_padding),
            alignment = Alignment.Start
        ) {
            SearchBar()
        }
        Spacer(Modifier.weight(1f))
        Orbiter(
            position = ContentEdge.Top,
            offset = dimensionResource(R.dimen.top_ornament_padding),
            alignment = Alignment.End
        ) {
            EnvironmentControls(telemetry = telemetry)
        }
    }
}

@Composable
private fun PrimaryContent(
    uiState: BugdroidUiState,
    onShowBugdroidToggle: () -> Unit,
    onAnimateBugdroidToggle: () -> Unit,
    modifier: Modifier = Modifier,
) {
    if (LocalSpatialCapabilities.current.isSpatialUiEnabled) {
        val showStringResId =
            if (uiState.showBugdroid) R.string.hide_bugdroid else R.string.show_bugdroid
        val animateStringResId =
            if (uiState.animateBugdroid) R.string.stop_animation_bugdroid else R.string.animate_bugdroid
        val modelTransform = uiState.modelTransform
        Surface(modifier.fillMaxSize()) {
            Column(modifier.padding(48.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                Box(modifier.padding(48.dp), contentAlignment = Alignment.Center) {
                    Button(
                        onClick = onShowBugdroidToggle,
                        modifier = modifier
                    ) {
                        Text(
                            text = stringResource(id = showStringResId),
                            style = MaterialTheme.typography.labelLarge
                        )
                    }
                }
                Box(modifier.padding(48.dp), contentAlignment = Alignment.Center) {
                    if (uiState.showBugdroid) {
                        Button(
                            onClick = onAnimateBugdroidToggle,
                            modifier = modifier
                        ) {
                            Text(
                                text = stringResource(id = animateStringResId),
                                style = MaterialTheme.typography.labelLarge
                            )
                        }
                    }
                }
                BugdroidModel(
                    modelTransform = modelTransform,
                    showBugdroid = uiState.showBugdroid,
                    animateBugdroid = uiState.animateBugdroid,
                    modifier = SubspaceModifier
                        .fillMaxSize()
                        .rotate(
                            Quaternion(
                                x = modelTransform.rotation.x,
                                y = modelTransform.rotation.y,
                                z = modelTransform.rotation.z,
                                w = modelTransform.rotation.w
                            )
                        )
                        .offset(
                            x = modelTransform.offset.x.dp,
                            y = modelTransform.offset.y.dp,
                            z = modelTransform.offset.z.dp // Relative position from the panel
                        )
                )
            }
        }
    } else {
        TextPane(
            text = stringResource(R.string.primary_content),
            modifier = modifier.clip(RoundedCornerShape(16.dp))
        )
    }
}

@Composable
private fun BlockOfContentOne(
    modifier: Modifier = Modifier,
    showBugdroid: Boolean,
    onSliderGroupSelected: (SliderGroup) -> Unit,
    onResetModel: () -> Unit
) {
    if (LocalSpatialConfiguration.current.hasXrSpatialFeature && showBugdroid) {
        BugdroidControls(
            onSliderGroupSelected = onSliderGroupSelected,
            onResetModel = {
                onResetModel()
                onSliderGroupSelected(SliderGroup.NONE)
            },
            modifier = modifier
        )
    } else {
        TextPane(stringResource(R.string.block_of_content_1), modifier = modifier.fillMaxHeight())
    }
}

@Composable
private fun BlockOfContentTwo(
    modifier: Modifier = Modifier,
    uiState: BugdroidUiState,
    showBugdroid: Boolean,
    onScaleChange: (Float) -> Unit,
    onRotationChange: (ModelRotation) -> Unit,
    onOffsetChange: (ModelOffset) -> Unit,
    onMaterialColorChange: (ModelMaterialColor) -> Unit,
    onMaterialPropertiesChange: (ModelMaterialProperties) -> Unit,
) {
    if (LocalSpatialConfiguration.current.hasXrSpatialFeature && showBugdroid) {
        BugdroidSliderControls(
            visibleSliderGroup = uiState.visibleSliderGroup,
            modelTransform = uiState.modelTransform,
            onScaleChange = onScaleChange,
            onRotationChange = onRotationChange,
            onOffsetChange = onOffsetChange,
            onMaterialColorChange = onMaterialColorChange,
            onMaterialPropertiesChange = onMaterialPropertiesChange,
            modifier = modifier
        )
    } else {
        TextPane(stringResource(R.string.block_of_content_2), modifier = modifier.fillMaxHeight())
    }
}

@Composable
@Preview(device = "spec:width=1920dp,height=1080dp,dpi=160")
@Preview(device = "spec:width=411dp,height=891dp")
fun AppLayoutPreview() {
    HelloAndroidXRTheme {
        HelloAndroidXRApp()
    }
}
