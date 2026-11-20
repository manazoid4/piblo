package uk.mazworks.piblo.ui

import android.os.SystemClock
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.delay
import uk.mazworks.piblo.hub.Answer
import uk.mazworks.piblo.hub.HubState
import uk.mazworks.piblo.hub.Item
import uk.mazworks.piblo.hub.ItemKind
import uk.mazworks.piblo.hub.LinkState
import uk.mazworks.piblo.proto.Priority

private val Mint = Color(0xFF4CE0B3)
private val Coral = Color(0xFFFF6B4A)

/** A 240x135 Piblo screen, scaled to the phone width. Everything is sized in "device pixels" (u = width / 240). */
@Composable
fun PibloScreen(state: HubState, batteryPct: Int, modifier: Modifier = Modifier) {
    var now by remember { mutableLongStateOf(SystemClock.elapsedRealtime()) }
    LaunchedEffect(Unit) { while (true) { delay(500); now = SystemClock.elapsedRealtime() } }
    BoxWithConstraints(modifier.fillMaxWidth().aspectRatio(240f / 135f).clip(RoundedCornerShape(14.dp)).background(Navy).border(BorderStroke(3.dp, Yellow), RoundedCornerShape(14.dp)).testTag("piblo_screen")) {
        val u: Dp = maxWidth / 240
        Column(Modifier.fillMaxSize().padding(horizontal = u * 8, vertical = u * 5)) {
            StatusBar(state.link, batteryPct, u)
            Box(Modifier.weight(1f).fillMaxWidth().padding(top = u * 4), contentAlignment = Alignment.CenterStart) {
                val head = state.head
                if (state.ringing) Ringing(u) else if (head == null) Idle(state, u) else ItemView(head, now, state.queue.size, u)
            }
        }
    }
}

@Composable
private fun StatusBar(link: LinkState, battery: Int, u: Dp) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text("PIBLO", color = Yellow, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold, fontSize = (u.value * 9).sp)
        Box(Modifier.weight(1f))
        val (dot, label) = when (link) {
            LinkState.SIMULATOR -> Mint to "demo"
            LinkState.ONLINE -> Mint to "online"
            LinkState.CONNECTING -> Yellow to "..."
            LinkState.OFFLINE -> Coral to "offline"
            LinkState.AUTH_FAILED -> Coral to "re-pair"
        }
        Box(Modifier.size(u * 5).clip(CircleShape).background(dot))
        Text(" $label  ${battery.coerceAtLeast(0)}%", color = Cream, fontFamily = FontFamily.Monospace, fontSize = (u.value * 8).sp)
    }
}

@Composable
private fun Idle(state: HubState, u: Dp) {
    val line = when (state.agent) { "needs_you" -> "Waiting for you"; "working" -> "Agent working..."; else -> "All quiet" }
    Column(verticalArrangement = Arrangement.spacedBy(u * 3)) {
        Text("(^_^)", color = Mint, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold, fontSize = (u.value * 26).sp)
        Text(line, color = Cream, fontFamily = FontFamily.Monospace, fontSize = (u.value * 11).sp)
    }
}

@Composable
private fun Ringing(u: Dp) {
    Column(verticalArrangement = Arrangement.spacedBy(u * 3)) {
        Text("RINGING", color = Coral, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold, fontSize = (u.value * 20).sp)
        Text("Find my phone is on. Tap Stop below.", color = Cream, fontFamily = FontFamily.Monospace, fontSize = (u.value * 10).sp)
    }
}

@Composable
private fun ItemView(item: Item, now: Long, count: Int, u: Dp) {
    val accent = when { item.kind == ItemKind.APPROVAL -> Coral; item.priority >= Priority.HIGH -> Yellow; else -> Mint }
    val head = if (item.kind == ItemKind.APPROVAL) "APPROVE?" else item.priority.wire.uppercase()
    Column(verticalArrangement = Arrangement.spacedBy(u * 3)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(head, color = accent, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold, fontSize = (u.value * 9).sp)
            Box(Modifier.weight(1f))
            item.deadlineMs?.let { Text("${((it - now) / 1000).coerceAtLeast(0)}s", color = Yellow, fontFamily = FontFamily.Monospace, fontSize = (u.value * 9).sp) }
            if (count > 1) Text("  +${count - 1}", color = Cream, fontFamily = FontFamily.Monospace, fontSize = (u.value * 9).sp)
        }
        Text(item.title, color = Cream, fontFamily = FontFamily.Monospace, fontWeight = FontWeight.Bold, fontSize = (u.value * 13).sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
        if (item.body.isNotBlank()) Text(item.body, color = Cream.copy(alpha = 0.85f), fontFamily = FontFamily.Monospace, fontSize = (u.value * 10).sp, maxLines = 4, overflow = TextOverflow.Ellipsis)
    }
}

/** The three buttons under the screen. Disabled when they would do nothing. */
@Composable
fun PibloButtons(state: HubState, onAnswer: (Answer) -> Unit, onStopRinging: () -> Unit, modifier: Modifier = Modifier) {
    val head = state.head
    if (state.ringing) {
        Button(onStopRinging, modifier.fillMaxWidth().testTag("btn_stop"), colors = ButtonDefaults.buttonColors(containerColor = Coral)) { Text("Stop ringing") }
        return
    }
    val approval = head?.kind == ItemKind.APPROVAL
    Row(modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button({ onAnswer(if (approval) Answer.APPROVE else Answer.ACK) }, Modifier.weight(1f).testTag("btn_approve"), enabled = head != null) { Text(if (approval || head == null) "Approve" else "Done") }
        OutlinedButton({ onAnswer(Answer.REJECT) }, Modifier.weight(1f).testTag("btn_reject"), enabled = approval) { Text("Reject") }
        OutlinedButton({ onAnswer(Answer.SNOOZE) }, Modifier.weight(1f).testTag("btn_snooze"), enabled = head?.canSnooze == true) { Text("Snooze") }
    }
}
