package uk.mazworks.piblo.ui

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import uk.mazworks.piblo.PibloApp
import uk.mazworks.piblo.data.Prefs
import uk.mazworks.piblo.proto.FindOverview
import uk.mazworks.piblo.proto.FoundDevice
import uk.mazworks.piblo.proto.NearbyDevice
import uk.mazworks.piblo.proto.Proximity
import uk.mazworks.piblo.proto.addDeviceJson
import uk.mazworks.piblo.proto.parseFindOverview
import uk.mazworks.piblo.proto.sightingsJson
import uk.mazworks.piblo.system.BleScanner

private const val GOOGLE_FIND_HUB_PACKAGE = "com.google.android.apps.adm"

/** "Piblo, find my AirPods": your devices, ring the ones Piblo can ring, hotter/colder with real Bluetooth signal. */
@Composable
fun FindDevicesScreen(onClose: () -> Unit, onConnect: () -> Unit) {
    val context = LocalContext.current
    val app = context.applicationContext as PibloApp
    val prefs by app.settings.prefs.collectAsStateWithLifecycle(initialValue = null)
    val connection = prefs ?: return
    val connected = connection.hasComputer && connection.mode == Prefs.Mode.COMPUTER
    val scope = rememberCoroutineScope()
    val scanner = remember { BleScanner(context) }
    val scanStatus by scanner.status.collectAsStateWithLifecycle()
    val tick by scanner.tick.collectAsStateWithLifecycle()
    var overview by remember { mutableStateOf<FindOverview?>(null) }
    var message by remember { mutableStateOf<String?>(null) }
    var tracking by remember { mutableStateOf<FoundDevice?>(null) }
    var refresh by remember { mutableIntStateOf(0) }
    val observer = "android:" + (connection.deviceName.ifBlank { "phone" })
    val selfId = "phone-" + connection.deviceId.ifBlank { "this" }

    val askBluetooth = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { granted ->
        if (granted.values.all { it }) scanner.start() else message = "Piblo needs Nearby devices permission to listen for your devices."
    }
    DisposableEffect(Unit) { onDispose { scanner.stop() } }

    suspend fun call(block: () -> String): String? = withContext(Dispatchers.IO) {
        val t = app.transport ?: return@withContext null
        try { t.run { block() } } catch (c: CancellationException) { throw c } catch (e: Exception) { message = "Couldn't reach Piblo Core."; null }
    }

    LaunchedEffect(connected, refresh) {
        if (!connected) return@LaunchedEffect
        while (true) {
            call { app.transport!!.get("/finddevices") }?.let { overview = parseFindOverview(it); message = null }
            delay(5_000)
        }
    }
    // While scanning, send what this phone hears to Core every 2 s (Core keeps last-seen and proximity).
    LaunchedEffect(scanStatus) {
        while (scanStatus == BleScanner.Status.SCANNING) {
            delay(2_000)
            val rows = scanner.drain()
            if (rows.isNotEmpty()) call { app.transport!!.post("/finddevices/sightings", sightingsJson(observer, rows)) }
        }
    }

    LazyColumn(
        Modifier.fillMaxSize().safeDrawingPadding().testTag("find_devices"),
        contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text("Find devices", style = MaterialTheme.typography.headlineSmall)
                TextButton(onClose) { Text("Close") }
            }
            Text("Your devices, how close they are, and the quickest way to make them ring.", color = Muted,
                 style = MaterialTheme.typography.bodyMedium)
        }
        if (!connected) {
            item {
                Section("Connect your computer") {
                    Text("Find devices keeps your device list in Piblo Core on your computer.")
                    Button(onConnect) { Text("Connect") }
                }
            }
            return@LazyColumn
        }
        message?.let { item { Text(it, color = Red) } }
        item {
            Section("Listen nearby") {
                Text(when (scanStatus) {
                    BleScanner.Status.SCANNING -> "Listening for Bluetooth. Walk around: the meter gets fuller as you get closer."
                    BleScanner.Status.BLUETOOTH_OFF -> "Bluetooth is off. Turn it on to listen for your devices."
                    BleScanner.Status.NO_PERMISSION -> "Allow Nearby devices so Piblo can hear your devices."
                    BleScanner.Status.UNSUPPORTED -> "This phone has no Bluetooth LE."
                    BleScanner.Status.FAILED -> "Bluetooth scan failed. Turn Bluetooth off and on, then try again."
                    BleScanner.Status.IDLE -> "Only while this screen is open. Piblo never scans in the background."
                }, style = MaterialTheme.typography.bodySmall)
                if (scanStatus == BleScanner.Status.SCANNING) {
                    OutlinedButton({ scanner.stop() }, Modifier.testTag("scan_stop")) { Text("Stop listening") }
                } else {
                    Button({ if (scanner.hasPermission()) scanner.start() else askBluetooth.launch(BleScanner.requiredPermissions()) },
                           Modifier.testTag("scan_start")) { Text("Start listening") }
                }
            }
        }
        tracking?.let { dev ->
            item {
                @Suppress("UNUSED_EXPRESSION") tick   // recompose on every advert
                val address = dev.bluetoothAddresses.firstOrNull() ?: scanner.strongestApple(dev.appleModelId)
                val reading = address?.let { Proximity.of(scanner.rssiHistory(it)) }
                Section("Hotter or colder: ${dev.name}") {
                    if (reading == null) {
                        Text(if (scanStatus == BleScanner.Status.SCANNING) "Not hearing it yet. Keep moving, or open the case / take it out of the drawer."
                             else "Start listening above to use the meter.")
                    } else {
                        LinearProgressIndicator(progress = { reading.fill }, modifier = Modifier.fillMaxWidth())
                        Text(reading.label + when (reading.trend) { "closer" -> ", getting warmer"; "further" -> ", getting colder"; else -> "" },
                             style = MaterialTheme.typography.titleMedium)
                        Text("Roughly ${reading.lowM}-${reading.highM} m. Bluetooth signal is a rough guide" +
                             (if (reading.confident) "." else "; hold still for a moment for a steadier reading."),
                             color = Muted, style = MaterialTheme.typography.bodySmall)
                        if (dev.bluetoothAddresses.isEmpty()) {
                            Text("Following the strongest ${dev.name}-type signal nearby; Piblo can't prove it's yours.",
                                 color = Muted, style = MaterialTheme.typography.bodySmall)
                        }
                    }
                    TextButton({ tracking = null }) { Text("Stop tracking") }
                }
            }
        }
        val devices = overview?.devices.orEmpty()
        if (devices.none { it.id == selfId }) {
            item {
                OutlinedButton({
                    scope.launch {
                        val body = addDeviceJson(connection.deviceName.ifBlank { "My phone" }, "phone",
                                                 mapOf("piblo_phone" to JSONObject()), selfId)
                        call { app.transport!!.post("/finddevices/devices", body) }
                        refresh++
                    }
                }, Modifier.testTag("add_this_phone")) { Text("Add this phone so Piblo can ring it") }
            }
        }
        if (overview != null && devices.isEmpty()) {
            item { EmptyState("No devices yet", "Start listening, then add your AirPods, watch or tag from the list below.") }
        }
        items(devices, key = { it.id }) { dev ->
            DeviceRow(dev, context,
                onRing = { scope.launch { call { app.transport!!.post("/finddevices/devices/${dev.id}/ring") }?.let {
                    message = JSONObject(it).optString("reply").ifBlank { null } } } },
                onTrack = { tracking = dev; if (scanStatus != BleScanner.Status.SCANNING) {
                    if (scanner.hasPermission()) scanner.start() else askBluetooth.launch(BleScanner.requiredPermissions()) } },
                onRemove = { scope.launch { call { app.transport!!.delete("/finddevices/devices/${dev.id}") }; refresh++ } })
        }
        val nearby = overview?.nearby.orEmpty()
        if (nearby.isNotEmpty()) {
            item { Text("Heard nearby", style = MaterialTheme.typography.titleMedium) }
            items(nearby.take(12), key = { it.address + it.name }) { n ->
                NearbyRow(n) {
                    scope.launch {
                        call { app.transport!!.post("/finddevices/devices", nearbyToDevice(n)) }
                        refresh++
                    }
                }
            }
        }
    }
}

@Composable
private fun DeviceRow(dev: FoundDevice, context: Context, onRing: () -> Unit, onTrack: () -> Unit, onRemove: () -> Unit) {
    Section(dev.name) {
        Text(dev.proximityLabel, style = MaterialTheme.typography.bodyMedium)
        dev.lastSeenAgoS?.let { Text("Last heard ${agoText(it)}", color = Muted, style = MaterialTheme.typography.bodySmall) }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (dev.canRing) Button(onRing, Modifier.testTag("ring_${dev.id}")) { Text("Ring") }
            dev.handoff?.let { h ->
                OutlinedButton({ openHandoff(context, h.provider, h.url) }) {
                    Text(if (h.provider == "apple_findmy") "Open Find My" else "Open Find Hub")
                }
            }
            if (dev.kind != "phone" || dev.bluetoothAddresses.isNotEmpty()) TextButton(onTrack) { Text("Hotter/colder") }
        }
        TextButton(onRemove) { Text("Remove", color = Muted) }
    }
}

@Composable
private fun NearbyRow(n: NearbyDevice, onAdd: () -> Unit) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Column(Modifier.weight(1f)) {
            Text(n.label, style = MaterialTheme.typography.bodyMedium)
            Text((if (n.bonded) "Paired with this phone. " else "") + (n.rssi?.let { "Signal $it dBm" } ?: ""),
                 color = Muted, style = MaterialTheme.typography.bodySmall)
        }
        if (n.bonded || n.appleModelId.isNotEmpty()) TextButton(onAdd) { Text("Add") }
    }
}

/** Paired devices are tracked by address; Apple lookalikes by model plus Find My for the real location. */
internal fun nearbyToDevice(n: NearbyDevice): String {
    val apple = n.appleModelId.isNotEmpty()
    val name = n.name.ifBlank { n.label.substringBefore("-type").removePrefix("An ").removePrefix("A ") }.take(60)
    val bt = JSONObject()
    if (n.bonded && n.address.isNotEmpty()) bt.put("addresses", JSONArray().put(n.address))
    if (n.bonded && n.name.isNotEmpty()) bt.put("names", JSONArray().put(n.name))
    if (apple) bt.put("apple_model_id", n.appleModelId)
    val providers = mutableMapOf("bluetooth" to bt)
    if (apple) providers["apple_findmy"] = JSONObject()
    val kind = when {
        Regex("airpods|buds|beats|earbuds", RegexOption.IGNORE_CASE).containsMatchIn(name) -> "earbuds"
        Regex("watch", RegexOption.IGNORE_CASE).containsMatchIn(name) -> "watch"
        Regex("headphone|max|wh-", RegexOption.IGNORE_CASE).containsMatchIn(name) -> "headphones"
        else -> "other"
    }
    return addDeviceJson(name.ifBlank { "Bluetooth device" }, kind, providers)
}

private fun openHandoff(context: Context, provider: String, url: String) {
    val intent = if (provider == "google_find_hub") context.packageManager.getLaunchIntentForPackage(GOOGLE_FIND_HUB_PACKAGE) else null
    try {
        context.startActivity((intent ?: Intent(Intent.ACTION_VIEW, Uri.parse(url))).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    } catch (_: ActivityNotFoundException) { }
}

private fun agoText(s: Long): String = when {
    s < 90 -> "just now"
    s < 3600 -> "${s / 60} min ago"
    s < 172_800 -> "${s / 3600} h ago"
    else -> "${s / 86_400} days ago"
}
