package uk.mazworks.piblo.hub

import uk.mazworks.piblo.proto.CoreClient
import uk.mazworks.piblo.proto.CoreHttpException
import uk.mazworks.piblo.proto.EventsPage
import uk.mazworks.piblo.proto.Summary
import java.io.IOException
import kotlin.math.min
import kotlin.random.Random

data class PollResult(val summary: Summary, val page: EventsPage)

/** The seam where a phone<->Piblo link plugs in. Today: Core over Wi-Fi HTTP. Later: BLE carrying the same v1 JSON. */
interface Transport {
    val label: String
    fun poll(since: Int): PollResult
    fun decide(id: String, allow: Boolean)
    fun alertAction(id: String, action: String, minutes: Int)
    fun postAlert(json: String): String
    fun postText(id: String, text: String, mode: String): String
    fun textStatus(id: String): String
    fun screenPng(): ByteArray
    fun pressKeys(keys: String)
}

class CoreTransport(private val c: CoreClient) : Transport {
    override val label = "Computer (Core, Wi-Fi)"
    override fun poll(since: Int) = PollResult(c.summary(), c.events(since))
    override fun decide(id: String, allow: Boolean) = c.decide(id, allow)
    override fun alertAction(id: String, action: String, minutes: Int) = c.alertAction(id, action, minutes)
    override fun postAlert(json: String) = c.postAlert(json)
    override fun postText(id: String, text: String, mode: String) = c.postText(id, text, mode)
    override fun textStatus(id: String) = c.textStatus(id)
    override fun screenPng() = c.screenPng()
    override fun pressKeys(keys: String) = c.pressKeys(keys)
}

/** Clearly a stub: the current firmware has no BLE GATT service. See android/README.md for the proposed framing. */
object BleTransportStub : Transport {
    override val label = "Piblo device (Bluetooth) - coming with firmware BLE"
    private fun na(): Nothing = throw IOException("Bluetooth link needs a firmware update that is not released yet")
    override fun poll(since: Int): PollResult = na()
    override fun decide(id: String, allow: Boolean) = na()
    override fun alertAction(id: String, action: String, minutes: Int) = na()
    override fun postAlert(json: String): String = na()
    override fun postText(id: String, text: String, mode: String): String = na()
    override fun textStatus(id: String): String = na()
    override fun screenPng(): ByteArray = na()
    override fun pressKeys(keys: String) = na()
}

/**
 * Exponential backoff with 25% uniform jitter.
 * @param minMs initial delay (default 2s)
 * @param maxMs cap (default 30s, per the task spec)
 */
class Backoff(private val minMs: Long = 2_000, private val maxMs: Long = 30_000) {
    private var cur = minMs

    /** Next delay in ms, with [0, 0.25 * cur) uniform jitter subtracted. */
    fun next(): Long {
        val v = cur
        cur = min(cur * 2, maxMs)
        val jitter = (v * Random.nextDouble(0.0, 0.25)).toLong()
        return (v - jitter).coerceAtLeast(1_000) // floor at 1s so polling never races
    }

    /** Immediate reconnect: reset to minMs (caller triggers a fresh attempt right away, but [next()] is called on failure). */
    fun reset() { cur = minMs }

    /** Current raw delay without jitter (for testing and visibility). */
    fun currentRaw(): Long = cur
}

/** One blocking cycle of "send what the person answered, then poll", returning how long to wait before the next one. */
class CoreLink(
    private val hub: Hub,
    private val t: Transport,
    private val pollMs: Long = 2_500,
    private val authRetryMs: Long = 30_000,
    /** Called when a share should be drained; returns true if any share was sent. */
    private val drainShares: (suspend () -> Unit)? = null,
) {
    private val backoff = Backoff()
    private var since = 0
    private var initial = true
    @Volatile private var reconnectRequested = false

    /** Call from the lifecycle / network-callback thread to break out of a long backoff sleep. */
    fun requestReconnect() { reconnectRequested = true }

    fun cycle(): Long {
        try {
            while (true) {
                val o = hub.nextOutgoing() ?: break
                try {
                    when (o) {
                        is Outgoing.Decide -> t.decide(o.id, o.allow)
                        is Outgoing.AlertAction -> t.alertAction(o.id, o.action, o.minutes)
                    }
                    hub.sent(o)
                } catch (e: CoreHttpException) {
                    if (e.status == 401) throw e
                    hub.failed(o, giveUp = true)   // 404 gone / 409 expired / 400 refused: retrying cannot help
                } catch (e: IOException) {
                    hub.failed(o, giveUp = false)  // network trouble: keep it (bounded) and back off
                    throw e
                }
            }
            val r = t.poll(since)
            hub.applyPoll(r.summary, r.page, initial)
            since = r.page.last
            if (initial) { hub.log("Connected to ${t.label}"); initial = false }
            hub.setLink(LinkState.ONLINE)
            backoff.reset()
            return pollMs
        } catch (e: CoreHttpException) {
            hub.setLink(if (e.status == 401) LinkState.AUTH_FAILED else LinkState.OFFLINE)
            return if (e.status == 401) authRetryMs else backoff.next()
        } catch (e: IOException) {
            return offline()
        } catch (e: uk.mazworks.piblo.proto.ProtoException) {
            return offline()
        }
    }

    /** After [cycle] returns, the caller should sleep for [delayMs] then call [cycle] again.
     *  If a reconnect was requested in the meantime, this returns true and the caller should
     *  skip the remaining delay and call [cycle] immediately. */
    fun shouldWakeEarly(delayMs: Long): Boolean {
        if (reconnectRequested) {
            reconnectRequested = false
            backoff.reset()
            return true
        }
        return false
    }

    private fun offline(): Long {
        hub.setLink(LinkState.OFFLINE)
        return backoff.next()
    }
}