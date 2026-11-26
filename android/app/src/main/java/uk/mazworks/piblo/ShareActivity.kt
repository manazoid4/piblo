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
import uk.mazworks.piblo.data.ShareEntry
import uk.mazworks.piblo.data.hasQueuedShare

/** Android Sharesheet destination. Captures locally even before the user pairs with Core. */
class ShareActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (intent.action != Intent.ACTION_SEND) { finish(); return }

        val text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT)?.toString().orEmpty().trim()
        val title = intent.getStringExtra(Intent.EXTRA_SUBJECT).orEmpty().take(200)
        if (text.isBlank() || text.length > 50_000) {
            Toast.makeText(this, "Share a link or text to Piblo.", Toast.LENGTH_LONG).show()
            finish()
            return
        }

        val url = ShareCapture.firstUrl(text)
        val label = ShareCapture.kind(url)
        lifecycleScope.launch {
            val app = application as PibloApp
            // Piblo should never reject useful things simply because the computer is offline
            // or the first pairing has not happened yet.
            val paired = withContext(Dispatchers.IO) {
                val p = app.settings.now()
                p.mode == Prefs.Mode.COMPUTER && p.hasComputer
            }
            val message = withContext(Dispatchers.IO) {
                try {
                    val dao = app.db.dao()
                    val pending = dao.pendingShares()
                    when {
                        hasQueuedShare(pending, text, title) ->
                            "Already in Piblo's phone queue."
                        pending.size >= MAX_SHARE_QUEUE ->
                            "Piblo's share queue is full. Connect to your computer to sync."
                        else -> {
                            dao.enqueueShare(
                                ShareEntry(text = text, title = title, url = url,
                                    createdAt = System.currentTimeMillis(),
                                    idempotencyId = java.util.UUID.randomUUID().toString())
                            )
                            app.requestShareDrain()
                            if (paired) "$label saved on phone. Syncs when Core is online."
                            else "$label saved on phone. Pair Piblo to sync to your computer."
                        }
                    }
                } catch (_: Exception) {
                    "Couldn't save locally. Open Piblo and try again."
                }
            }
            Toast.makeText(this@ShareActivity, message, Toast.LENGTH_LONG).show()
            finish()
        }
    }
}
