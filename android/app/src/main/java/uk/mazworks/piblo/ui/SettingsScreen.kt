package uk.mazworks.piblo.ui

import android.Manifest
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
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
import androidx.core.app.NotificationManagerCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import uk.mazworks.piblo.BuildConfig
import uk.mazworks.piblo.PibloApp
import uk.mazworks.piblo.data.MAX_NOTES
import uk.mazworks.piblo.data.Note
import uk.mazworks.piblo.data.Prefs
import uk.mazworks.piblo.data.Reminder
import uk.mazworks.piblo.hub.LinkState
import uk.mazworks.piblo.pairing.DevicePairing
import uk.mazworks.piblo.pairing.DiscoveredCore
import uk.mazworks.piblo.pairing.LanDiscovery
import uk.mazworks.piblo.proto.Api
import uk.mazworks.piblo.proto.CoreClient
import uk.mazworks.piblo.proto.FlowSettings
import uk.mazworks.piblo.proto.FlowStatus
import uk.mazworks.piblo.proto.FlowWord
import uk.mazworks.piblo.system.FindPhone
import uk.mazworks.piblo.system.Notifier
import uk.mazworks.piblo.system.ReminderScheduler
import java.text.DateFormat
import java.util.Calendar
import java.util.Date

private fun app(ctx: Context) = ctx.applicationContext as PibloApp
private val timeFmt: DateFormat get() = DateFormat.getTimeInstance(DateFormat.SHORT)
private val dateTimeFmt: DateFormat get() = DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)

private data class AppRow(val pkg: String, val label: String)

private val reminderChoices: List<Pair<String, (Long) -> Long>> = listOf(
    "in 5 min" to { n -> n + 5 * 60_000L },
    "in 30 min" to { n -> n + 30 * 60_000L },
    "in 1 hour" to { n -> n + 60 * 60_000L },
    "tomorrow 9:00" to { n ->
        Calendar.getInstance().apply { timeInMillis = n; add(Calendar.DAY_OF_YEAR, 1); set(Calendar.HOUR_OF_DAY, 9); set(Calendar.MINUTE, 0); set(Calendar.SECOND, 0) }.timeInMillis
    },
)

/** Settings: connection, notifications, dictation, tools, developer and about. Every switch shows its true state. */
@Composable
fun SettingsScreen(openLive: () -> Unit) {
    val ctx = LocalContext.current
    val a = app(ctx)
    val scope = rememberCoroutineScope()
    val prefs by a.settings.prefs.collectAsStateWithLifecycle(Prefs())
    val state by a.hub.state.collectAsStateWithLifecycle()
    val health by a.health.collectAsStateWithLifecycle()
    val device by a.deviceStatus.collectAsStateWithLifecycle()
    var listenerOn by remember { mutableStateOf(NotificationManagerCompat.getEnabledListenerPackages(ctx).contains(ctx.packageName)) }
    var showApps by remember { mutableStateOf(false) }
    var apps by remember { mutableStateOf<List<AppRow>>(emptyList()) }
    var flowStatus by remember { mutableStateOf<FlowStatus?>(null) }
    var flowSettings by remember { mutableStateOf<FlowSettings?>(null) }
    var words by remember { mutableStateOf<List<FlowWord>>(emptyList()) }
    var word by remember { mutableStateOf("") }
    val askNotif = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {}

    // Notification access can change in Android settings while this screen is open.
    LaunchedEffect(Unit) { while (true) { listenerOn = NotificationManagerCompat.getEnabledListenerPackages(ctx).contains(ctx.packageName); delay(2000) } }
    LaunchedEffect(showApps) {
        if (showApps) apps = withContext(Dispatchers.IO) { withContextSafe(ctx) }
    }
    LaunchedEffect(Unit) {
        while (true) {
            val t = a.transport
            if (t != null) {
                withContext(Dispatchers.IO) {
                    try { flowStatus = Api.flowStatus(t.get("/flow/status")) } catch (_: Exception) { }
                    try { flowSettings = Api.flowSettings(t.get("/flow/settings")) } catch (_: Exception) { }
                    try { words = Api.flowDictionary(t.get("/flow/dictionary")) } catch (_: Exception) { }
                }
            }
            delay(10_000)
        }
    }

    Column(
        Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        ConnectSection(openLive)

        Section("Notifications") {
            Text("Forward other apps' notifications to your computer, and keep Piblo watching when the app is closed.", style = MaterialTheme.typography.bodySmall, color = Muted)
            SwitchRow("Forward notifications", if (listenerOn) "Notification access: allowed" else "Notification access: not allowed yet", prefs.forwardNotifications, "fwd_switch") { on ->
                scope.launch { a.settings.setForward(on) }
                if (on && !listenerOn) ctx.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }
            if (prefs.forwardNotifications) {
                if (!listenerOn) TextButton({ ctx.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }) { Text("Open Android notification access settings") }
                TextButton({ showApps = !showApps }, modifier = Modifier.testTag("pick_apps")) { Text(if (showApps) "Hide app list" else "Choose apps (${prefs.forwardPackages.size} selected)") }
                if (showApps) apps.forEach { row ->
                    SwitchRow(row.label, null, row.pkg in prefs.forwardPackages, "app_${row.pkg}") { on -> scope.launch { a.settings.setPackage(row.pkg, on) } }
                }
            }
            SwitchRow("Keep watching when the app is closed", "A small always-on notification lets approvals reach you.", prefs.watchInBackground, "bg_switch") { on ->
                scope.launch { a.settings.setBackground(on) }
                if (on && Build.VERSION.SDK_INT >= 33 && !Notifier.canNotify(ctx)) askNotif.launch(Manifest.permission.POST_NOTIFICATIONS)
            }
            Text("Notifications permission: ${if (Notifier.canNotify(ctx)) "allowed" else "not allowed yet"}")
            Text("Exact reminders: ${if (ReminderScheduler.canBeExact(ctx)) "allowed" else "not allowed"}")
            if (!ReminderScheduler.canBeExact(ctx)) {
                TextButton({
                    ctx.startActivity(Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM, Uri.parse("package:${ctx.packageName}")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                }) { Text("Allow exact alarms in Android settings") }
            }
        }

        DictationSection(flowStatus, flowSettings, words, word, { word = it }, onAdd = {
            scope.launch(Dispatchers.IO) { try { a.transport?.post("/flow/dictionary", JSONObject().put("word", word.trim()).toString()); word = "" } catch (_: Exception) {} }
        }, onRemove = { w ->
            scope.launch(Dispatchers.IO) { try { a.transport?.delete("/flow/dictionary/${java.net.URLEncoder.encode(w, "UTF-8")}") } catch (_: Exception) {} }
        }, onSettings = { patch ->
            scope.launch(Dispatchers.IO) { try { a.transport?.patch("/flow/settings", JSONObject(patch).toString()) } catch (_: Exception) {} }
        })

        ToolsSection(openLive)

        Section("Developer") {
            SwitchRow("Built-in simulator", "A pretend Piblo on this phone, for testing with no computer. Off unless you turn it on.", prefs.developer, "developer_switch") { on ->
                scope.launch { a.settings.setDeveloper(on); if (on) a.settings.useSimulator() else a.settings.useComputer() }
            }
            if (prefs.developer) Text("Simulator is available. Normal use still connects to your real computer.", style = MaterialTheme.typography.bodySmall, color = Muted)
        }

        Section("About") {
            Labeled("App", "Piblo Companion ${BuildConfig.VERSION_NAME}")
            Labeled("Computer (Core)", health?.version?.ifBlank { null } ?: "not connected")
            Labeled("Piblo device", device?.version?.ifBlank { null } ?: "offline")
            Labeled("Staged firmware", device?.hubVersion ?: "none")
            Text("Speaks piblo-proto v1. Notes, reminders and messages stay on this phone.", style = MaterialTheme.typography.bodySmall, color = Muted)
        }
    }
}

private fun withContextSafe(ctx: Context): List<AppRow> = try {
    val pm = ctx.packageManager
    pm.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0)
        .map { AppRow(it.activityInfo.packageName, it.loadLabel(pm).toString()) }
        .filter { it.pkg != ctx.packageName }.distinctBy { it.pkg }.sortedBy { it.label.lowercase() }
} catch (_: Exception) { emptyList() }

@Composable
private fun ConnectSection(openLive: () -> Unit) {
    val ctx = LocalContext.current
    val a = app(ctx)
    val scope = rememberCoroutineScope()
    val prefs by a.settings.prefs.collectAsStateWithLifecycle(Prefs())
    val state by a.hub.state.collectAsStateWithLifecycle()
    var change by remember { mutableStateOf(false) }
    var cores by remember { mutableStateOf<List<DiscoveredCore>>(emptyList()) }
    var searching by remember { mutableStateOf(false) }
    var addr by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var msg by remember { mutableStateOf<String?>(null) }

    Section("Connection") {
        if (prefs.hasComputer) {
            Labeled("Saved", prefs.coreUrl)
            Labeled("State", when (state.link) {
                LinkState.ONLINE -> "connected"
                LinkState.CONNECTING -> "connecting"
                LinkState.OFFLINE -> "can't reach it"
                LinkState.AUTH_FAILED -> "pairing rejected"
                LinkState.SIMULATOR -> "simulator"
            })
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton({ change = !change }, modifier = Modifier.testTag("change_computer")) { Text(if (change) "Hide" else "Change computer") }
                OutlinedButton({ scope.launch { a.settings.forgetComputer() } }, modifier = Modifier.testTag("forget_computer")) { Text("Forget") }
            }
        } else {
            Text("This phone isn't connected to a computer.", fontWeight = FontWeight.SemiBold)
        }
        if (change || !prefs.hasComputer) {
            Text("Find your computer on this Wi-Fi, tap it, then press Allow on the computer.", style = MaterialTheme.typography.bodySmall, color = Muted)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button({
                    searching = true
                    scope.launch(Dispatchers.IO) { val f = try { LanDiscovery.search() } catch (_: Exception) { emptyList() }; cores = f; searching = false }
                }, enabled = !searching && !busy, modifier = Modifier.testTag("find_computer")) { Text(if (searching) "Searching…" else "Find my computer") }
            }
            cores.forEach { c ->
                Button({
                    busy = true; msg = null
                    scope.launch(Dispatchers.IO) {
                        try {
                            val (name, id) = a.settings.ensureDeviceIdentity()
                            val base = "http://${c.host}:${c.port}"
                            val pairing = uk.mazworks.piblo.pairing.DevicePairing(BuildConfig.VERSION_NAME)
                            val info = pairing.request(base, id, name)
                            var waited = 0
                            while (waited < 120) {
                                when (val o = pairing.poll(base, info.requestId)) {
                                    is uk.mazworks.piblo.pairing.PairOutcome.Approved -> { a.savePairing(base, o.token); return@launch }
                                    uk.mazworks.piblo.pairing.PairOutcome.Denied, uk.mazworks.piblo.pairing.PairOutcome.Expired -> {
                                        msg = "That request ended. Try again."; return@launch
                                    }
                                    uk.mazworks.piblo.pairing.PairOutcome.Pending, is uk.mazworks.piblo.pairing.PairOutcome.Network -> Unit
                                }
                                waited += 2; delay(2000)
                            }
                            msg = "Timed out waiting for Allow."
                        } catch (e: Exception) { msg = "Couldn't reach that computer." }
                        finally { busy = false }
                    }
                }, enabled = !busy, modifier = Modifier.testTag("settings_core_${c.host}")) { Text("Piblo Core on ${c.name}") }
            }
            OutlinedTextField(addr, { addr = it }, label = { Text("Computer address") }, placeholder = { Text("192.168.1.20:8787") }, singleLine = true, modifier = Modifier.fillMaxWidth().testTag("settings_addr"))
            OutlinedTextField(code, { code = it.take(12) }, label = { Text("Pairing code") }, singleLine = true, modifier = Modifier.fillMaxWidth().testTag("settings_code"))
            Button({
                busy = true; msg = null
                scope.launch(Dispatchers.IO) {
                    msg = try {
                        val base = CoreClient.normalizeBase(addr) ?: throw IllegalArgumentException("bad_address")
                        val token = DevicePairing(BuildConfig.VERSION_NAME).manualToken(base, code)
                        CoreClient(base, token, BuildConfig.VERSION_NAME).summary()
                        a.savePairing(base, token); null
                    } catch (e: Exception) { "That didn't work. Check the address and code." }
                    busy = false
                }
            }, enabled = !busy && addr.isNotBlank() && code.isNotBlank(), modifier = Modifier.testTag("settings_connect")) { Text(if (busy) "Connecting…" else "Connect") }
            msg?.let { Text(it, color = MaterialTheme.colorScheme.error) }
        }
        Text("Traffic to your computer is plain Wi-Fi HTTP on your home network, like the Piblo device itself. Away from home use the https address of your Core.", style = MaterialTheme.typography.bodySmall, color = Muted)
    }
}

@Composable
private fun SwitchRow(label: String, detail: String?, checked: Boolean, tag: String, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(label)
            if (detail != null) Text(detail, style = MaterialTheme.typography.bodySmall, color = Muted)
        }
        Switch(checked, onChange, Modifier.testTag(tag))
    }
}

@Composable
private fun DictationSection(
    status: FlowStatus?,
    settings: FlowSettings?,
    words: List<FlowWord>,
    word: String,
    onWord: (String) -> Unit,
    onAdd: () -> Unit,
    onRemove: (String) -> Unit,
    onSettings: (Map<String, String>) -> Unit,
) {
    Section("Dictation") {
        val s = settings
        Text(
            when {
                status == null -> "Reading dictation history…"
                status.count == 0 -> "No dictations yet."
                else -> "${status.count} dictations · average ${status.avgMs} ms${status.errors.lastOrNull()?.let { " · last problem: $it" } ?: ""}"
            },
            style = MaterialTheme.typography.bodySmall, color = Muted,
        )
        Text("How spoken text arrives", style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("paste" to "Paste", "type" to "Type").forEach { (v, label) ->
                if (s?.inputMode == v) Button({ onSettings(mapOf("input_mode" to v)) }) { Text(label) }
                else OutlinedButton({ onSettings(mapOf("input_mode" to v)) }) { Text(label) }
            }
        }
        SwitchRow("Press Enter after pasting", null, s?.pressEnter ?: false, "flow_enter") { on -> onSettings(mapOf("press_enter" to on.toString())) }
        Text("Clean up the words", style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("auto" to "Auto", "rules" to "On this PC", "groq" to "Cloud").forEach { (v, label) ->
                if (s?.cleanup == v) Button({ onSettings(mapOf("cleanup" to v)) }) { Text(label) }
                else OutlinedButton({ onSettings(mapOf("cleanup" to v)) }) { Text(label) }
            }
        }
        Text("Speech recognition", style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("auto" to "Auto", "parakeet" to "Parakeet", "whisper" to "Whisper").forEach { (v, label) ->
                if (s?.stt == v) Button({ onSettings(mapOf("stt" to v)) }) { Text(label) }
                else OutlinedButton({ onSettings(mapOf("stt" to v)) }) { Text(label) }
            }
        }
        OutlinedTextField(word, onWord, label = { Text("Add a word, or Term: misheard") }, singleLine = true, modifier = Modifier.fillMaxWidth().testTag("flow_word"))
        Button(onAdd, enabled = word.isNotBlank(), modifier = Modifier.testTag("flow_add")) { Text("Add word") }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            words.take(40).forEach { w ->
                TextButton({ onRemove(w.term) }) { Text(w.term) }
            }
        }
    }
}

@Composable
private fun ToolsSection(openLive: () -> Unit) {
    val ctx = LocalContext.current
    val a = app(ctx)
    val dao = a.db.dao()
    val scope = rememberCoroutineScope()
    val notes by dao.notes().collectAsStateWithLifecycle(emptyList())
    val reminders by dao.reminders().collectAsStateWithLifecycle(emptyList())
    var noteText by remember { mutableStateOf("") }
    var remText by remember { mutableStateOf("") }
    val askNotif = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {}

    Section("Tools") {
        Text("Notes, reminders and timers live on this phone.", style = MaterialTheme.typography.bodySmall, color = Muted)
        OutlinedTextField(remText, { remText = it.take(200) }, label = { Text("Remind me to…") }, singleLine = true, modifier = Modifier.fillMaxWidth().testTag("settings_reminder_text"))
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            reminderChoices.forEach { (label, at) ->
                OutlinedButton({
                    if (remText.isNotBlank()) {
                        if (Build.VERSION.SDK_INT >= 33 && !Notifier.canNotify(ctx)) askNotif.launch(Manifest.permission.POST_NOTIFICATIONS)
                        scope.launch(Dispatchers.IO) {
                            val due = at(System.currentTimeMillis())
                            val text = remText.trim()
                            val id = dao.insert(Reminder(text = text, atMs = due, kind = "reminder"))
                            ReminderScheduler.schedule(ctx, Reminder(id, text, due, "reminder"))
                        }
                        remText = ""
                    }
                }) { Text(label) }
            }
        }
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf(1, 5, 10, 25).forEach { m ->
                OutlinedButton({
                    if (Build.VERSION.SDK_INT >= 33 && !Notifier.canNotify(ctx)) askNotif.launch(Manifest.permission.POST_NOTIFICATIONS)
                    scope.launch(Dispatchers.IO) {
                        val at = System.currentTimeMillis() + m * 60_000L
                        val id = dao.insert(Reminder(text = "$m minute timer", atMs = at, kind = "timer"))
                        ReminderScheduler.schedule(ctx, Reminder(id, "$m minute timer", at, "timer"))
                    }
                }) { Text("$m min timer") }
            }
        }
        reminders.take(10).forEach { r ->
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(r.text, Modifier.weight(1f))
                Text(dateTimeFmt.format(Date(r.atMs)), style = MaterialTheme.typography.bodySmall, color = Muted)
                TextButton({ scope.launch(Dispatchers.IO) { ReminderScheduler.cancel(ctx, r.id); dao.delete(r) } }) { Text("Delete") }
            }
        }
        OutlinedTextField(noteText, { noteText = it }, label = { Text("Write a note") }, modifier = Modifier.fillMaxWidth().testTag("settings_note_text"))
        Button({
            val t = noteText.trim()
            if (t.isNotEmpty()) { scope.launch(Dispatchers.IO) { if (dao.noteCount() < MAX_NOTES) dao.insert(Note(text = t.take(2000), createdAt = System.currentTimeMillis())) }; noteText = "" }
        }, modifier = Modifier.testTag("settings_note_add")) { Text("Save note") }
        notes.take(10).forEach { n ->
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(n.text, Modifier.weight(1f))
                TextButton({ scope.launch(Dispatchers.IO) { dao.delete(n) } }) { Text("Delete") }
            }
        }
        TextButton({ FindPhone.start(ctx) }, modifier = Modifier.testTag("settings_find")) { Text("Test Find my phone") }
        TextButton(openLive) { Text("Open Live View") }
    }
}
