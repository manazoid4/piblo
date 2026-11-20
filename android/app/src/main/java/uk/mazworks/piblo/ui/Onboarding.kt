package uk.mazworks.piblo.ui

import android.content.Context
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
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
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import uk.mazworks.piblo.BuildConfig
import uk.mazworks.piblo.PibloApp
import uk.mazworks.piblo.pairing.DevicePairing
import uk.mazworks.piblo.pairing.DiscoveredCore
import uk.mazworks.piblo.pairing.LanDiscovery
import uk.mazworks.piblo.pairing.PairOutcome
import uk.mazworks.piblo.proto.CoreClient
import uk.mazworks.piblo.proto.PairRequestInfo

private fun app(ctx: Context) = ctx.applicationContext as PibloApp

/**
 * First launch: find Piblo Core on this Wi-Fi, tap it, then approve on the computer.
 * Nothing is typed and nothing is approved without a human pressing Allow on the PC.
 */
@Composable
fun OnboardingScreen(onDone: () -> Unit) {
    val a = app(LocalContext.current)
    val scope = rememberCoroutineScope()
    var cores by remember { mutableStateOf<List<DiscoveredCore>>(emptyList()) }
    var searching by remember { mutableStateOf(true) }
    var busy by remember { mutableStateOf(false) }
    var message by remember { mutableStateOf<String?>(null) }
    var pairing by remember { mutableStateOf<PairRequestInfo?>(null) }
    var pairingName by remember { mutableStateOf("") }
    var baseUrl by remember { mutableStateOf("") }
    var manual by remember { mutableStateOf(false) }
    var job by remember { mutableStateOf<Job?>(null) }

    fun search() {
        searching = true
        message = null
        scope.launch(Dispatchers.IO) {
            var found = try { LanDiscovery.search() } catch (_: Exception) { emptyList() }
            if (found.isEmpty()) {
                // Not on the same Wi-Fi (e.g. Tailscale): try the Core this phone used before.
                val known = a.settings.prefs.first().coreUrl
                if (known.isNotBlank()) found = listOfNotNull(LanDiscovery.probe(known, BuildConfig.VERSION_NAME))
            }
            cores = found
            searching = false
        }
    }

    LaunchedEffect(Unit) { search() }

    Column(
        Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text("Welcome to Piblo", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
        Text(
            "Find your computer on this Wi-Fi, tap it, then press Allow on the computer. " +
                "Nothing is typed and nothing is approved without you pressing Allow there.",
            style = MaterialTheme.typography.bodyMedium,
        )

        val p = pairing
        if (p != null) {
            Section("Approve on your computer") {
                Text("${pairingName.ifEmpty { "Piblo" }} wants to connect.", fontWeight = FontWeight.SemiBold)
                Text("Check it shows", style = MaterialTheme.typography.bodyMedium)
                Text(
                    DevicePairing.formatCode(p.code),
                    style = MaterialTheme.typography.displaySmall,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier.testTag("pair_code"),
                )
                Text("Then press Allow on the computer.", style = MaterialTheme.typography.bodySmall, color = Muted)
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    CircularProgressIndicator(Modifier.padding(2.dp), strokeWidth = 2.dp)
                    Text("Waiting for Allow…", style = MaterialTheme.typography.bodyMedium, color = Muted)
                }
                OutlinedButton(
                    {
                        job?.cancel(); pairing = null; busy = false; message = null
                    },
                    enabled = !busy,
                    modifier = Modifier.testTag("pair_cancel"),
                ) { Text("Cancel") }
            }
        } else if (manual) {
            ManualConnect(
                busy = busy,
                message = message,
                onConnect = { url, code ->
                    busy = true; message = null
                    scope.launch {
                        try {
                            val token = withContext(Dispatchers.IO) { DevicePairing(BuildConfig.VERSION_NAME).manualToken(url, code) }
                            a.savePairing(url, token)
                            onDone()
                        } catch (e: Exception) {
                            message = "That didn't work. Check the address and code, then try again."
                        } finally { busy = false }
                    }
                },
                onBack = { manual = false; message = null },
            )
        } else {
            Section("Your computer") {
                when {
                    searching -> Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        CircularProgressIndicator(strokeWidth = 2.dp)
                        Text("Searching this Wi-Fi…", color = Muted)
                    }
                    cores.isEmpty() -> Text(
                        "No computer found. Make sure Piblo Core is running, then search again.\n\n" +
                            "Not on the same Wi-Fi? Tap \"Enter address and code\". Tailscale addresses (100.x.x.x:8787) work.",
                        color = Muted,
                        textAlign = TextAlign.Center,
                    )
                    else -> cores.forEach { c ->
                        Button(
                            {
                                pairingName = c.name
                                baseUrl = "http://${c.host}:${c.port}"
                                job = scope.launch {
                                    busy = true; message = null
                                    try {
                                        val (name, id) = withContext(Dispatchers.IO) { a.settings.ensureDeviceIdentity() }
                                        val pairer = DevicePairing(BuildConfig.VERSION_NAME)
                                        val info = pairer.request(baseUrl, id, name)
                                        pairingName = name
                                        pairing = info
                                        // Wait up to Core's 120 s expiry.
                                        var waited = 0
                                        while (waited < 120) {
                                            when (val o = pairer.poll(baseUrl, info.requestId)) {
                                                is PairOutcome.Approved -> {
                                                    a.savePairing(baseUrl, o.token)
                                                    onDone(); return@launch
                                                }
                                                PairOutcome.Denied, PairOutcome.Expired -> {
                                                    message = "That request ended. Press connect on your phone again."
                                                    busy = false; return@launch
                                                }
                                                is PairOutcome.Network -> Unit // transient: keep waiting
                                                PairOutcome.Pending -> Unit
                                            }
                                            waited += 2
                                            delay(2000)
                                        }
                                        message = "Timed out waiting for Allow. Try again."
                                        busy = false
                                    } catch (e: Exception) {
                                        message = "Couldn't reach that computer. Search again."
                                        pairing = null
                                        busy = false
                                    }
                                }
                            },
                            enabled = !busy,
                            modifier = Modifier.fillMaxWidth().testTag("core_${c.host}"),
                        ) { Text("Piblo Core on ${c.name}") }
                    }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    OutlinedButton({ search() }, enabled = !searching && !busy, modifier = Modifier.testTag("search_again")) { Text("Search again") }
                    TextButton({ manual = true }, enabled = !busy, modifier = Modifier.testTag("manual_link")) { Text("Enter address and code") }
                }
                message?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            }
        }

        TextButton(onDone, modifier = Modifier.testTag("onboarding_skip")) { Text("Skip for now") }
    }
}

@Composable
private fun ManualConnect(
    busy: Boolean,
    message: String?,
    onConnect: (String, String) -> Unit,
    onBack: () -> Unit,
) {
    var address by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    Section("Enter address and code") {
        OutlinedTextField(
            address, { address = it }, label = { Text("Computer address") },
            placeholder = { Text("192.168.1.20:8787 or 100.x.x.x:8787") }, singleLine = true,
            modifier = Modifier.fillMaxWidth().testTag("manual_address"),
        )
        OutlinedTextField(
            code, { code = it }, label = { Text("Pairing code") },
            placeholder = { Text("ABC123") }, singleLine = true,
            modifier = Modifier.fillMaxWidth().testTag("manual_code"),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Button(
                { onConnect(CoreClient.normalizeBase(address) ?: address, code) },
                enabled = !busy && address.isNotBlank() && code.isNotBlank(),
                modifier = Modifier.testTag("manual_connect"),
            ) { Text(if (busy) "Connecting…" else "Connect") }
            OutlinedButton(onBack, enabled = !busy) { Text("Back") }
        }
        message?.let { Text(it, color = MaterialTheme.colorScheme.error) }
    }
}
