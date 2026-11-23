package uk.mazworks.piblo.ui

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
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
import androidx.core.content.FileProvider
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import uk.mazworks.piblo.BuildConfig
import uk.mazworks.piblo.PibloApp
import uk.mazworks.piblo.hub.LinkState
import uk.mazworks.piblo.proto.CoreHttpException
import java.io.File
import java.security.MessageDigest

/** The app build Piblo Core on the computer holds (scripts/stage_phone_apk.py). */
data class PhoneBuild(val code: Int, val name: String, val label: String, val sha256: String, val size: Long)

object AppUpdate {
    fun parse(json: String): PhoneBuild = JSONObject(json).let {
        PhoneBuild(it.getInt("version_code"), it.optString("version_name"), it.optString("label"), it.getString("sha256"), it.getLong("size"))
    }

    /** Newer than this install? Android refuses a lower versionCode anyway; this keeps the button honest. */
    fun isNewer(b: PhoneBuild, installedCode: Int) = b.code > installedCode

    fun sha256(f: File): String {
        val md = MessageDigest.getInstance("SHA-256")
        f.inputStream().use { s -> val buf = ByteArray(64 * 1024); while (true) { val n = s.read(buf); if (n < 0) break; md.update(buf, 0, n) } }
        return md.digest().joinToString("") { "%02x".format(it) }
    }

    fun canInstall(ctx: Context) = Build.VERSION.SDK_INT < 26 || ctx.packageManager.canRequestPackageInstalls()

    fun allowInstallsIntent(ctx: Context) = Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${ctx.packageName}"))

    /** Hands the verified file to Android's installer, which asks the person to confirm. */
    fun install(ctx: Context, apk: File) {
        val uri = FileProvider.getUriForFile(ctx, "${ctx.packageName}.updates", apk)
        ctx.startActivity(Intent(Intent.ACTION_VIEW).setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}

/**
 * "Update available" with one button. [alwaysShow] = Settings (also says "up to date");
 * otherwise (Home) it only appears when Core holds a newer build.
 */
@Composable
fun AppUpdateCard(alwaysShow: Boolean) {
    val ctx = LocalContext.current
    val a = ctx.applicationContext as PibloApp
    val state by a.hub.state.collectAsStateWithLifecycle()
    val scope = rememberCoroutineScope()
    var build by remember { mutableStateOf<PhoneBuild?>(null) }
    var checked by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var msg by remember { mutableStateOf<String?>(null) }
    val online = state.link == LinkState.ONLINE

    LaunchedEffect(online) {
        if (!online) return@LaunchedEffect
        build = withContext(Dispatchers.IO) {
            try { a.transport?.get("/phone/apk/manifest")?.let(AppUpdate::parse) } catch (_: Exception) { null }   // 404 = nothing staged
        }
        checked = true
    }

    val b = build
    val newer = b != null && AppUpdate.isNewer(b, BuildConfig.VERSION_CODE)
    if (!alwaysShow && !newer) return

    Section("App update", Modifier.testTag("app_update")) {
        Labeled("This phone", BuildConfig.VERSION_NAME)
        when {
            !online -> Text("Connect to your computer to check for an update.", style = MaterialTheme.typography.bodySmall, color = Muted)
            !checked -> Loading("Checking your computer…")
            b == null || !newer -> Text("Up to date. Your computer has no newer build.", style = MaterialTheme.typography.bodySmall, color = Muted)
            else -> {
                Text("Update ready: ${b.label.ifBlank { b.name }}", fontWeight = FontWeight.SemiBold)
                Text("${b.name} · ${b.size / 1024 / 1024} MB, from your computer", style = MaterialTheme.typography.bodySmall, color = Muted)
                if (!AppUpdate.canInstall(ctx)) {
                    Text("One time only: let Piblo install its own updates.", style = MaterialTheme.typography.bodySmall)
                    OutlinedButton({ ctx.startActivity(AppUpdate.allowInstallsIntent(ctx)) }) { Text("Allow updates") }
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Button({
                        busy = true; msg = null
                        scope.launch {
                            val result = withContext(Dispatchers.IO) {
                                try {
                                    val dir = File(ctx.cacheDir, "updates").apply { mkdirs() }
                                    val f = File(dir, "piblo.apk"); f.delete()
                                    a.transport?.download("/phone/apk/latest.apk", f) ?: return@withContext "Not connected."
                                    if (f.length() != b.size || AppUpdate.sha256(f) != b.sha256) { f.delete(); return@withContext "The download didn't match. Try again." }
                                    f
                                } catch (e: CoreHttpException) { if (e.status == 404) "Your computer no longer has this build." else "Download failed (${e.status})." }
                                catch (e: Exception) { "Download failed: ${e.message ?: "connection lost"}" }
                            }
                            busy = false
                            if (result is File) {
                                if (AppUpdate.canInstall(ctx)) AppUpdate.install(ctx, result)
                                else msg = "Tap Allow updates first, then Update again."
                            } else msg = result as String
                        }
                    }, enabled = !busy, modifier = Modifier.testTag("app_update_go")) { Text(if (busy) "Downloading…" else "Update") }
                }
                msg?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
            }
        }
    }
}
