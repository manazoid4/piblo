package uk.mazworks.piblo.ui

import android.content.Context
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
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
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import uk.mazworks.piblo.PibloApp
import uk.mazworks.piblo.hub.Answer
import uk.mazworks.piblo.hub.ItemKind
import uk.mazworks.piblo.proto.Api
import uk.mazworks.piblo.proto.FocusState
import uk.mazworks.piblo.proto.HaltState
import uk.mazworks.piblo.proto.NeedItem
import uk.mazworks.piblo.proto.NeedsPage
import uk.mazworks.piblo.proto.TodayPage

private fun app(ctx: Context) = ctx.applicationContext as PibloApp

private val QUICK = listOf(
    "Sleep" to "sleep", "Lock" to "lock", "Restart" to "restart", "Shut down" to "shutdown",
    "Play/Pause" to "play-pause", "Next track" to "next", "Previous track" to "previous",
    "Mute" to "vol-mute", "Volume up" to "vol-up", "Volume down" to "vol-down",
    "Brighter" to "bright-up", "Dimmer" to "bright-down",
)

/** Actions: quiet hours, approvals, needs-you, today, focus and the PC quick keys. */
@Composable
fun ActionsScreen(openLive: () -> Unit, onConnect: () -> Unit = {}) {
    val a = app(LocalContext.current)
    val scope = rememberCoroutineScope()
    val state by a.hub.state.collectAsStateWithLifecycle()
    var needs by remember { mutableStateOf<NeedsPage?>(null) }
    var today by remember { mutableStateOf<TodayPage?>(null) }
    var focus by remember { mutableStateOf<FocusState?>(null) }
    var halt by remember { mutableStateOf<HaltState?>(null) }
    var task by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var busy by remember { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        while (true) {
            val t = a.transport
            if (t != null) {
                withContext(Dispatchers.IO) {
                    try { needs = Api.needs(t.get("/needs")) } catch (_: Exception) { }
                    try { today = Api.today(t.get("/buddy/today")) } catch (_: Exception) { }
                    try { focus = Api.focus(t.get("/focus/state")) } catch (_: Exception) { }
                    try { halt = Api.halt(t.get("/buddy/halt")) } catch (_: Exception) { }
                }
            }
            delay(8_000)
        }
    }

    Column(
        Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        ComputerOffline(onConnect)
        Section("Quiet") {
            if (halt?.halted == true) {
                Text("Approvals are paused on Piblo.", fontWeight = FontWeight.SemiBold)
                Text("Your computer still works. Approvals wait until you resume.", style = MaterialTheme.typography.bodySmall, color = Muted)
                Button({
                    scope.launch(Dispatchers.IO) {
                        try { a.transport?.setHalt(false); halt = a.transport?.haltState() } catch (_: Exception) { }
                    }
                }, modifier = Modifier.testTag("resume")) { Text("Resume approvals") }
            } else {
                Text("Pause approvals on Piblo for a while.", style = MaterialTheme.typography.bodyMedium)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton({
                        scope.launch(Dispatchers.IO) { try { a.transport?.setHalt(true, 15); halt = a.transport?.haltState() } catch (_: Exception) {} }
                    }, modifier = Modifier.testTag("halt_15")) { Text("15 min") }
                    OutlinedButton({
                        scope.launch(Dispatchers.IO) { try { a.transport?.setHalt(true, 60); halt = a.transport?.haltState() } catch (_: Exception) {} }
                    }, modifier = Modifier.testTag("halt_60")) { Text("1 hour") }
                }
                Text("Or set a longer pause from the Piblo screen on your computer.", style = MaterialTheme.typography.bodySmall, color = Muted)
            }
        }

        Section("Approvals") {
            val approvals = state.queue.filter { it.kind == ItemKind.APPROVAL }
            if (approvals.isEmpty()) EmptyState("Nothing waiting", "Agents ask here when they need you to say yes or no.")
            else {
                val head = approvals.first()
                Text(head.title, fontWeight = FontWeight.Bold)
                Text(head.body, style = MaterialTheme.typography.bodyMedium)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button({ a.hub.answer(Answer.APPROVE) }, modifier = Modifier.testTag("actions_allow")) { Text("Approve") }
                    OutlinedButton({ a.hub.answer(Answer.REJECT) }, modifier = Modifier.testTag("actions_deny")) { Text("Reject") }
                }
                if (approvals.size > 1) Text("+${approvals.size - 1} more waiting behind this one", style = MaterialTheme.typography.bodySmall, color = Muted)
            }
        }

        Section("Needs you") {
            val n = needs
            if (n == null) Loading()
            else if (n.items.isEmpty()) EmptyState("Nothing needs you", "Pull requests, reviews and agent questions land here.")
            else n.items.forEach { item ->
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Column(Modifier.padding(end = 8.dp)) {
                        Text(item.title.ifBlank { item.id }, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium)
                        Text("${item.tier} · ${item.project} · ${ago(item.age)}", style = MaterialTheme.typography.bodySmall, color = Muted)
                        if (item.detail.isNotBlank()) Text(item.detail, style = MaterialTheme.typography.bodySmall)
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        TextButton({ scope.launch(Dispatchers.IO) { try { a.transport?.post("/needs/${item.id}/done", null) } catch (_: Exception) {} } }) { Text("Done") }
                        TextButton({ scope.launch(Dispatchers.IO) { try { a.transport?.post("/needs/${item.id}/snooze", null) } catch (_: Exception) {} } }) { Text("Snooze") }
                    }
                }
            }
        }

        Section("Today") {
            val t = today
            if (t == null) Loading()
            else {
                Labeled("Done", "${t.done}")
                Labeled("Waiting", "${t.needs}")
                Labeled("Errors", "${t.errors}")
                Labeled("Approved / denied", "${t.approved} / ${t.denied}")
            }
        }

        Section("Focus sprint") {
            val f = focus
            if (f == null || f.state.isEmpty()) {
                Text("No sprint running.", style = MaterialTheme.typography.bodyMedium)
            } else {
                Text(f.state, fontWeight = FontWeight.SemiBold)
                if (f.task.isNotBlank()) Text(f.task, style = MaterialTheme.typography.bodyMedium)
                if (f.remainingS > 0) Text("${f.remainingS / 60} min left", style = MaterialTheme.typography.bodySmall, color = Muted)
                OutlinedButton({ scope.launch(Dispatchers.IO) { try { a.transport?.post("/focus/end", org.json.JSONObject().put("done", false).toString()) } catch (_: Exception) {} } }, modifier = Modifier.testTag("focus_end")) { Text("End sprint") }
            }
            OutlinedTextField(task, { task = it }, label = { Text("Start a sprint about") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Button({
                scope.launch(Dispatchers.IO) {
                    try { a.transport?.post("/focus/start", org.json.JSONObject().put("task", task.trim()).toString()) } catch (_: Exception) {}
                }
            }, enabled = task.isNotBlank(), modifier = Modifier.testTag("focus_start")) { Text("Start sprint") }
        }

        Section("Quick actions") {
            Text("These control the computer Piblo Core runs on.", style = MaterialTheme.typography.bodySmall, color = Muted)
            QUICK.chunked(3).forEach { row ->
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    row.forEach { (label, action) ->
                        OutlinedButton({
                            scope.launch(Dispatchers.IO) { try { a.transport?.post("/pc/action", org.json.JSONObject().put("action", action).toString()) } catch (_: Exception) {} }
                        }, modifier = Modifier.testTag("quick_$action")) { Text(label) }
                    }
                }
            }
            Button(openLive, modifier = Modifier.testTag("open_live_actions")) { Text("Open Live View") }
        }

        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
    }
}
