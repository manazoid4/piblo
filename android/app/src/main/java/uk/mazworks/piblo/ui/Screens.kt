@file:OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class, androidx.compose.material3.ExperimentalMaterial3Api::class)

package uk.mazworks.piblo.ui

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.Uri
import android.os.BatteryManager
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
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.text.KeyboardOptions
import androidx.core.app.NotificationManagerCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import uk.mazworks.piblo.BuildConfig
import uk.mazworks.piblo.PibloApp
import uk.mazworks.piblo.data.Note
import uk.mazworks.piblo.data.Prefs
import uk.mazworks.piblo.data.Reminder
import uk.mazworks.piblo.hub.LinkState
import uk.mazworks.piblo.proto.CoreClient
import uk.mazworks.piblo.proto.CoreHttpException
import uk.mazworks.piblo.proto.Priority
import uk.mazworks.piblo.system.FindPhone
import uk.mazworks.piblo.system.Notifier
import uk.mazworks.piblo.system.ReminderScheduler
import java.io.IOException
import java.text.DateFormat
import java.util.Calendar
import java.util.Date

private fun app(ctx: Context) = ctx.applicationContext as PibloApp
private val timeFmt: DateFormat get() = DateFormat.getTimeInstance(DateFormat.SHORT)
private val dateTimeFmt: DateFormat get() = DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT)

@Composable
private fun Section(title: String, content: @Composable () -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            content()
        }
    }
}

@Composable
private fun SwitchRow(label: String, detail: String?, checked: Boolean, tag: String, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(label)
            if (detail != null) Text(detail, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Switch(checked, onChange, Modifier.testTag(tag))
    }
}

// ---------------------------------------------------------------- Home

@Composable
fun rememberBattery(): Int {
    val ctx = LocalContext.current
    val v by produceState(-1) {
        while (true) {
            val i = ctx.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
            val level = i?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
            val scale = i?.getIntExtra(BatteryManager.EXTRA_SCALE, 100) ?: 100
            value = if (level >= 0 && scale > 0) level * 100 / scale else -1
            delay(30_000)
        }
    }
    return v
}

@Composable
fun HomeScreen() {
    val ctx = LocalContext.current
    val a = app(ctx)
    val state by a.hub.state.collectAsStateWithLifecycle()
    val battery = rememberBattery()
    LazyColumn(Modifier.padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(12.dp), contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 16.dp)) {
        item { if (state.link == LinkState.ONLINE) LiveScreen() else PibloScreen(state, battery) }
        item { PibloButtons(state, a.hub::answer, { FindPhone.stop(ctx) }) }
        item {
            Section("Status") {
                val link = when (state.link) {
                    LinkState.SIMULATOR -> "Using the built-in simulator"
                    LinkState.CONNECTING -> "Connecting to your computer..."
                    LinkState.ONLINE -> "Connected to your computer"
                    LinkState.OFFLINE -> "Can't reach your computer. Trying again..."
                    LinkState.AUTH_FAILED -> "Your computer didn't accept the pairing. Connect again."
                }
                Text(link, Modifier.testTag("status_link"))
                Text("Phone battery: ${if (battery >= 0) "$battery%" else "unknown"}")
                if (state.link == LinkState.ONLINE) Text("Your computer's agent: ${when (state.agent) { "needs_you" -> "waiting for you"; "working" -> "working"; else -> "idle" }}")
            }
        }
        item {
            Section("Try it (demo, stays on this phone)") {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    AssistChip({ a.hub.localApproval("Run shell command", "rm -rf build/ && npm run build") }, { Text("Approval") }, Modifier.testTag("demo_approval"))
                    AssistChip({ a.hub.localAlert("Build failed", "3 tests failing on main", Priority.URGENT, "sla", "demo") }, { Text("Urgent alert") }, Modifier.testTag("demo_alert"))
                    AssistChip({ a.hub.localAlert("Reminder", "Stand up and stretch", Priority.HIGH, "reminder", "demo") }, { Text("Reminder") })
                    AssistChip({ a.hub.localAlert("Task done", "Agent finished the refactor", Priority.LOW, "task_done", "demo") }, { Text("Task done") })
                    AssistChip({ FindPhone.start(ctx) }, { Text("Find my phone") }, Modifier.testTag("demo_find"))
                }
            }
        }
        item { Text("Recent activity", style = MaterialTheme.typography.titleMedium) }
        if (state.activity.isEmpty()) item { Text("Nothing yet. Try a demo above.", color = MaterialTheme.colorScheme.onSurfaceVariant) }
        items(state.activity.take(15)) { Text("${timeFmt.format(Date(it.atWallMs))}  ${it.text}", style = MaterialTheme.typography.bodyMedium) }
    }
}

// ---------------------------------------------------------------- Messages

@Composable
fun MessagesScreen() {
    val a = app(LocalContext.current)
    val msgs by a.db.dao().messages().collectAsStateWithLifecycle(emptyList())
    val scope = rememberCoroutineScope()
    LazyColumn(Modifier.padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(8.dp), contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 16.dp)) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Everything shown on Piblo", Modifier.weight(1f), style = MaterialTheme.typography.titleMedium)
                if (msgs.isNotEmpty()) TextButton({ scope.launch(Dispatchers.IO) { a.db.dao().clearMessages() } }) { Text("Clear") }
            }
        }
        if (msgs.isEmpty()) item { Text("No messages yet. Reminders, alerts and approvals will show up here.", color = MaterialTheme.colorScheme.onSurfaceVariant) }
        items(msgs, key = { it.id }) {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(12.dp)) {
                    Text(it.title, style = MaterialTheme.typography.titleSmall)
                    if (it.body.isNotBlank()) Text(it.body)
                    Text("${it.source}  ${dateTimeFmt.format(Date(it.atMs))}  ${it.priority}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
        }
    }
}

// ---------------------------------------------------------------- My Tools

private val reminderChoices: List<Pair<String, (Long) -> Long>> = listOf(
    "in 5 min" to { n -> n + 5 * 60_000L },
    "in 30 min" to { n -> n + 30 * 60_000L },
    "in 1 hour" to { n -> n + 60 * 60_000L },
    "tomorrow 9:00" to { n ->
        Calendar.getInstance().apply { timeInMillis = n; add(Calendar.DAY_OF_YEAR, 1); set(Calendar.HOUR_OF_DAY, 9); set(Calendar.MINUTE, 0); set(Calendar.SECOND, 0) }.timeInMillis
    },
)

@Composable
fun ToolsScreen() {
    val ctx = LocalContext.current
    val a = app(ctx)
    val dao = a.db.dao()
    val scope = rememberCoroutineScope()
    val notes by dao.notes().collectAsStateWithLifecycle(emptyList())
    val reminders by dao.reminders().collectAsStateWithLifecycle(emptyList())
    val state by a.hub.state.collectAsStateWithLifecycle()
    var noteText by remember { mutableStateOf("") }
    var remText by remember { mutableStateOf("") }
    var noteSending by remember { mutableStateOf(false) }
    var noteMsg by remember { mutableStateOf<String?>(null) }
    // Notification permission is asked only when the person first sets a reminder or timer.
    val askNotif = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {}
    fun ensureNotifPermission() { if (Build.VERSION.SDK_INT >= 33 && !Notifier.canNotify(ctx)) askNotif.launch(Manifest.permission.POST_NOTIFICATIONS) }
    fun addReminder(text: String, at: Long, kind: String) {
        ensureNotifPermission()
        scope.launch(Dispatchers.IO) {
            val id = dao.insert(Reminder(text = text.trim().take(200), atMs = at, kind = kind))
            ReminderScheduler.schedule(ctx, Reminder(id, text.trim(), at, kind))
        }
    }

    LazyColumn(Modifier.padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(12.dp), contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 16.dp)) {
        item {
            Section("Timers") {
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    listOf(1, 5, 10, 25).forEach { m ->
                        AssistChip({ addReminder("$m minute timer", System.currentTimeMillis() + m * 60_000L, "timer") }, { Text("$m min") }, Modifier.testTag("timer_$m"))
                    }
                }
            }
        }
        item {
            Section("Reminders") {
                OutlinedTextField(remText, { remText = it.take(200) }, Modifier.fillMaxWidth().testTag("reminder_text"), label = { Text("Remind me to...") }, singleLine = true)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    reminderChoices.forEach { (label, at) ->
                        AssistChip({ if (remText.isNotBlank()) { addReminder(remText, at(System.currentTimeMillis()), "reminder"); remText = "" } }, { Text(label) }, Modifier.testTag("remind_$label"))
                    }
                }
                if (!ReminderScheduler.canBeExact(ctx)) {
                    Text("Reminders may arrive a few minutes late until you allow exact alarms.", style = MaterialTheme.typography.bodySmall)
                    TextButton({ ctx.startActivity(Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM, Uri.parse("package:${ctx.packageName}")).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }) { Text("Allow exact reminders") }
                }
                reminders.forEach { r ->
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(r.text)
                            Text((if (r.done) "Done  " else "") + dateTimeFmt.format(Date(r.atMs)), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        TextButton({ scope.launch(Dispatchers.IO) { ReminderScheduler.cancel(ctx, r.id); dao.delete(r) } }) { Text("Delete") }
                    }
                }
            }
        }
        item {
            Section("Notes") {
                OutlinedTextField(noteText, { noteText = it }, Modifier.fillMaxWidth().testTag("note_text"), label = { Text("Write a note") })
                Button({ val t = noteText.trim(); if (t.isNotEmpty()) { scope.launch(Dispatchers.IO) { if (dao.noteCount() < uk.mazworks.piblo.data.MAX_NOTES) dao.insert(Note(text = t.take(2000), createdAt = System.currentTimeMillis())) }; noteText = "" } }, Modifier.testTag("note_add")) { Text("Save note") }
                noteMsg?.let { Text(it, Modifier.testTag("note_msg"), style = MaterialTheme.typography.bodySmall) }
                notes.forEach { n ->
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Text(n.text, Modifier.weight(1f))
                        TextButton({ if (!noteSending) { noteSending = true; scope.launch(Dispatchers.IO) { noteMsg = sendNoteToComputer(a, n.text); noteSending = false } } }, Modifier.testTag("note_send_${n.id}"), enabled = !noteSending) { Text("Send to computer") }
                        TextButton({ scope.launch(Dispatchers.IO) { dao.delete(n) } }) { Text("Delete") }
                    }
                }
            }
        }
        item { PhoneKeyboardSection(a, state.link == LinkState.ONLINE) }
    }
}

/** Files a phone note on the connected computer through the share queue (same path as Android Share), so it also survives being offline. */
private suspend fun sendNoteToComputer(a: PibloApp, text: String): String {
    return try {
        val p = a.settings.now()
        if (!(p.hasComputer && p.mode == Prefs.Mode.COMPUTER)) return "Connect your computer first (Connections tab)."
        val dao = a.db.dao()
        if (dao.shareQueueCount() >= uk.mazworks.piblo.data.MAX_SHARE_QUEUE) return "Too many items waiting to send. Try again in a moment."
        val title = "Note from phone"
        if (!uk.mazworks.piblo.data.hasQueuedShare(dao.pendingShares(), text, title))
            dao.enqueueShare(uk.mazworks.piblo.data.ShareEntry(text = text, title = title, url = "", createdAt = System.currentTimeMillis(), idempotencyId = java.util.UUID.randomUUID().toString()))
        a.requestShareDrain()
        "Note will be filed on your computer as soon as it is connected."
    } catch (e: kotlinx.coroutines.CancellationException) { throw e }
    catch (_: Exception) { "Couldn't send. Try again." }
}

// ---------------------------------------------------------------- Phone keyboard

@Composable
private fun PhoneKeyboardSection(a: PibloApp, online: Boolean) {
    val scope = rememberCoroutineScope()
    var text by remember { mutableStateOf("") }
    var mode by remember { mutableStateOf("insert") }
    var sending by remember { mutableStateOf(false) }
    var result by remember { mutableStateOf<String?>(null) }
    // A "pending" send may still land on Piblo; resending the same text reuses its id so it is typed once.
    var pendingSend by remember { mutableStateOf<Triple<String, String, String>?>(null) }

    fun send() {
        val t = text.trim()
        if (t.isEmpty() || sending) return
        sending = true; result = null
        val id = pendingSend?.takeIf { it.first == t && it.second == mode }?.third ?: java.util.UUID.randomUUID().toString()
        scope.launch {
            val status = withContext(Dispatchers.IO) { a.sendText(t, mode, id) }
            pendingSend = if (status == "pending") Triple(t, mode, id) else null
            result = when (status) {
                "inserted" -> "Text sent to Piblo."
                "no_field" -> "No text field is focused on Piblo. Open a note or message there first."
                "too_long" -> "That text is too long for the field on Piblo."
                "unsupported" -> "Piblo can only show English letters, numbers and basic symbols. Remove emoji, accents or other alphabets and try again."
                "no_computer" -> "Connect your computer first (Connections tab)."
                "busy" -> "Piblo is busy. Try again in a moment."
                "send_failed" -> "Couldn't reach your computer."
                "pending" -> "No answer from Piblo yet — it may be away."
                else -> "Piblo said: $status"
            }
            if (status == "inserted") text = ""
            sending = false
        }
    }

    Section("Type on Piblo") {
        Text("Use your phone's keyboard to type into whatever field is focused on Piblo. The text is sent as plain characters — it can never run a shortcut or approve anything.")
        OutlinedTextField(text, { text = it.take(500) }, Modifier.fillMaxWidth().testTag("phone_text"), label = { Text("Message") }, minLines = 2)
        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            listOf("insert" to "Type into field", "replace" to "Replace field", "send" to "Type, then press Enter on Piblo").forEach { (m, label) ->
                AssistChip({ mode = m }, { Text(label) }, Modifier.testTag("mode_$m"))
                if (mode == m) Text("✓", color = MaterialTheme.colorScheme.primary)
            }
        }
        Button(::send, Modifier.testTag("phone_send"), enabled = !sending && text.isNotBlank()) { Text(if (sending) "Sending..." else "Send to Piblo") }
        if (!online) Text("Not connected to your computer. Phone typing needs a connected computer.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        result?.let { Text(it, Modifier.testTag("phone_result"), color = if (it.startsWith("Text sent") || it.startsWith("Piblo said: inserted")) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface) }
    }
}

// ---------------------------------------------------------------- Connections

@Composable
fun ConnectionsScreen() {
    val ctx = LocalContext.current
    val a = app(ctx)
    val scope = rememberCoroutineScope()
    val prefs by a.settings.prefs.collectAsStateWithLifecycle(Prefs())
    val state by a.hub.state.collectAsStateWithLifecycle()
    var addr by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var token by remember { mutableStateOf("") }
    var showToken by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var msg by remember { mutableStateOf<String?>(null) }

    fun connect() {
        val base = CoreClient.normalizeBase(addr)
        if (base == null) { msg = "That address doesn't look right. Try something like 192.168.1.20 or http://my-pc:8787."; return }
        if (code.isBlank() && token.isBlank()) { msg = "Enter the pairing code, or open \"I already have a token\"."; return }
        busy = true; msg = null
        scope.launch {
            msg = withContext(Dispatchers.IO) {
                try {
                    val t = if (code.isNotBlank()) CoreClient.claim(base, code, BuildConfig.VERSION_NAME) else token.trim()
                    CoreClient(base, t, BuildConfig.VERSION_NAME).summary()   // proves the token works before we save it
                    a.settings.saveComputer(base, t)
                    code = ""; token = ""
                    null
                } catch (e: CoreHttpException) {
                    when {
                        e.status == 400 && e.detail == "invalid_or_expired_code" -> "That code didn't work. Codes last 5 minutes and work once. Ask your computer for a new one."
                        e.status == 401 -> "Your computer didn't accept that token."
                        e.status == 429 -> "Too many tries. Wait a minute and try again."
                        else -> "Your computer answered with an error (${e.status} ${e.detail})."
                    }
                } catch (e: IOException) { "Can't reach $base. Is Core running, and is this phone on the same Wi-Fi?" }
                catch (e: Exception) { "Something went wrong: ${e.message}" }
            }
            busy = false
        }
    }

    LazyColumn(Modifier.padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(12.dp), contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 16.dp)) {
        item {
            Section("Built-in simulator") {
                Text("A pretend Piblo on this phone. Works with no computer and no internet.")
                if (prefs.mode == Prefs.Mode.SIMULATOR) Text("In use", color = MaterialTheme.colorScheme.primary)
                else OutlinedButton({ scope.launch { a.settings.useSimulator() } }, Modifier.testTag("use_simulator")) { Text("Use simulator") }
            }
        }
        item {
            Section("Connect your computer") {
                if (prefs.hasComputer) {
                    Text("Saved: ${prefs.coreUrl}")
                    Text(if (prefs.mode == Prefs.Mode.COMPUTER) "In use (${state.link.name.lowercase().replace('_', ' ')})" else "Saved but not in use", Modifier.testTag("computer_state"))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        if (prefs.mode != Prefs.Mode.COMPUTER) Button({ scope.launch { a.settings.useComputer() } }) { Text("Use computer") }
                        OutlinedButton({ scope.launch { a.settings.forgetComputer() } }, Modifier.testTag("forget_computer")) { Text("Forget this computer") }
                    }
                } else {
                    Text("Your computer runs Piblo Core. Enter its address and a pairing code. Your computer shows the code when you pair a new device. This phone and the computer need to be on the same Wi-Fi.")
                }
                OutlinedTextField(addr, { addr = it }, Modifier.fillMaxWidth().testTag("core_addr"), label = { Text("Computer address") }, placeholder = { Text("192.168.1.20") }, singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri))
                OutlinedTextField(code, { code = it.take(12) }, Modifier.fillMaxWidth().testTag("core_code"), label = { Text("Pairing code") }, singleLine = true)
                TextButton({ showToken = !showToken }) { Text("I already have a token") }
                if (showToken) OutlinedTextField(token, { token = it }, Modifier.fillMaxWidth().testTag("core_token"), label = { Text("Token") }, singleLine = true, visualTransformation = PasswordVisualTransformation())
                Button(::connect, Modifier.testTag("core_connect"), enabled = !busy) { Text(if (busy) "Connecting..." else "Connect") }
                msg?.let { Text(it, color = MaterialTheme.colorScheme.error, modifier = Modifier.testTag("connect_error")) }
                Text("Traffic to your computer is plain Wi-Fi HTTP on your home network, like the Piblo device itself. Away from home use the https address of your Core.", style = MaterialTheme.typography.bodySmall)
            }
        }
        item {
            Section("Piblo device") {
                Text("Coming soon. Connecting this phone straight to a Piblo over Bluetooth needs a firmware update that isn't released yet.")
            }
        }
    }
}

// ---------------------------------------------------------------- Settings

private data class AppRow(val pkg: String, val label: String)

@Composable
fun SettingsScreen() {
    val ctx = LocalContext.current
    val a = app(ctx)
    val scope = rememberCoroutineScope()
    val prefs by a.settings.prefs.collectAsStateWithLifecycle(Prefs())
    val listenerOn = NotificationManagerCompat.getEnabledListenerPackages(ctx).contains(ctx.packageName)
    var showApps by remember { mutableStateOf(false) }
    val apps by produceState(emptyList<AppRow>(), showApps) {
        if (showApps) value = withContext(Dispatchers.IO) {
            val pm = ctx.packageManager
            pm.queryIntentActivities(Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER), 0)
                .map { AppRow(it.activityInfo.packageName, it.loadLabel(pm).toString()) }.filter { it.pkg != ctx.packageName }.distinctBy { it.pkg }.sortedBy { it.label.lowercase() }
        }
    }
    val askNotif = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {}

    LazyColumn(Modifier.padding(horizontal = 16.dp), verticalArrangement = Arrangement.spacedBy(12.dp), contentPadding = androidx.compose.foundation.layout.PaddingValues(vertical = 16.dp)) {
        item {
            Section("Privacy") {
                Text("No accounts. No cloud. Notes, reminders and messages stay on this phone. The only place this app talks to is the computer you connect, and only when you connect one.")
            }
        }
        item {
            Section("Send phone notifications to Piblo") {
                SwitchRow("Forward notifications", "Off until you turn it on. Only apps you pick below are sent. Title and text go to your connected computer, or to the simulator if none is connected.", prefs.forwardNotifications, "fwd_switch") { on ->
                    scope.launch { a.settings.setForward(on) }
                    if (on) {
                        if (!listenerOn) ctx.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                    }
                }
                if (prefs.forwardNotifications) {
                    Text(if (listenerOn) "Notification access: allowed" else "Notification access: not allowed yet. Android needs you to switch it on for Piblo.")
                    if (!listenerOn) TextButton({ ctx.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) }) { Text("Open Android settings") }
                    TextButton({ showApps = !showApps }, Modifier.testTag("pick_apps")) { Text(if (showApps) "Hide app list" else "Choose apps (${prefs.forwardPackages.size} selected)") }
                    if (showApps) apps.forEach { row ->
                        SwitchRow(row.label, null, row.pkg in prefs.forwardPackages, "app_${row.pkg}") { on -> scope.launch { a.settings.setPackage(row.pkg, on) } }
                    }
                }
            }
        }
        item {
            Section("Permissions") {
                SwitchRow("Keep watching when the app is closed", "Shows a small always-on notification so approvals can reach you. Needs a connected computer.", prefs.watchInBackground, "bg_switch") { on ->
                    scope.launch { a.settings.setBackground(on) }
                    if (on && Build.VERSION.SDK_INT >= 33 && !Notifier.canNotify(ctx)) askNotif.launch(Manifest.permission.POST_NOTIFICATIONS)
                }
                Text("Exact reminder times: ${if (ReminderScheduler.canBeExact(ctx)) "allowed" else "not allowed (reminders can be a few minutes late)"}")
                Text("Notifications: ${if (Notifier.canNotify(ctx)) "allowed" else "not allowed yet (asked when you first set a reminder)"}")
            }
        }
        item {
            Section("Find my phone") {
                Text("When your Piblo or your computer asks, this phone rings loudly until you stop it.")
                Button({ FindPhone.start(ctx) }, Modifier.testTag("settings_find")) { Text("Test the alarm") }
            }
        }
        item {
            Section("About") {
                Text("Piblo Companion ${BuildConfig.VERSION_NAME}")
                Text("Speaks piblo-proto v1 (Piblo Core /buddy routes). Not yet tested with a physical Piblo device.")
            }
        }
    }
}
