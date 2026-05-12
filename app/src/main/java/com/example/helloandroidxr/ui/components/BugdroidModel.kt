/*
 * Copyright 2025 The Android Open Source Project
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

package com.example.helloandroidxr.ui.components

import android.annotation.SuppressLint
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalDensity
import androidx.xr.compose.platform.LocalSession
import androidx.xr.compose.spatial.PlanarEmbeddedSubspace
import androidx.xr.compose.subspace.SpatialGltfModel
import androidx.xr.compose.subspace.SpatialGltfModelAnimation
import androidx.xr.compose.subspace.SpatialGltfModelSource
import androidx.xr.compose.subspace.draw.scale
import androidx.xr.compose.subspace.layout.SubspaceModifier
import androidx.xr.compose.subspace.layout.onSizeChanged
import androidx.xr.compose.subspace.rememberSpatialGltfModelState
import androidx.xr.compose.unit.Meter
import androidx.xr.runtime.math.Vector4
import androidx.xr.scenecore.AlphaMode
import androidx.xr.scenecore.KhronosPbrMaterial
import androidx.xr.scenecore.Texture
import com.example.helloandroidxr.viewmodel.ModelTransform
import java.nio.file.Paths
import kotlin.io.path.Path

// Bugdroid glb height in meters
private const val bugdroidHeight = 2.08f

// The desired amount of the available layout height to use for the bugdroid
private const val fillRatio = 0.5f

@SuppressLint("NewApi", "RestrictedApi")
@Composable
fun BugdroidModel(
    modelTransform: ModelTransform,
    showBugdroid: Boolean,
    animateBugdroid: Boolean,
    modifier: SubspaceModifier = SubspaceModifier,
) {
    val xrSession = LocalSession.current
    val density = LocalDensity.current
    var scaleFromLayout by remember { mutableFloatStateOf(1f) }

    if (xrSession != null && showBugdroid) {
        // Initialize and remember the state of the glTF model, loading it from the assets folder.
        val bugdroidModelState = rememberSpatialGltfModelState(
            source = SpatialGltfModelSource.fromPath(
                Paths.get("models/bugdroid_animated_wave.glb")
            )
        )

        // Find a specific node by name to apply modifications, such as material overrides.
        val bugdroidNode = remember(bugdroidModelState.nodes) {
            bugdroidModelState.nodes.find { it.name == "Droid_Solo:Bugdroid" }
        }

        // Maintain a reference to the custom material to avoid re-creating it on every recomposition.
        var pbrMaterial by remember { mutableStateOf<KhronosPbrMaterial?>(null) }

        // Create and apply a custom PBR material to the model when the XR session or target node changes.
        LaunchedEffect(xrSession, bugdroidNode) {
            val material = pbrMaterial ?: KhronosPbrMaterial.create(
                session = xrSession,
                alphaMode = AlphaMode.OPAQUE
            ).also {
                pbrMaterial = it
                // Load a texture; using a plain white texture for visibility of the base color factor
                val texture = Texture.create(
                    session = xrSession,
                    path = Path("textures/white.png")
                )

                // Apply the texture and configure occlusion to define ambient lighting strength.
                it.setOcclusionTexture(
                    texture = texture,
                    strength = modelTransform.materialProperties.ambientOcclusion
                )

                // Apply the initial material properties. Base Color is RGBA value
                it.setBaseColorFactor(
                    Vector4(
                        x = modelTransform.materialColor.x,
                        y = modelTransform.materialColor.y,
                        z = modelTransform.materialColor.z,
                        w = modelTransform.materialColor.w
                    )
                )
                it.setMetallicFactor(modelTransform.materialProperties.metallic)
                it.setRoughnessFactor(modelTransform.materialProperties.roughness)
            }

            // Apply the custom PBR material to the specific node, overriding original glTF material.
            bugdroidNode?.setMaterialOverride(
                material = material
            )
        }

        // Update the base color material properties whenever the model transform state changes.
        LaunchedEffect(modelTransform.materialColor, pbrMaterial) {
            pbrMaterial?.setBaseColorFactor(
                Vector4(
                    x = modelTransform.materialColor.x,
                    y = modelTransform.materialColor.y,
                    z = modelTransform.materialColor.z,
                    w = modelTransform.materialColor.w
                )
            )
        }

        // Update the metallic factor property whenever the model transform state changes.
        LaunchedEffect(modelTransform.materialProperties.metallic, pbrMaterial) {
            pbrMaterial?.setMetallicFactor(modelTransform.materialProperties.metallic)
        }

        // Update the roughness property whenever the model transform state changes.
        LaunchedEffect(modelTransform.materialProperties.roughness, pbrMaterial) {
            pbrMaterial?.setRoughnessFactor(modelTransform.materialProperties.roughness)
        }

        // Control the model's animation state based on the animateBugdroid flag.
        LaunchedEffect(bugdroidModelState.animations) {
            val animation = bugdroidModelState.animations.find {
                it.name == "Armature|Take 001|BaseLayer"
            }
            if (animateBugdroid) {
                if (animation?.animationState != SpatialGltfModelAnimation.AnimationState.Playing) {
                    animation?.loop()
                }
            } else {
                animation?.stop()
            }
        }

        // Use a PlanarEmbeddedSubspace to anchor the 3D model within the 2D layout.
        PlanarEmbeddedSubspace {
            SpatialGltfModel(
                state = bugdroidModelState,
                modifier = modifier
                    .onSizeChanged { size ->
                        // Calculate the scale to use for the entity based on the layout size
                        val scaleToFillLayoutHeight = Meter
                            .fromPixel(size.height.toFloat(), density).toM() / bugdroidHeight
                        // Limit the scale to a ratio of the available space.
                        scaleFromLayout = scaleToFillLayoutHeight * fillRatio
                    }
                    .scale(scaleFromLayout * modelTransform.scale)
            )
        }
    }
}