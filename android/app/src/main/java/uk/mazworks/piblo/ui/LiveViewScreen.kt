package uk.mazworks.piblo.ui

import android.content.Context
import android.graphics.BitmapFactory
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.FilterQuality
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import uk.mazworks.piblo.PibloApp
import uk.mazworks.piblo.hub.LinkState

private fun app(ctx: Context) = ctx.applicationContext as PibloApp

private val keys = listOf(
    "Esc" to "esc", "Up" to "up", "Down" to "down", "Left" to "left",
    "Right" to "right", "Enter" to "enter", "Back" to "backspace",
)

/** Full-screen Live View: the real Piblo screen, a real keyboard, and the cardputer keys. */
@Composable
fun LiveViewScreen(onClose: () -> Unit) {
    val a = app(LocalContext.current)
    val scope = rememberCoroutineScope()
    val state by a.hub.state.collectAsStateWithLifecycle()
    var frame by remember { mutableStateOf<ImageBitmap?>(null) }
    var unreachable by remember { mutableStateOf(false) }
    var text by remember { mutableStateOf("") }
    var result by remember { mutableStateOf<String?>(null) }
    var sending by remember { mutableStateOf(false) }
    var updateConfirm by remember { mutableStateOf(false) }

    // Frames only while this screen is actually visible.
    val owner = LocalLifecycleOwner.current
    LaunchedEffect(owner) {
        owner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) {
                val t = a.transport
                if (t != null) {
                    try {
                        val bytes = withContext(Dispatchers.IO) { t.screenPng() }
                        frame = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap()
                        unreachable = false
                    } catch (e: kotlinx.coroutines.CancellationException) {
                        throw e
                    } catch (_: Exception) { unreachable = true }
                }
                delay(500)
            }
        }
    }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TextButton(onClose, modifier = Modifier.testTag("live_close")) { Text("← Back") }
            Text("Live View", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            Box(Modifier.weight(1f))
            Pill(if (state.link == LinkState.ONLINE) "Live" else "Offline", if (state.link == LinkState.ONLINE) Teal else Red)
        }

        Box(
            Modifier.fillMaxWidth().aspectRatio(240f / 135f).clip(RoundedCornerShape(14.dp)).background(Navy)
                .border(BorderStroke(3.dp, Yellow), RoundedCornerShape(14.dp)),
        ) {
            frame?.let { Image(it, null, Modifier.fillMaxSize().testTag("live_screen"), contentScale = ContentScale.FillBounds, filterQuality = FilterQuality.None) }
            if (unreachable) {
                Box(Modifier.fillMaxSize().background(Navy.copy(alpha = 0.85f)), contentAlignment = Alignment.Center) {
                    Text("Piblo isn't reachable. Check it's on and on the same Wi-Fi.", color = Cream, modifier = Modifier.testTag("live_unreachable"))
                }
            }
        }

        OutlinedTextField(
            text, { text = it.take(500) },
            label = { Text("Type on Piblo") },
            supportingText = { Text("Uses your phone keyboard. Sent as plain characters.") },
            modifier = Modifier.fillMaxWidth().testTag("live_text"),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button({
                val t = text.trim()
                if (t.isEmpty() || sending) return@Button
                sending = true; result = null
                scope.launch {
                    val status = withContext(Dispatchers.IO) { a.sendText(t, "insert", java.util.UUID.randomUUID().toString()) }
                    result = when (status) {
                        "inserted" -> "Sent to Piblo."
                        "no_field" -> "No text field is focused on Piblo."
                        "too_long" -> "Too long for the field on Piblo."
                        "no_computer" -> "Connect your computer first."
                        else -> "Piblo said: $status"
                    }
                    if (status == "inserted") text = ""
                    sending = false
                }
            }, enabled = !sending && text.isNotBlank(), modifier = Modifier.testTag("live_send")) { Text(if (sending) "Sending…" else "Send") }
            OutlinedButton({ scope.launch(Dispatchers.IO) { try { a.transport?.deviceAction("reboot") } catch (_: Exception) {} } }, modifier = Modifier.testTag("live_reboot")) { Text("Reboot") }
            OutlinedButton({ updateConfirm = true }, modifier = Modifier.testTag("live_update")) { Text("Update") }
        }
        result?.let { Text(it, style = MaterialTheme.typography.bodySmall) }

        Text("Keys", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            keys.forEach { (label, key) ->
                OutlinedButton({
                    scope.launch(Dispatchers.IO) { try { a.transport?.pressKeys(key) } catch (_: Exception) { unreachable = true } }
                }, modifier = Modifier.testTag("live_key_${label.lowercase()}")) { Text(label) }
            }
        }
    }

    if (updateConfirm) {
        AlertDialog(
            onDismissRequest = { updateConfirm = false },
            title = { Text("Update Piblo?") },
            text = { Text("Piblo restarts when the update finishes. Keep it on Wi-Fi with battery above 30% or charging.") },
            confirmButton = {
                TextButton({
                    updateConfirm = false
                    scope.launch(Dispatchers.IO) { try { a.transport?.deviceAction("update") } catch (_: Exception) {} }
                }) { Text("Install") }
            },
            dismissButton = { TextButton({ updateConfirm = false }) { Text("Not now") } },
        )
    }
}
