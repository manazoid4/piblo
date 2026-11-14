package uk.mazworks.piblo

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import uk.mazworks.piblo.data.MAX_SHARE_QUEUE
import uk.mazworks.piblo.data.Prefs
import uk.mazworks.piblo.data.PibloApp
import uk.mazworks.piblo.data.ShareEntry

/** Android's text share target: queues locally, sends when Core link reconnects. */
class ShareActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (intent.action != Intent.ACTION_SEND) { finish(); return }
        val text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT)?.toString().orEmpty().trim()
        val title = intent.getStringExtra(Intent.EXTRA_SUBJECT).orEmpty().take(200)
        if (text.isBlank() || text.length > 50_000) {
            Toast.makeText(this, "Share text or a link to Piblo.", Toast.LENGTH_LONG).show()
            finish()
            return
        }
        val url = Regex("https?://[^\s]+").find(text)?.value.orEmpty()
        lifecycleScope.launch {
            val app = application as PibloApp
            val prefs = withContext(Dispatchers.IO) { app.settings.now() }
            // Only queue when paired to a computer; otherwise tell the user to open Piblo and pair first.
            if (prefs.mode != Prefs.Mode.COMPUTER || !prefs.hasComputer) {
                Toast.makeText(this@ShareActivity, "Open Piblo and connect to your computer first.", Toast.LENGTH_LONG).show()
                finish()
                return@launch
            }
            val queued = withContext(Dispatchers.IO) {
                try {
                    val dao = app.db.dao()
                    // Bound the queue so it cannot grow without bound.
                    if (dao.shareQueueCount() >= MAX_SHARE_QUEUE) {
                        Toast.makeText(this@ShareActivity, "Share queue full. Wait for queued shares to send.", Toast.LENGTH_LONG).show()
                        false
                    } else {
                        dao.enqueueShare(
                            ShareEntry(text = text, title = title, url = url,
                                createdAt = System.currentTimeMillis(),
                                idempotencyId = java.util.UUID.randomUUID().toString())
                        )
                        true
                    }
                } catch (_: Exception) { false }
            }
            Toast.makeText(this@ShareActivity,
                if (queued) "Saved to Piblo. It will be sent when your computer is connected."
                else "Couldn't save. Open Piblo and check your computer connection.",
                Toast.LENGTH_LONG).show()
            finish()
        }
    }
}

