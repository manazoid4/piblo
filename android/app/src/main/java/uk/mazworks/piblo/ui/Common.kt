package uk.mazworks.piblo.ui

import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.TextButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.ui.platform.testTag
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import uk.mazworks.piblo.PibloApp
import uk.mazworks.piblo.data.Prefs
import uk.mazworks.piblo.hub.LinkState
import uk.mazworks.piblo.proto.CoreClient

val Navy = Color(0xFF0B1B3A)
val Navy2 = Color(0xFF13264D)
val Yellow = Color(0xFFFFD23F)
val Cream = Color(0xFFFFF6E9)
val Paper = Color(0xFFFFFBF5)
val Teal = Color(0xFF4CE0B3)
val Red = Color(0xFFE0574B)
val Muted: Color
    @Composable get() = MaterialTheme.colorScheme.onSurfaceVariant

@Composable
fun OpenTailscaleButton() {
    val context = LocalContext.current
    var launchFailed by remember { mutableStateOf(false) }
    TextButton({
        val intent = context.packageManager.getLaunchIntentForPackage("com.tailscale.ipn")
            ?: Intent(Intent.ACTION_VIEW, Uri.parse("https://play.google.com/store/apps/details?id=com.tailscale.ipn"))
        try {
            context.startActivity(intent)
            launchFailed = false
        } catch (_: ActivityNotFoundException) {
            launchFailed = true
        }
    }) { Text("Open Tailscale") }
    if (launchFailed) Text("Open Tailscale from your phone's app list.", style = MaterialTheme.typography.bodySmall)
}

/** True when screens that read from Piblo Core can show live data (connected, or the simulator). */
@Composable
fun computerReachable(): Boolean {
    val a = LocalContext.current.applicationContext as PibloApp
    val state by a.hub.state.collectAsStateWithLifecycle()
    val prefs by a.settings.prefs.collectAsStateWithLifecycle(initialValue = Prefs())
    return state.link == LinkState.ONLINE || prefs.useSimulator
}

/** "Reading…" while connected; while the computer is unreachable, say so instead of spinning forever. */
@Composable
fun Loading(text: String = "Reading…") {
    Text(if (computerReachable()) text else "Needs your computer. It can't be reached right now.", color = Muted, style = MaterialTheme.typography.bodySmall)
}

/** Shown at the top of every tab while Piblo Core can't be reached: why, and the one-tap fixes. Nothing when connected. */
@Composable
fun ComputerOffline(onConnect: () -> Unit) {
    val a = LocalContext.current.applicationContext as PibloApp
    val state by a.hub.state.collectAsStateWithLifecycle()
    val prefs by a.settings.prefs.collectAsStateWithLifecycle(initialValue = Prefs())
    if (state.link == LinkState.ONLINE || prefs.useSimulator) return
    val tailnet = CoreClient.isTailnetAddress(prefs.coreUrl)
    Section(when {
        !prefs.hasComputer -> "Connect your computer"
        state.link == LinkState.CONNECTING -> "Connecting to Piblo Core"
        state.link == LinkState.AUTH_FAILED -> "Pair your phone again"
        else -> "Can't reach your computer"
    }, Modifier.testTag("computer_offline")) {
        Text(when {
            !prefs.hasComputer -> "Pair with Piblo Core to see your device and approvals."
            state.link == LinkState.AUTH_FAILED -> "Piblo Core rejected the saved pairing."
            tailnet -> "Make sure Tailscale is connected on this phone and Piblo Core is running on your computer."
            else -> "Away from home? Turn on Tailscale on this phone and your computer, then use its Tailscale address. At home, check Piblo Core is running."
        }, style = MaterialTheme.typography.bodyMedium, color = Muted)
        if (!prefs.hasComputer || state.link == LinkState.AUTH_FAILED) {
            Button(onConnect, modifier = Modifier.testTag("home_connect")) { Text("Connection settings") }
        } else {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                OutlinedButton(a::reconnect, enabled = state.link != LinkState.CONNECTING) { Text("Check again") }
                OpenTailscaleButton()
            }
            TextButton(onConnect) { Text("Connection settings") }
        }
    }
}

@Composable
fun Pill(text: String, color: Color = Teal, modifier: Modifier = Modifier) {
    Box(
        modifier
            .background(color.copy(alpha = 0.16f), RoundedCornerShape(50))
            .padding(horizontal = 10.dp, vertical = 4.dp),
    ) { Text(text, color = color, style = MaterialTheme.typography.labelMedium, fontWeight = FontWeight.SemiBold) }
}

@Composable
fun Section(
    title: String? = null,
    modifier: Modifier = Modifier,
    content: @Composable () -> Unit,
) {
    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            if (title != null) Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            content()
        }
    }
}

@Composable
fun EmptyState(title: String, body: String, modifier: Modifier = Modifier) {
    Column(modifier.fillMaxWidth().padding(vertical = 18.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Text(title, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.width(4.dp))
        Text(body, style = MaterialTheme.typography.bodySmall, color = Muted, textAlign = androidx.compose.ui.text.style.TextAlign.Center)
    }
}

@Composable
fun Labeled(label: String, value: String, modifier: Modifier = Modifier) {
    Row(modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
        Text(label, style = MaterialTheme.typography.bodyMedium, color = Muted)
        Text(value, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
    }
}

/** "just now", "5 min ago", "3 h ago", "2 d ago" from a Unix timestamp in seconds. */
fun ago(ts: Long): String {
    if (ts <= 0) return ""
    val s = (System.currentTimeMillis() / 1000 - ts).coerceAtLeast(0)
    return when {
        s < 60 -> "just now"
        s < 3600 -> "${s / 60} min ago"
        s < 86_400 -> "${s / 3600} h ago"
        else -> "${s / 86_400} d ago"
    }
}

/** Simple vertical list spacing used by every screen. */
@Composable
fun VStack(modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(12.dp)) { content() }
}

@Composable
fun ScreenPadding(content: @Composable (PaddingValues) -> Unit) {
    Column(Modifier.fillMaxWidth()) { content(PaddingValues(0.dp)) }
}
