package com.example.helloandroidxr.ui.components

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp

data class AnalysisLensOption(
    val id: String,
    val label: String,
)

val DefaultAnalysisLenses =
    listOf(
        AnalysisLensOption("medical", "Medical"),
        AnalysisLensOption("manufacturing", "Manufacturing"),
        AnalysisLensOption("animator", "Animator"),
        AnalysisLensOption("low_vision_accessibility", "Low Vision"),
    )

@Composable
fun AnalysisLensControls(
    activeLensIds: Set<String>,
    onToggleLens: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(20.dp),
        tonalElevation = 2.dp,
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 14.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(
                text = "Specialization Lenses",
                style = MaterialTheme.typography.titleMedium,
                fontWeight = FontWeight.SemiBold,
            )
            Text(
                text = "Base heuristics always stay on. Toggle extra lenses before or during the session to steer the analysis.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            DefaultAnalysisLenses.chunked(2).forEach { row ->
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    row.forEach { lens ->
                        val enabled = lens.id in activeLensIds
                        val background =
                            if (enabled) {
                                MaterialTheme.colorScheme.primaryContainer
                            } else {
                                MaterialTheme.colorScheme.surfaceVariant
                            }
                        val contentColor =
                            if (enabled) {
                                MaterialTheme.colorScheme.onPrimaryContainer
                            } else {
                                MaterialTheme.colorScheme.onSurfaceVariant
                            }
                        Text(
                            text = lens.label,
                            color = contentColor,
                            style = MaterialTheme.typography.labelLarge,
                            modifier =
                                Modifier
                                    .weight(1f)
                                    .clip(RoundedCornerShape(999.dp))
                                    .background(background)
                                    .clickable { onToggleLens(lens.id) }
                                    .padding(horizontal = 14.dp, vertical = 12.dp),
                        )
                    }
                }
            }
        }
    }
}
