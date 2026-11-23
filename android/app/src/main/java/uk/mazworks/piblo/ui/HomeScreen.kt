package uk.mazworks.piblo.ui

import android.content.Context
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import uk.mazworks.piblo.BuildConfig
import uk.mazworks.piblo.PibloApp
import uk.mazworks.piblo.hub.Answer
import uk.mazworks.piblo.hub.LinkState
import uk.mazworks.piblo.proto.Api
import uk.mazworks.piblo.proto.CoreClient
import uk.mazworks.piblo.proto.DeviceStatus
import uk.mazworks.piblo.proto.PromisesToday
import uk.mazworks.piblo.proto.TodayPage

private fun app(ctx: Context) = ctx.applicationContext as PibloApp

/**
 * Home: the real Piblo. Live device status, the one approval that needs the person,
 * today's promises and the latest results. No demo content.
 */
@Composable
fun HomeScreen(openLive: () -> Unit, onConnect: () -> Unit, openCraft: () -> Unit, openFind: () -> Unit = {}) {
    val a = app(LocalContext.current)
    val scope = rememberCoroutineScope()
    val state by a.hub.state.collectAsStateWithLifecycle()
    val prefs by a.settings.prefs.collectAsStateWithLifecycle(initialValue = uk.mazworks.piblo.data.Prefs())
    val device by a.deviceStatus.collectAsStateWithLifecycle()
    val deviceErr by a.deviceError.collectAsStateWithLifecycle()
    val online = state.link == LinkState.ONLINE
    val tailnet = CoreClient.isTailnetAddress(prefs.coreUrl)
    var promises by remember(prefs.coreUrl, prefs.tokenBlob) { mutableStateOf<PromisesToday?>(null) }
    var today by remember(prefs.coreUrl, prefs.tokenBlob) { mutableStateOf<TodayPage?>(null) }
    var updateConfirm by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    // Promises and today's counters refresh on their own; neither may ever spin forever.
    LaunchedEffect(online, prefs.coreUrl, prefs.tokenBlob) {
        if (!online) return@LaunchedEffect
        while (true) {
            val t = a.transport
            if (t != null) {
                withContext(Dispatchers.IO) {
                    try { promises = Api.promisesToday(t.get("/promises/today")) } catch (_: Exception) { }
                    try { today = Api.today(t.get("/buddy/today")) } catch (_: Exception) { }
                }
            }
            delay(10_000)
        }
    }

    Column(
        Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("Home", style = MaterialTheme.typography.headlineLarge, fontWeight = FontWeight.Bold)
            Text(
                when {
                    online && tailnet -> "Connected through Tailscale"
                    online -> "Connected to your computer"
                    prefs.useSimulator -> "Simulator"
                    else -> "Your Piblo and what needs you"
                },
                color = Muted, style = MaterialTheme.typography.bodyMedium,
            )
        }

        if (online) {
            DeviceCard(device, deviceErr, busy, onUpdate = { updateConfirm = true })
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(openLive, modifier = Modifier.testTag("open_live")) { Text("Live View") }
                OutlinedButton(openCraft, modifier = Modifier.testTag("open_craft")) { Text("PibloCraft") }
                OutlinedButton(openFind, modifier = Modifier.testTag("open_find")) { Text("Find devices") }
            }
        } else if (!prefs.useSimulator) {
            Section(when {
                !prefs.hasComputer -> "Connect your computer"
                state.link == LinkState.CONNECTING -> "Connecting to Piblo Core"
                state.link == LinkState.AUTH_FAILED -> "Pair your phone again"
                else -> "Computer unreachable"
            }) {
                Text(when {
                    !prefs.hasComputer -> "Pair with Piblo Core to see your device and approvals."
                    state.link == LinkState.AUTH_FAILED -> "Piblo Core rejected the saved pairing."
                    tailnet -> "Make sure Tailscale is connected on this phone and Piblo Core is running on your computer."
                    else -> "Check that Piblo Core is running. Use your home Wi-Fi or your computer's Tailscale address."
                }, style = MaterialTheme.typography.bodyMedium, color = Muted)
                if (!prefs.hasComputer || state.link == LinkState.AUTH_FAILED) {
                    Button(onConnect, modifier = Modifier.testTag("home_connect")) { Text("Connection settings") }
                } else {
                    OutlinedButton(a::reconnect, enabled = state.link != LinkState.CONNECTING) { Text("Check again") }
                    if (tailnet) OpenTailscaleButton()
                    TextButton(onConnect) { Text("Connection settings") }
                }
            }
        } else {
            OutlinedButton(openLive, modifier = Modifier.testTag("open_live")) { Text("Live View") }
        }

        val head = state.head
        if (online || head != null) Section("Waiting for you") {
            if (head == null) {
                Text("Nothing waiting", color = Muted, style = MaterialTheme.typography.bodyMedium)
            } else {
                Text(head.title, fontWeight = FontWeight.Bold)
                Text(head.body, style = MaterialTheme.typography.bodyMedium)
                Text(head.origin.name.lowercase().replace('_', ' ') + " request", style = MaterialTheme.typography.bodySmall, color = Muted)
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Button({ a.hub.answer(Answer.APPROVE) }, modifier = Modifier.testTag("approve")) { Text("Approve") }
                    OutlinedButton({ a.hub.answer(Answer.REJECT) }, modifier = Modifier.testTag("reject")) { Text("Reject") }
                }
                if (head.canSnooze) TextButton({ a.hub.answer(Answer.SNOOZE) }, modifier = Modifier.testTag("snooze")) { Text("Snooze 10 min") }
            }
        }

        if (online) PromisesCard(promises, busy, onReload = {
            scope.launch(Dispatchers.IO) {
                val t = a.transport ?: return@launch
                try { promises = Api.promisesToday(t.get("/promises/today")) } catch (_: Exception) { }
            }
        })

        if (online) today?.let { t ->
            Section("Today") {
                Labeled("Done today", "${t.done}")
                Labeled("Waiting on you", "${t.needs}")
                Labeled("Approved / denied", "${t.approved} / ${t.denied}")
                t.lastText?.let { Text("Last: $it", style = MaterialTheme.typography.bodySmall, color = Muted) }
            }
        }

        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
    }

    if (updateConfirm) {
        val d = device
        AlertDialog(
            onDismissRequest = { updateConfirm = false },
            title = { Text("Update Piblo?") },
            text = { Text("Install ${d?.hubVersion ?: "the new build"}? Piblo restarts when it finishes. Keep it on Wi-Fi with battery above 30% or charging.") },
            confirmButton = {
                TextButton({
                    updateConfirm = false
                    scope.launch(Dispatchers.IO) {
                        try { a.transport?.deviceAction("update"); a.refreshDevice() } catch (e: Exception) { error = "Update didn't start: ${e.message}" }
                    }
                }) { Text("Install") }
            },
            dismissButton = { TextButton({ updateConfirm = false }) { Text("Not now") } },
        )
    }
}

@Composable
private fun DeviceCard(d: DeviceStatus?, err: String?, busy: Boolean, onUpdate: () -> Unit) {
    Section("Your Piblo") {
        if (d == null) {
            val msg = when {
                err != null -> "Core is connected. Piblo isn't responding."
                else -> "Checking Piblo…"
            }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                if (err == null) CircularProgressIndicator(strokeWidth = 2.dp)
                Text(msg, style = MaterialTheme.typography.bodyMedium, color = Muted)
            }
        } else {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(d.name, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                Pill(if (d.online) "Online" else "Offline", if (d.online) Teal else Red)
            }
            d.battery?.let { Labeled("Battery", "$it%${if (d.charging) " · charging" else ""}") }
            Labeled("Software", d.version.ifBlank { "unknown" })
            if (d.app.isNotBlank()) Labeled("Showing", d.app)
            Labeled("Wi-Fi", when {
                d.wifiSsid.isNotBlank() -> d.wifiSsid
                d.wifiConnected -> "connected"
                else -> "not connected"
            })
            d.hubVersion?.let { Labeled("Ready to install", it) }
            d.lastStage?.let { Labeled("Last update", it + (d.lastError?.let { e -> " ($e)" } ?: "")) }
            if (d.online && d.updateAvailable) {
                Button(onUpdate, enabled = !busy, modifier = Modifier.testTag("update_firmware")) { Text("Update firmware") }
            }
        }
    }
}

@Composable
private fun PromisesCard(data: PromisesToday?, busy: Boolean, onReload: () -> Unit) {
    var note by remember { mutableStateOf("") }
    var candidate by remember { mutableStateOf<Pair<String, String>?>(null) }  // due, label
    var message by remember { mutableStateOf<String?>(null) }
    val a = app(LocalContext.current)
    val scope = rememberCoroutineScope()

    Section("Today's promises") {
        if (data == null || data.empty) {
            Text(if (data == null) "Checking promises…" else "No promises today", color = Muted, style = MaterialTheme.typography.bodyMedium)
        } else {
            data.cards.forEach { c ->
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.padding(end = 8.dp)) {
                        Text(c.title, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium)
                        Text("${c.state} · ${c.whenText}", style = MaterialTheme.typography.bodySmall, color = Muted)
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        if (c.actions.contains("done")) TextButton({ scope.launch(Dispatchers.IO) { a.transport?.post("/promises/${c.id}/done", null); onReload() } }) { Text("Done") }
                        if (c.actions.contains("later")) TextButton({ scope.launch(Dispatchers.IO) { a.transport?.post("/promises/${c.id}/later", "{}"); onReload() } }) { Text("Later") }
                        if (c.actions.contains("undo")) TextButton({ scope.launch(Dispatchers.IO) { a.transport?.post("/promises/${c.id}/undo", null); onReload() } }) { Text("Undo") }
                    }
                }
            }
        }

        OutlinedTextField(
            note, { note = it }, label = { Text("New promise") },
            placeholder = { Text("Call the dentist tomorrow at 10:00") }, singleLine = true,
            modifier = Modifier.fillMaxWidth().testTag("promise_note"),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Button(
                {
                    scope.launch(Dispatchers.IO) {
                        try {
                            val raw = a.transport?.post("/promises/propose", JSONObject().put("note", note.trim()).toString())
                                ?: throw IllegalStateException("not_connected")
                            val o = JSONObject(raw)
                            val c = o.optJSONArray("candidates")?.optJSONObject(0)
                            if (c != null) candidate = c.optString("due") to c.optString("label")
                            else message = o.optString("message").ifBlank { "No date found in that note." }
                        } catch (e: Exception) { message = "Couldn't read that promise. Is your computer connected?" }
                    }
                },
                enabled = note.isNotBlank() && !busy,
                modifier = Modifier.testTag("promise_add"),
            ) { Text("Add") }
        }
        candidate?.let { (due, label) ->
            Text("Save \"$label\" for $due?", style = MaterialTheme.typography.bodyMedium)
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Button({
                    scope.launch(Dispatchers.IO) {
                        try {
                            a.transport?.post("/promises/confirm", JSONObject().put("note", note.trim()).put("due", due).put("confirmed", true).toString())
                            note = ""; candidate = null; onReload()
                        } catch (e: Exception) { message = "Couldn't save that promise." }
                    }
                }, modifier = Modifier.testTag("promise_confirm")) { Text("Save") }
                OutlinedButton({ candidate = null }) { Text("Cancel") }
            }
        }
        message?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
    }
}
