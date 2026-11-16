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
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import uk.mazworks.piblo.PibloApp
import uk.mazworks.piblo.proto.Api
import uk.mazworks.piblo.proto.DumpItem
import uk.mazworks.piblo.proto.HistoryItem
import uk.mazworks.piblo.proto.LibraryHit

private fun app(ctx: Context) = ctx.applicationContext as PibloApp

/** Memory: everything Piblo has been shown, plus your saved thoughts and library. */
@Composable
fun MemoryScreen() {
    val a = app(LocalContext.current)
    val scope = rememberCoroutineScope()
    var history by remember { mutableStateOf<List<HistoryItem>>(emptyList()) }
    var dumps by remember { mutableStateOf<List<DumpItem>>(emptyList()) }
    var openDump by remember { mutableStateOf<Pair<String, String>?>(null) }  // id, transcript
    var draft by remember { mutableStateOf("") }
    var query by remember { mutableStateOf("") }
    var hits by remember { mutableStateOf<List<LibraryHit>?>(null) }
    var link by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(Unit) {
        while (true) {
            val t = a.transport
            if (t != null) {
                withContext(Dispatchers.IO) {
                    try { history = Api.history(t.get("/buddy/history?limit=100")) } catch (_: Exception) { }
                    try { dumps = Api.dumps(t.get("/dumps")) } catch (_: Exception) { }
                }
            }
            delay(10_000)
        }
    }

    fun reloadDumps() {
        scope.launch(Dispatchers.IO) { try { dumps = Api.dumps(a.transport?.get("/dumps") ?: return@launch) } catch (_: Exception) {} }
    }

    Column(
        Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Section("Messages") {
            if (history.isEmpty()) {
                EmptyState("Nothing here yet", "Approvals, alerts, reminders and results from Piblo and your computer will appear here.")
            } else history.take(50).forEach { h ->
                Column(Modifier.fillMaxWidth()) {
                    Text(h.text.ifBlank { h.kind }, style = MaterialTheme.typography.bodyMedium)
                    Text("${h.source.ifBlank { h.kind }} · ${ago(h.at)}", style = MaterialTheme.typography.bodySmall, color = Muted)
                }
            }
        }

        Section("Saved thoughts") {
            OutlinedTextField(draft, { draft = it }, label = { Text("Save a thought or idea") }, modifier = Modifier.fillMaxWidth().testTag("dump_text"))
            Button({
                scope.launch(Dispatchers.IO) {
                    try {
                        a.transport?.post("/dumps", JSONObject().put("text", draft.trim()).put("source", "phone").toString())
                        draft = ""; reloadDumps()
                    } catch (_: Exception) { error = "Couldn't save that. Is your computer connected?" }
                }
            }, enabled = draft.isNotBlank(), modifier = Modifier.testTag("dump_add")) { Text("Save") }
            if (dumps.isEmpty()) Text("Nothing saved yet.", style = MaterialTheme.typography.bodySmall, color = Muted)
            dumps.take(30).forEach { d ->
                Column(Modifier.fillMaxWidth()) {
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                        Text(d.summary.ifBlank { "(no summary)" }, fontWeight = FontWeight.SemiBold, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(end = 8.dp))
                        Pill(d.status)
                    }
                    Text("${d.project.ifBlank { "no project" }} · ${d.source}", style = MaterialTheme.typography.bodySmall, color = Muted)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        TextButton({
                            scope.launch(Dispatchers.IO) {
                                try {
                                    val raw = a.transport?.get("/dumps/${d.id}") ?: return@launch
                                    openDump = d.id to (Api.dumpDetail(raw) ?: "No transcript saved.")
                                } catch (_: Exception) { error = "Couldn't read that thought." }
                            }
                        }) { Text("Read") }
                        if (d.status != "done") TextButton({
                            scope.launch(Dispatchers.IO) {
                                try { a.transport?.post("/dumps/${d.id}/done", JSONObject().put("agent", "phone").put("note", "").toString()); reloadDumps() } catch (_: Exception) {}
                            }
                        }) { Text("Done") }
                    }
                    openDump?.let { (id, transcript) ->
                        if (id == d.id) Text(transcript, style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
        }

        Section("Library") {
            OutlinedTextField(query, { query = it }, label = { Text("Search your library") }, singleLine = true, modifier = Modifier.fillMaxWidth().testTag("library_q"))
            Button({
                scope.launch(Dispatchers.IO) {
                    try { hits = Api.library(a.transport?.get("/library/search?q=${java.net.URLEncoder.encode(query.trim(), "UTF-8")}&limit=20") ?: return@launch) }
                    catch (_: Exception) { error = "Couldn't search. Is your computer connected?" }
                }
            }, enabled = query.isNotBlank(), modifier = Modifier.testTag("library_search")) { Text("Search") }
            hits?.forEach { h ->
                Column(Modifier.fillMaxWidth()) {
                    Text(h.snippet ?: h.url ?: h.id, style = MaterialTheme.typography.bodyMedium)
                    Text("${h.kind} · ${h.author ?: ""} · ${h.project ?: ""}", style = MaterialTheme.typography.bodySmall, color = Muted)
                }
            }
            OutlinedTextField(link, { link = it }, label = { Text("Save a link (https://…)") }, singleLine = true, modifier = Modifier.fillMaxWidth().testTag("library_link"))
            OutlinedButton({
                scope.launch(Dispatchers.IO) {
                    try { a.transport?.post("/ingest", JSONObject().put("kind", "url").put("url", link.trim()).toString()); link = "" }
                    catch (_: Exception) { error = "Couldn't save that link." }
                }
            }, enabled = link.startsWith("http"), modifier = Modifier.testTag("library_save")) { Text("Save link") }
        }

        error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
    }
}
