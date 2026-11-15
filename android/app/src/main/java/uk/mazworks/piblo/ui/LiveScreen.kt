@file:OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)

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
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.Text
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
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import uk.mazworks.piblo.PibloApp
import uk.mazworks.piblo.hub.LinkState

private fun app(ctx: Context) = ctx.applicationContext as PibloApp

private val Navy = Color(0xFF0B1B3A)
private val Yellow = Color(0xFFFFD23F)
private val Cream = Color(0xFFFFF6E9)

private const val UNREACHABLE = "Piblo isn't reachable. Check it's on and on the same Wi-Fi."
private const val RECONNECTING = "Reconnecting…"

private val keys = listOf(
    "Esc" to "esc",
    "Up" to "up",
    "Down" to "down",
    "Left" to "left",
    "Right" to "right",
    "Enter" to "enter",
    "Back" to "backspace",
)

/** The real Piblo screen streamed from Core, plus the cardputer keys to control it. Shown only when ONLINE. */
@Composable
fun LiveScreen() {
    val a = app(LocalContext.current)
    val scope = rememberCoroutineScope()
    val state by a.hub.state.collectAsStateWithLifecycle()
    var frame by remember { mutableStateOf<ImageBitmap?>(null) }
    var unreachable by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        while (true) {
            val t = a.transport
            if (t != null) {
                try {
                    val bytes = withContext(Dispatchers.IO) { t.screenPng() }
                    frame = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.asImageBitmap()
                    unreachable = false
                } catch (_: Exception) {
                    unreachable = true   // 503 or any error: keep the last frame
                }
            }
            delay(500)
        }
    }

    Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Box(Modifier.fillMaxWidth().aspectRatio(240f / 135f).clip(RoundedCornerShape(14.dp)).background(Navy).border(BorderStroke(3.dp, Yellow), RoundedCornerShape(14.dp))) {
            frame?.let {
                Image(it, null, Modifier.fillMaxSize().testTag("live_screen"), contentScale = ContentScale.FillBounds, filterQuality = FilterQuality.None)
            }
            if (unreachable) {
                val isReconnecting = state.link != LinkState.ONLINE && frame != null
                val msg = if (isReconnecting) RECONNECTING else UNREACHABLE
                val overlay = if (isReconnecting) Navy.copy(alpha = 0.7f) else Navy
                Box(Modifier.fillMaxSize().background(overlay), contentAlignment = Alignment.Center) {
                    Text(msg, Modifier.testTag(if (isReconnecting) "live_reconnecting" else "live_unreachable"), color = Cream)
                }
            }
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            keys.forEach { (label, key) ->
                Button({ scope.launch(Dispatchers.IO) { a.transport?.pressKeys(key) } }, Modifier.testTag("live_key_${label.lowercase()}")) { Text(label) }
            }
        }
    }
}
