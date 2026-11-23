package uk.mazworks.piblo.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import uk.mazworks.piblo.PibloApp
import uk.mazworks.piblo.data.Prefs
import uk.mazworks.piblo.hub.LinkState
import uk.mazworks.piblo.proto.CoreHttpException
import uk.mazworks.piblo.proto.CraftTask
import uk.mazworks.piblo.proto.parseCraftTasks

@Composable
fun CraftScreen(onClose: () -> Unit, onConnect: () -> Unit) {
    val app = LocalContext.current.applicationContext as PibloApp
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val prefs by app.settings.prefs.collectAsStateWithLifecycle(initialValue = null)
    val hub by app.hub.state.collectAsStateWithLifecycle()
    val connection = prefs ?: return
    val connected = connection.hasComputer && connection.mode == Prefs.Mode.COMPUTER
    var tasks by remember(connection.coreUrl, connection.tokenBlob, connected) { mutableStateOf<List<CraftTask>?>(null) }
    var error by remember(connection.coreUrl, connection.tokenBlob, connected) { mutableStateOf<String?>(null) }

    LaunchedEffect(lifecycle, connection.coreUrl, connection.tokenBlob, connected) {
        if (!connected) return@LaunchedEffect
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) {
                val transport = app.transport
                try {
                    if (transport == null) {
                        error = if (hub.link == LinkState.AUTH_FAILED) "Pair this phone with Piblo Core again." else "Connecting to Piblo Core."
                    } else {
                        tasks = withContext(Dispatchers.IO) { parseCraftTasks(transport.get("/buddy/tasks")) }
                        error = null
                    }
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (failure: Exception) {
                    val status = (failure as? CoreHttpException)?.status
                    error = when (status) {
                        401, 403 -> "Pair this phone with Piblo Core again."
                        404 -> "This version of Piblo Core does not expose the task board."
                        else -> "Couldn't refresh tasks. Check your connection to Piblo Core."
                    }
                    if (status == 401 || status == 403) tasks = null
                }
                delay(8_000)
            }
        }
    }

    val current = tasks
    val problem = error
    LazyColumn(
        Modifier.fillMaxSize().safeDrawingPadding(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item {
            TextButton(onClose) { Text("Back to Home") }
            Text("PibloCraft", style = MaterialTheme.typography.headlineMedium)
        }
        item {
            when {
                !connected -> Button(onConnect) { Text("Connect to Piblo Core") }
                problem != null -> {
                    Text(problem, color = MaterialTheme.colorScheme.error)
                    if (current != null) Text("Showing the last update.")
                    if (hub.link == LinkState.AUTH_FAILED) Button(onConnect) { Text("Connect again") }
                }
                current == null -> Text("Loading tasks…")
                current.isEmpty() -> Text("No tasks have been reported to Piblo Core yet.")
            }
        }
        items(current.orEmpty(), key = { it.id }) { task ->
            Section(task.title) {
                Text(task.agent.ifBlank { "Unassigned" }, style = MaterialTheme.typography.bodyMedium)
                Text(task.statusLabel + (task.progress?.let { " · $it%" } ?: ""))
                if (task.detail.isNotBlank()) Text(task.detail, style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}
