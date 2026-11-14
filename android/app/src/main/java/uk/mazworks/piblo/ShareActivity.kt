package uk.mazworks.piblo

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
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
        val url = Regex("https?://[^\\s]+").find(text)?.value.orEmpty()
        lifecycleScope.launch {
            val queued = withContext(Dispatchers.IO) {
                try {
                    val app = application as PibloApp
                    app.db.dao().enqueueShare(
                        ShareEntry(text = text, title = title, url = url, createdAt = System.currentTimeMillis())
                    )
                    true
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
