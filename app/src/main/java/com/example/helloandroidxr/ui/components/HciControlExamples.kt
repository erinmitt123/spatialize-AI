package com.example.helloandroidxr.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.helloandroidxr.R
import com.example.xrtelemetry.XrTelemetryRecorder

@Composable
fun HciControlExamples(
    telemetry: XrTelemetryRecorder,
    modifier: Modifier = Modifier,
) {
    var goodStatus by remember { mutableStateOf("No clean example tapped yet.") }
    var badStatus by remember { mutableStateOf("No intentionally weak control tapped yet.") }

    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(24.dp),
        tonalElevation = 3.dp,
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 18.dp, vertical = 16.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Text(
                text = stringResource(R.string.hci_examples_title),
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
            )
            Text(
                text = stringResource(R.string.hci_examples_subtitle),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            BoxWithConstraints {
                val wideLayout = maxWidth >= 700.dp
                if (wideLayout) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        GoodExamplesSection(
                            telemetry = telemetry,
                            status = goodStatus,
                            onStatusChange = { goodStatus = it },
                            modifier = Modifier.weight(1f),
                        )
                        BadExamplesSection(
                            telemetry = telemetry,
                            status = badStatus,
                            onStatusChange = { badStatus = it },
                            modifier = Modifier.weight(1f),
                        )
                    }
                } else {
                    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        GoodExamplesSection(
                            telemetry = telemetry,
                            status = goodStatus,
                            onStatusChange = { goodStatus = it },
                        )
                        BadExamplesSection(
                            telemetry = telemetry,
                            status = badStatus,
                            onStatusChange = { badStatus = it },
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun GoodExamplesSection(
    telemetry: XrTelemetryRecorder,
    status: String,
    onStatusChange: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    SectionShell(
        title = stringResource(R.string.hci_examples_good_title),
        body = stringResource(R.string.hci_examples_good_body),
        modifier = modifier,
    ) {
        Button(
            onClick = {
                telemetry.logUiInteraction(
                    component = "good_example_open_checklist_button",
                    action = "click",
                    target = "open_checklist",
                    source = "example_good_button",
                    extras = mapOf(
                        "example_quality" to "good",
                        "example_pattern" to "large_clear_primary_action",
                    ),
                )
                onStatusChange("Checklist opened with clear visual feedback.")
            },
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 52.dp),
        ) {
            Text(stringResource(R.string.hci_examples_good_open_checklist))
        }

        FilledTonalButton(
            onClick = {
                telemetry.logUiInteraction(
                    component = "good_example_confirm_button",
                    action = "click",
                    target = "confirm_placement",
                    source = "example_good_button",
                    extras = mapOf(
                        "example_quality" to "good",
                        "example_pattern" to "strong_confirmation_button",
                    ),
                )
                onStatusChange("Placement confirmed with a clear, immediate response.")
            },
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 52.dp),
        ) {
            Text(stringResource(R.string.hci_examples_good_confirm_placement))
        }

        FilledTonalButton(
            onClick = {
                telemetry.logUiInteraction(
                    component = "good_example_help_button",
                    action = "click",
                    target = "show_help",
                    source = "example_good_button",
                    extras = mapOf(
                        "example_quality" to "good",
                        "example_pattern" to "secondary_support_action",
                    ),
                )
                onStatusChange("Help opened with a distinct label and safe secondary styling.")
            },
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(min = 48.dp),
        ) {
            Text(stringResource(R.string.hci_examples_good_show_help))
        }

        StatusSurface(text = status, emphasized = true)
    }
}

@Composable
private fun BadExamplesSection(
    telemetry: XrTelemetryRecorder,
    status: String,
    onStatusChange: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    SectionShell(
        title = stringResource(R.string.hci_examples_bad_title),
        body = stringResource(R.string.hci_examples_bad_body),
        modifier = modifier,
    ) {
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            BadExampleChip(
                label = stringResource(R.string.hci_examples_bad_tiny),
                width = 42.dp,
                onClick = {
                    telemetry.logUiInteraction(
                        component = "bad_example_tiny_button",
                        action = "click",
                        target = "tiny_button",
                        source = "example_bad_button",
                        issueFlag = "example_small_touch_target",
                        extras = mapOf(
                            "example_quality" to "bad",
                            "intended_rule" to "touch_target_size",
                        ),
                    )
                    onStatusChange("Tiny target tapped. This should flag touch target sizing.")
                },
            )
            BadExampleChip(
                label = stringResource(R.string.hci_examples_bad_ambiguous),
                width = 42.dp,
                onClick = {
                    telemetry.logUiInteraction(
                        component = "bad_example_ambiguous_button",
                        action = "click",
                        target = "ambiguous_button",
                        source = "example_bad_button",
                        issueFlag = "example_ambiguous_icon_button",
                        extras = mapOf(
                            "example_quality" to "bad",
                            "intended_rule" to "icon_button_visual_clarity",
                        ),
                    )
                    onStatusChange("Ambiguous button tapped. This should flag icon clarity.")
                },
            )
            BadExampleChip(
                label = stringResource(R.string.hci_examples_bad_silent),
                width = 54.dp,
                onClick = {
                    telemetry.logUiInteraction(
                        component = "bad_example_silent_button",
                        action = "click",
                        target = "silent_button",
                        source = "example_bad_button",
                        issueFlag = "example_missing_feedback",
                        extras = mapOf(
                            "example_quality" to "bad",
                            "intended_rule" to "press_feedback_confirmation",
                        ),
                    )
                    onStatusChange("Silent button tapped. This should flag missing feedback.")
                },
            )
        }

        Surface(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(14.dp))
                .clickable {
                    telemetry.logUiInteraction(
                        component = "bad_example_flat_button",
                        action = "click",
                        target = "flat_button",
                        source = "example_bad_button",
                        issueFlag = "example_low_affordance_button",
                        extras = mapOf(
                            "example_quality" to "bad",
                            "intended_rule" to "control_affordance_visibility",
                        ),
                    )
                    onStatusChange("Flat control tapped. This should flag weak affordance visibility.")
                },
            color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.75f),
        ) {
            Text(
                text = stringResource(R.string.hci_examples_bad_flat),
                modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.outline,
            )
        }

        Text(
            text = stringResource(R.string.hci_examples_bad_tip),
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        StatusSurface(text = status, emphasized = false)
    }
}

@Composable
private fun SectionShell(
    title: String,
    body: String,
    modifier: Modifier = Modifier,
    content: @Composable ColumnScope.() -> Unit,
) {
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(20.dp),
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.35f),
    ) {
        Column(
            modifier = Modifier.padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
            content = {
                Text(
                    text = title,
                    style = MaterialTheme.typography.titleSmall,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    text = body,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                content()
            },
        )
    }
}

@Composable
private fun BadExampleChip(
    label: String,
    width: androidx.compose.ui.unit.Dp,
    onClick: () -> Unit,
) {
    Surface(
        modifier = Modifier
            .width(width)
            .size(height = 30.dp, width = width)
            .clip(RoundedCornerShape(10.dp))
            .clickable(onClick = onClick),
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.95f),
    ) {
        androidx.compose.foundation.layout.Box(
            contentAlignment = Alignment.Center,
            modifier = Modifier.background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.95f)),
        ) {
            Text(
                text = label,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.outline,
            )
        }
    }
}

@Composable
private fun StatusSurface(
    text: String,
    emphasized: Boolean,
) {
    Surface(
        shape = RoundedCornerShape(14.dp),
        color = if (emphasized) {
            MaterialTheme.colorScheme.primaryContainer.copy(alpha = 0.7f)
        } else {
            MaterialTheme.colorScheme.surface.copy(alpha = 0.7f)
        },
    ) {
        Text(
            text = text,
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
            style = MaterialTheme.typography.bodySmall,
            color = if (emphasized) {
                MaterialTheme.colorScheme.onPrimaryContainer
            } else {
                MaterialTheme.colorScheme.onSurfaceVariant
            },
        )
    }
}
