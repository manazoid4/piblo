package uk.mazworks.piblo

import android.content.Intent
import android.graphics.BitmapFactory
import android.net.Uri
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.lifecycleScope
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import uk.mazworks.piblo.data.MAX_SHARE_QUEUE
import uk.mazworks.piblo.data.ShareEntry
import uk.mazworks.piblo.data.hasQueuedShare

/**
 * Screenshot -> on-phone bundled OCR -> one free public GitHub API lookup -> review/save.
 * No screenshot, entire OCR transcript, credentials or Instagram account data are uploaded.
 * Only a short guessed search phrase (or exact repo path) reaches api.github.com.
 */
class ScreenshotRepoActivity : ComponentActivity() {
    private var busy by mutableStateOf(true)
    private var status by mutableStateOf("Reading screenshot on your phone…")
    private var query by mutableStateOf("")
    private var repos by mutableStateOf<List<ScreenshotRepoFinder.Repo>>(emptyList())
    private var savedMessage by mutableStateOf("")

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val screenshot = sharedImage()
        if (screenshot == null) {
            Toast.makeText(this, "Share one screenshot image to Piblo.", Toast.LENGTH_LONG).show()
            finish()
            return
        }
        setContent {
            MaterialTheme {
                Column(
                    modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState())
                        .padding(20.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Text("Find its GitHub project", style = MaterialTheme.typography.headlineSmall)
                    Text("OCR runs on this phone, free and offline. Only a short search phrase is sent to GitHub; your screenshot stays on your phone.")
                    if (busy) CircularProgressIndicator()
                    Text(status)
                    OutlinedTextField(
                        value = query, onValueChange = { query = it.take(140) },
                        modifier = Modifier.fillMaxWidth(),
                        label = { Text("Project name or GitHub link") },
                        singleLine = true,
                    )
                    Button(
                        onClick = { find(ScreenshotRepoFinder.manualQuery(query)) },
                        enabled = !busy && query.isNotBlank(),
                    ) { Text("Search GitHub for free") }
                    if (repos.isNotEmpty()) {
                        Text("Possible source repositories", style = MaterialTheme.typography.titleMedium)
                    }
                    repos.forEach { repo ->
                        Column(
                            modifier = Modifier.fillMaxWidth().padding(vertical = 8.dp),
                            verticalArrangement = Arrangement.spacedBy(6.dp),
                        ) {
                            Text(repo.fullName, style = MaterialTheme.typography.titleMedium)
                            Text(if (repo.verified) "Verified URL from screenshot"
                                 else "Possible match — check before using")
                            if (repo.description.isNotBlank()) Text(repo.description)
                            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                Button(onClick = { save(repo) }) { Text("Save to Piblo") }
                                OutlinedButton(onClick = {
                                    startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(repo.url)))
                                }) { Text("Open GitHub") }
                            }
                        }
                    }
                    if (savedMessage.isNotBlank()) Text(savedMessage)
                    OutlinedButton(onClick = { finish() }) { Text("Close") }
                }
            }
        }
        readLocally(screenshot)
    }

    private fun sharedImage(): Uri? {
        if (intent.action != Intent.ACTION_SEND || !intent.type.orEmpty().startsWith("image/"))
            return null
        @Suppress("DEPRECATION")
        val uri = intent.getParcelableExtra<Uri>(Intent.EXTRA_STREAM) ?: return null
        // Shared content permission is temporary and scoped to this foreground activity.
        return uri.takeIf { it.scheme == "content" }
    }

    private fun readLocally(uri: Uri) {
        lifecycleScope.launch {
            try {
                val text = withContext(Dispatchers.IO) {
                    // Prevent opening massive/corrupt content via an exported share target.
                    val size = contentResolver.openFileDescriptor(uri, "r")?.use { it.statSize } ?: -1L
                    if (size > 18L * 1024 * 1024) throw IllegalArgumentException("image_too_large")
                    val options = BitmapFactory.Options().apply { inJustDecodeBounds = true }
                    contentResolver.openInputStream(uri)?.use {
                        BitmapFactory.decodeStream(it, null, options)
                    } ?: throw IllegalArgumentException("image_unreadable")
                    if (options.outWidth <= 0 || options.outHeight <= 0 ||
                        options.outWidth.toLong() * options.outHeight.toLong() > 24_000_000L)
                        throw IllegalArgumentException("image_dimensions_invalid")
                    val recogniser = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)
                    try {
                        val image = InputImage.fromFilePath(this@ScreenshotRepoActivity, uri)
                        Tasks.await(recogniser.process(image)).text
                    } finally { recogniser.close() }
                }
                val clue = ScreenshotRepoFinder.clue(text)
                if (clue == null) {
                    busy = false
                    status = "No clear project name detected. Type its name to try GitHub search."
                } else {
                    query = clue.query
                    find(clue)
                }
            } catch (_: Exception) {
                busy = false
                status = "Couldn't read this screenshot. Try a clearer image, or enter the project name."
            }
        }
    }

    private fun find(clue: ScreenshotRepoFinder.Clue?) {
        if (clue == null) {
            status = "Enter a project name or a GitHub repository link."
            return
        }
        busy = true
        savedMessage = ""
        status = "Searching public GitHub repositories…"
        repos = emptyList()
        lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) { ScreenshotRepoFinder.lookup(clue) }
            repos = result.repos
            status = result.message
            busy = false
        }
    }

    private fun save(repo: ScreenshotRepoFinder.Repo) {
        lifecycleScope.launch {
            savedMessage = withContext(Dispatchers.IO) {
                try {
                    val app = application as PibloApp
                    val dao = app.db.dao()
                    // No raw screenshot data or full OCR stored; only selected verified URL.
                    val note = "GitHub repository found from an Instagram screenshot: " + repo.url
                    val title = "Screenshot repo: " + repo.fullName
                    val pending = dao.pendingShares()
                    when {
                        hasQueuedShare(pending, note, title) -> "Already queued on this phone."
                        pending.size >= MAX_SHARE_QUEUE -> "Phone share queue full. Sync saved items first."
                        else -> {
                            dao.enqueueShare(ShareEntry(
                                text = note, title = title, url = repo.url,
                                createdAt = System.currentTimeMillis(),
                                idempotencyId = java.util.UUID.randomUUID().toString(),
                            ))
                            app.requestShareDrain()
                            "Saved on phone. Syncs to Piblo Core when paired and online."
                        }
                    }
                } catch (_: Exception) {
                    "Could not save on phone. Try again."
                }
            }
        }
    }
}
