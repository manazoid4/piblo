package uk.mazworks.piblo

import android.app.Activity
import android.app.Application
import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.os.Bundle
import android.os.SystemClock
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import uk.mazworks.piblo.data.Message
import uk.mazworks.piblo.data.Prefs
import uk.mazworks.piblo.data.PibloDb
import uk.mazworks.piblo.data.SecretBox
import uk.mazworks.piblo.data.SettingsRepo
import uk.mazworks.piblo.data.MAX_SHARE_RETRIES
import uk.mazworks.piblo.hub.Clock
import uk.mazworks.piblo.hub.CoreLink
import uk.mazworks.piblo.hub.CoreTransport
import uk.mazworks.piblo.hub.Hub
import uk.mazworks.piblo.hub.HubEvents
import uk.mazworks.piblo.hub.Item
import uk.mazworks.piblo.hub.ItemKind
import uk.mazworks.piblo.hub.LinkState
import uk.mazworks.piblo.hub.Origin
import uk.mazworks.piblo.hub.Transport
import uk.mazworks.piblo.proto.CoreClient
import uk.mazworks.piblo.proto.CoreHttpException
import uk.mazworks.piblo.proto.Priority
import uk.mazworks.piblo.proto.Proto
import uk.mazworks.piblo.system.FindPhone
import uk.mazworks.piblo.system.Notifier
import uk.mazworks.piblo.system.WatchService

class PibloApp : Application() {
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    val db by lazy { PibloDb.get(this) }
    val settings by lazy { SettingsRepo(this) }
    @Volatile private var visible = 0
    @Volatile private var prevVisible = 0
    @Volatile var transport: Transport? = null
    private var linkJob: Job? = null
    @Volatile private var link: CoreLink? = null
    private var lastForward = 0L
    private var lastForwardKey = ""
    private var netCbRegistered = false

    val hub = Hub(
        object : Clock {
            override fun mono() = SystemClock.elapsedRealtime()
            override fun wall() = System.currentTimeMillis()
        },
        object : HubEvents {
            override fun shown(item: Item, source: String) = this@PibloApp.onShown(item, source)
            override fun findPhone() = FindPhone.start(this@PibloApp)
        },
    )

    override fun onCreate() {
        super.onCreate()
        inst = this
        Notifier.channels(this)
        registerActivityLifecycleCallbacks(object : ActivityLifecycleCallbacks {
            override fun onActivityStarted(a: Activity) {
                prevVisible = visible
                visible++
                if (prevVisible == 0 && visible == 1) onAppForegrounded()
            }
            override fun onActivityStopped(a: Activity) { visible-- }
            override fun onActivityCreated(a: Activity, b: Bundle?) {}
            override fun onActivityResumed(a: Activity) {}
            override fun onActivityPaused(a: Activity) {}
            override fun onActivitySaveInstanceState(a: Activity, b: Bundle) {}
            override fun onActivityDestroyed(a: Activity) {}
        })
        scope.launch { while (isActive) { delay(1000); hub.tick() } }
        scope.launch {
            settings.prefs.map { it.copy(forwardNotifications = false, forwardPackages = emptySet()) }.distinctUntilChanged().collectLatest(::applyMode)
        }
        registerNetworkCallback()
    }

    /** Called when the app transitions from background to foreground. Resets backoff and reconnects immediately. */
    private fun onAppForegrounded() {
        link?.requestReconnect()
    }

    /** Registers a ConnectivityManager callback so we reconnect when Wi-Fi / mobile comes back. */
    private fun registerNetworkCallback() {
        if (netCbRegistered) return
        try {
            val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            val req = NetworkRequest.Builder()
                .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                .build()
            cm.registerNetworkCallback(req, object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) { link?.requestReconnect() }
            })
            netCbRegistered = true
        } catch (_: Exception) {
            // ConnectivityManager not available (e.g. unit tests) -- skip
        }
    }

    /** Starts or stops the computer link when the connection settings change. */
    private fun applyMode(p: Prefs) {
        linkJob?.cancel()
        transport = null
        WatchService.set(this, p.mode == Prefs.Mode.COMPUTER && p.hasComputer && p.watchInBackground)
        if (p.mode != Prefs.Mode.COMPUTER || !p.hasComputer) { hub.setLink(LinkState.SIMULATOR); return }
        val token = SecretBox.decrypt(p.tokenBlob)
        if (token == null) { hub.setLink(LinkState.AUTH_FAILED); hub.log("Saved pairing can't be read. Connect again."); return }
        val t = CoreTransport(CoreClient(p.coreUrl, token, BuildConfig.VERSION_NAME))
        transport = t
        hub.setLink(LinkState.CONNECTING)
        val l = CoreLink(hub, t)
        link = l
        linkJob = scope.launch(Dispatchers.IO) {
            var prevLinkState: LinkState? = null
            while (isActive) {
                val delayMs = l.cycle()
                val nowState = hub.state.value.link
                if (l.shouldWakeEarly(delayMs)) {
                    hub.log("Reconnecting (foreground or network change)")
                    continue
                }
                val chunk = 500L
                var remaining = delayMs
                while (remaining > 0 && isActive) {
                    if (l.shouldWakeEarly(remaining)) { remaining = 0; break }
                    delay(minOf(chunk, remaining))
                    remaining -= chunk
                }
                if (remaining <= 0 && nowState == LinkState.ONLINE && prevLinkState != LinkState.ONLINE) {
                    drainShareQueue(t)
                }
                prevLinkState = nowState
            }
        }
    }
    /** Drains the local share queue by sending each pending entry to Core via POST /share. */
    private suspend fun drainShareQueue(t: Transport) = withContext(Dispatchers.IO) {
        val dao = db.dao()
        val pending = dao.pendingShares()
        for (s in pending) {
            try {
                t.share(Proto.shareJson(s.text, s.title, s.url, s.idempotencyId))
                dao.dequeueShare(s.id)
                hub.log("Sent queued share: " + (s.title.ifBlank { s.text.take(50) }))
            } catch (e: CoreHttpException) {
                when {
                    e.status == 429 -> {
                        // Rate-limited: don't count against retries, stop this drain cycle
                        hub.log("Share drain rate-limited by Core, backing off")
                        break
                    }
                    e.status in 400..499 -> {
                        // Permanent per-item error: count toward eviction, skip this item, try the next
                        dao.markShareRetry(s.id)
                        if (s.retries + 1 >= MAX_SHARE_RETRIES) {
                            dao.dequeueShare(s.id)
                            Notifier.post(this@PibloApp, "Share dropped", s.title.ifBlank { "Shared text" })
                            hub.log("Gave up on queued share after ${MAX_SHARE_RETRIES} tries")
                        }
                        continue
                    }
                    else -> {
                        // 5xx or unknown CoreHttpException: don't count against retries, stop this drain
                        break
                    }
                }
            } catch (e: IOException) {
                // Network trouble: don't count against retries, stop this drain cycle
                hub.log("Share drain network error: ${e.message}")
                break
            } catch (e: Exception) {
                // Unexpected error: count toward eviction, skip this item
                dao.markShareRetry(s.id)
                if (s.retries + 1 >= MAX_SHARE_RETRIES) {
                    dao.dequeueShare(s.id)
                    Notifier.post(this@PibloApp, "Share dropped", s.title.ifBlank { "Shared text" })
                    hub.log("Gave up on queued share after ${MAX_SHARE_RETRIES} tries")
                }
                continue
            }
        }
    }

    private fun onShown(item: Item, source: String) {
        scope.launch(Dispatchers.IO) {
            val dao = db.dao()
            dao.insert(Message(atMs = System.currentTimeMillis(), source = source, title = item.title, body = item.body, priority = item.priority.wire))
            dao.trimMessages()
        }
        // Notify only for things the person has to act on, and only when the app is closed. Forwarded phone notifications are not repeated.
        if (visible <= 0 && (item.kind == ItemKind.APPROVAL || item.origin == Origin.CORE || item.alertKind == "reminder")) {
            Notifier.post(this, if (item.kind == ItemKind.APPROVAL) "Approval needed: ${item.title}" else item.title, item.body)
        }
    }

    /** Called by the notification listener. Dropped unless forwarding is on and this app is on the allow list. */
    fun forward(pkg: String, label: String, title: String, text: String) {
        scope.launch(Dispatchers.IO) {
            val p = settings.now()
            if (!p.forwardNotifications || pkg !in p.forwardPackages) return@launch
            val key = "$pkg|$title|$text"
            val now = SystemClock.elapsedRealtime()
            // Bounded: at most one per second, and an identical repeat within 10 s is ignored.
            if (now - lastForward < 1000 || (key == lastForwardKey && now - lastForward < 10_000)) return@launch
            lastForward = now; lastForwardKey = key
            val t = transport
            val shownTitle = Proto.truncate(title.ifBlank { label }, 60)
            if (t != null && hub.state.value.link == LinkState.ONLINE) {
                try {
                    t.postAlert(Proto.alertJson(label, "info", shownTitle, text, Priority.NORMAL))
                    hub.log("Sent a notification from $label to your computer")
                    return@launch
                } catch (_: Exception) { /* fall through: show it on the simulator instead */ }
            }
            hub.localAlert(shownTitle, Proto.truncate(text, 200), Priority.NORMAL, "forwarded", label)
        }
    }

    /** Phone keyboard mode: send literal text to the focused Piblo field and wait for the device ack.
     *  Returns the ack status (inserted / no_field / too_long / unsupported) or a short failure reason.
     *  Pass the same [id] to retry a "pending" send: Core dedupes it, so the device never types it twice. */
    suspend fun sendText(text: String, mode: String, id: String = java.util.UUID.randomUUID().toString()): String {
        val t = transport ?: return "no_computer"
        try { t.postText(id, text, mode) } catch (e: CoreHttpException) {
            return when { e.status == 429 -> "busy"; e.detail == "unsupported_characters" -> "unsupported"; else -> "send_failed" }
        } catch (_: Exception) { return "send_failed" }
        repeat(15) {  // poll ~3 s: the device acks when it inserts the text (or says no field / too long)
            delay(200)
            val s = try { t.textStatus(id) } catch (_: Exception) { null }
            if (s != null && s != "pending") return s
        }
        return "pending"  // device is away or busy: dropped after Core's 2-minute TTL, never inserted late
    }

    companion object {
        @Volatile private var inst: PibloApp? = null
        fun get() = inst
    }
}
