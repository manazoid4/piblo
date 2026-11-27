package uk.mazworks.piblo

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import uk.mazworks.piblo.data.Prefs
import uk.mazworks.piblo.data.SecretBox
import uk.mazworks.piblo.proto.CoreClient
import uk.mazworks.piblo.proto.CoreHttpException
import uk.mazworks.piblo.proto.KeyTap

/**
 * Opens when a Piblo Key is tapped on the phone (App Link https://piblo.co.uk/k). Forwards the full
 * URL to the paired computer, shows one line, and closes. No screen of its own.
 */
class KeyTapActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val url = if (intent.action == Intent.ACTION_VIEW) intent.data?.toString() else null
        if (!KeyTap.isKeyUrl(url)) { toastAndFinish(KeyTap.message("not_a_key")); return }
        lifecycleScope.launch {
            val app = application as PibloApp
            val message = withContext(Dispatchers.IO) {
                val prefs = app.settings.now()
                if (prefs.mode != Prefs.Mode.COMPUTER || !prefs.hasComputer) return@withContext KeyTap.message("not_paired")
                val token = SecretBox.decrypt(prefs.tokenBlob) ?: return@withContext KeyTap.message("not_paired")
                try {
                    CoreClient(prefs.coreUrl, token, BuildConfig.VERSION_NAME).keyTap(url!!)
                    KeyTap.message(null)
                } catch (e: CoreHttpException) {
                    KeyTap.message(e.detail)
                } catch (_: Exception) {
                    KeyTap.message("offline")
                }
            }
            toastAndFinish(message)
        }
    }

    private fun toastAndFinish(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_LONG).show()
        finish()
    }
}
