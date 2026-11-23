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
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
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
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import uk.mazworks.piblo.PibloApp
import uk.mazworks.piblo.proto.Api
import uk.mazworks.piblo.proto.ModelsStatus

private fun app(ctx: Context) = ctx.applicationContext as PibloApp

private data class ChatMsg(val question: String, val answer: String, val provider: String?)

/** Ask: send a question to Core's brain and read the answer. */
@Composable
fun AskScreen(onConnect: () -> Unit = {}) {
    val a = app(LocalContext.current)
    val scope = rememberCoroutineScope()
    var history by remember { mutableStateOf<List<ChatMsg>>(emptyList()) }
    var input by remember { mutableStateOf("") }
    var route by remember { mutableStateOf("local") }
    var sending by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var models by remember { mutableStateOf<ModelsStatus?>(null) }
    var sessionId by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(Unit) {
        while (true) {
            val t = a.transport
            if (t != null) { withContext(Dispatchers.IO) { try { models = Api.models(t.get("/models")) } catch (_: Exception) { } } }
            delay(15_000)
        }
    }

    fun send() {
        val text = input.trim()
        if (text.isEmpty() || sending) return
        sending = true; error = null
        scope.launch(Dispatchers.IO) {
            try {
                val t = a.transport ?: throw IllegalStateException("not_connected")
                var sid = sessionId
                if (sid == null) {
                    val s = t.post("/session/start")  // body optional; session id comes back
                    sid = JSONObject(s).optString("session_id").ifBlank { null }
                    sessionId = sid
                }
                if (sid == null) throw IllegalStateException("no_session")
                val body = JSONObject().put("text", text).put("session_id", sid).put("route", route).toString()
                val raw = t.postSlow("/turn/text", body)
                val r = Api.ask(raw)
                history = history + ChatMsg(text, r.reply, r.provider)
                input = ""
            } catch (e: Exception) {
                error = when {
                    e.message == "not_connected" -> "Connect your computer first (Settings → Connection)."
                    else -> "No answer. Your computer may be busy or offline."
                }
            } finally { sending = false }
        }
    }

    Column(
        Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        ComputerOffline(onConnect)
        Section("Ask Piblo") {
            if (history.isEmpty()) {
                EmptyState("Ask anything", "Questions go to the brain on your computer. It can read your projects and the web, and the answer appears here.")
            } else {
                history.forEach { m ->
                    Column(Modifier.fillMaxWidth()) {
                        Text("You", style = MaterialTheme.typography.labelMedium, color = Muted)
                        Text(m.question, fontWeight = FontWeight.SemiBold)
                        Text("Piblo${m.provider?.let { " · $it" } ?: ""}", style = MaterialTheme.typography.labelMedium, color = Muted)
                        Text(m.answer, style = MaterialTheme.typography.bodyMedium)
                    }
                }
            }
            OutlinedTextField(
                input, { input = it }, label = { Text("Your question") },
                modifier = Modifier.fillMaxWidth().testTag("ask_input"),
            )
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                listOf("local" to "Local", "auto" to "Auto", "cloud" to "Cloud").forEach { (v, label) ->
                    if (route == v) Button({ route = v }, modifier = Modifier.testTag("route_$v")) { Text(label) }
                    else OutlinedButton({ route = v }, modifier = Modifier.testTag("route_$v")) { Text(label) }
                }
            }
            Button({ send() }, enabled = !sending && input.isNotBlank(), modifier = Modifier.testTag("ask_send")) {
                Text(if (sending) "Asking…" else "Send")
            }
            if (sending) Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                CircularProgressIndicator(strokeWidth = 2.dp)
                Text("Thinking…", style = MaterialTheme.typography.bodySmall, color = Muted)
            }
            error?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
        }

        Section("Brain") {
            val m = models
            if (m == null) Loading("Reading what this computer can use…")
            else {
                Labeled("Local model", when {
                    m.localModel == null -> "none configured"
                    m.localModelInstalled == false -> "${m.localModel} (not downloaded)"
                    else -> m.localModel
                })
                Labeled("Default route", m.defaultRoute ?: "auto")
                m.lastProvider?.let { Labeled("Last used", it) }
            }
        }
    }
}
