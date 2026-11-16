package uk.mazworks.piblo.data

import android.content.Context
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.core.stringSetPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import java.util.UUID

private val Context.store by preferencesDataStore("piblo_settings")

data class Prefs(
    val mode: Mode = Mode.SIMULATOR,
    val coreUrl: String = "",
    val tokenBlob: String = "",          // SecretBox ciphertext, never the plain token
    val forwardNotifications: Boolean = false,
    val forwardPackages: Set<String> = emptySet(),
    val watchInBackground: Boolean = false,
    val onboarded: Boolean = false,      // first-run pairing finished (or explicitly skipped)
    val developer: Boolean = false,      // Settings > Developer: unlocks the built-in simulator
    val deviceName: String = "",         // this phone's name, sent when it asks to pair
    val deviceId: String = "",           // stable id so a re-pair replaces the old request
) {
    enum class Mode { SIMULATOR, COMPUTER }
    val hasComputer get() = coreUrl.isNotEmpty() && tokenBlob.isNotEmpty()
    /** The simulator is a developer tool only; normal use is always the real Core. */
    val useSimulator get() = mode == Mode.SIMULATOR && developer
}

class SettingsRepo(private val ctx: Context) {
    private object K {
        val mode = stringPreferencesKey("mode")
        val url = stringPreferencesKey("core_url")
        val token = stringPreferencesKey("token_blob")
        val fwd = booleanPreferencesKey("forward_notifications")
        val pkgs = stringSetPreferencesKey("forward_packages")
        val bg = booleanPreferencesKey("watch_background")
        val onboarded = booleanPreferencesKey("onboarded")
        val developer = booleanPreferencesKey("developer")
        val deviceName = stringPreferencesKey("device_name")
        val deviceId = stringPreferencesKey("device_id")
    }

    val prefs: Flow<Prefs> = ctx.store.data.map(::read)
    suspend fun now(): Prefs = read(ctx.store.data.first())

    private fun read(p: Preferences) = Prefs(
        mode = if (p[K.mode] == "computer") Prefs.Mode.COMPUTER else Prefs.Mode.SIMULATOR,
        coreUrl = p[K.url] ?: "", tokenBlob = p[K.token] ?: "",
        forwardNotifications = p[K.fwd] ?: false, forwardPackages = p[K.pkgs] ?: emptySet(),
        watchInBackground = p[K.bg] ?: false,
        onboarded = p[K.onboarded] ?: false, developer = p[K.developer] ?: false,
        deviceName = p[K.deviceName] ?: "", deviceId = p[K.deviceId] ?: "",
    )

    suspend fun saveComputer(url: String, plainToken: String) = ctx.store.edit {
        it[K.url] = url; it[K.token] = SecretBox.encrypt(plainToken); it[K.mode] = "computer"
    }

    suspend fun useSimulator() = ctx.store.edit { it[K.mode] = "simulator" }
    suspend fun useComputer() = ctx.store.edit { if ((it[K.url] ?: "").isNotEmpty()) it[K.mode] = "computer" }
    suspend fun forgetComputer() = ctx.store.edit { it.remove(K.url); it.remove(K.token); it[K.mode] = "simulator"; it[K.bg] = false }
    suspend fun setForward(on: Boolean) = ctx.store.edit { it[K.fwd] = on }
    suspend fun setBackground(on: Boolean) = ctx.store.edit { it[K.bg] = on }
    suspend fun setPackage(pkg: String, on: Boolean) = ctx.store.edit {
        it[K.pkgs] = (it[K.pkgs] ?: emptySet()).let { s -> if (on) s + pkg else s - pkg }
    }
    suspend fun setOnboarded(on: Boolean = true) = ctx.store.edit { it[K.onboarded] = on }
    suspend fun setDeveloper(on: Boolean) = ctx.store.edit { it[K.developer] = on }
    suspend fun setDeviceIdentity(name: String, id: String) = ctx.store.edit {
        it[K.deviceName] = name; it[K.deviceId] = id
    }

    /** Stable phone identity for pairing: a readable name plus an id that never changes while installed. */
    suspend fun ensureDeviceIdentity(): Pair<String, String> {
        val p = now()
        var id = p.deviceId
        if (id.isEmpty()) { id = UUID.randomUUID().toString(); setDeviceIdentity(p.deviceName, id) }
        val name = p.deviceName.ifEmpty { android.os.Build.MODEL?.take(24) ?: "Piblo phone" }
        if (p.deviceName != name) setDeviceIdentity(name, id)
        return name to id
    }
}
