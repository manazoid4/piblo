package uk.mazworks.piblo.pairing

import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import uk.mazworks.piblo.proto.Api
import uk.mazworks.piblo.proto.CoreClient
import uk.mazworks.piblo.proto.CoreHttpException
import uk.mazworks.piblo.proto.PairRequestInfo
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.NetworkInterface

/** One Core that answered the LAN discovery datagram. [host] is the reply's source address. */
data class DiscoveredCore(val name: String, val host: String, val port: Int, val version: String)

/**
 * LAN discovery (docs/protocol/DEVICE-PAIRING.md section 1): send `PIBLO-DISCOVER 1\n` to the
 * global and subnet broadcasts, collect the JSON replies. Blocking; call from Dispatchers.IO.
 */
object LanDiscovery {
    private const val PORT = 8788
    private const val LINE = "PIBLO-DISCOVER 1\n"
    private val TAG = "PibloDiscovery"

    fun search(timeoutMs: Long = 2500): List<DiscoveredCore> {
        val found = LinkedHashMap<String, DiscoveredCore>()
        val targets = broadcastTargets()
        DatagramSocket().use { sock ->
            sock.broadcast = true
            sock.soTimeout = 200
            val payload = LINE.toByteArray(Charsets.UTF_8)
            for (t in targets) {
                try { sock.send(DatagramPacket(payload, payload.size, t)) } catch (e: Exception) { Log.d(TAG, "send $t failed: ${e.message}") }
            }
            val deadline = System.currentTimeMillis() + timeoutMs
            val buf = ByteArray(512)
            while (System.currentTimeMillis() < deadline) {
                val p = DatagramPacket(buf, buf.size)
                try { sock.receive(p) } catch (_: Exception) { break }  // timeout: no more replies
                val text = String(p.data, 0, p.length, Charsets.UTF_8).trim()
                val o = try { JSONObject(text) } catch (_: Exception) { null } ?: continue
                if (o.optInt("piblo") != 1) continue
                val host = p.address?.hostAddress ?: continue
                val port = o.optInt("port", CoreClient.DEFAULT_PORT)
                val name = o.optString("name").ifBlank { host }
                found["$host:$port"] = DiscoveredCore(name, host, port, o.optString("version"))
            }
        }
        return found.values.toList()
    }

    /** Asks one known address (remembered Core, or a typed LAN / Tailscale one) for /health. Null when unreachable or not Piblo Core. */
    fun probe(baseUrl: String, clientVersion: String): DiscoveredCore? = try {
        val base = CoreClient.normalizeBase(baseUrl) ?: return null
        val o = JSONObject(CoreClient(base, "", clientVersion).health())
        val u = java.net.URL(base)
        if (!o.optBoolean("ok")) null
        else DiscoveredCore(o.optString("name").ifBlank { u.host }, u.host, if (u.port == -1) CoreClient.DEFAULT_PORT else u.port, o.optString("version"))
    } catch (_: Exception) { null }

    /** Global broadcast plus every non-loopback IPv4 subnet broadcast (Android often drops the global one). */
    private fun broadcastTargets(): List<InetSocketAddress> {
        val out = mutableListOf<InetSocketAddress>()
        fun add(addr: InetAddress?) {
            if (addr is Inet4Address && !addr.isLoopbackAddress) {
                try { out.add(InetSocketAddress(addr, PORT)) } catch (_: Exception) {}
            }
        }
        try { out.add(InetSocketAddress(InetAddress.getByName("255.255.255.255"), PORT)) } catch (_: Exception) {}
        try {
            val ifaces = NetworkInterface.getNetworkInterfaces() ?: return out
            for (nif in ifaces) {
                if (!nif.isUp || nif.isLoopback) continue
                for (ia in nif.interfaceAddresses) {
                    add(ia.broadcast)
                }
            }
        } catch (_: Exception) {}
        return out.distinctBy { it.address?.hostAddress }
    }
}

/** Result of one pairing poll. */
sealed class PairOutcome {
    object Pending : PairOutcome()          // still waiting for the person on the PC
    data class Approved(val token: String, val port: Int) : PairOutcome()
    object Denied : PairOutcome()
    object Expired : PairOutcome()          // Core says denied/expired/unknown, or the request vanished
    data class Network(val message: String) : PairOutcome()
}

/** The phone is the "device" in docs/protocol/DEVICE-PAIRING.md: it asks, the PC clicks Allow. */
class DevicePairing(private val clientVersion: String) {

    suspend fun request(baseUrl: String, deviceId: String, name: String): PairRequestInfo = withContext(Dispatchers.IO) {
        val raw = CoreClient(baseUrl, "", clientVersion).pairingRequest(baseUrl, deviceId, name)
        Api.pairRequest(raw) ?: throw CoreHttpException(500, "bad_pair_response")
    }

    /** Manual fallback: a short pairing code (exchange it for a token) or a token pasted from Core. */
    suspend fun manualToken(baseUrl: String, codeOrToken: String): String = withContext(Dispatchers.IO) {
        val v = codeOrToken.trim().filter { !it.isWhitespace() }
        if (v.length <= 12 && v.all { it.isLetterOrDigit() }) CoreClient.claim(baseUrl, v, clientVersion) else v
    }

    suspend fun poll(baseUrl: String, requestId: String): PairOutcome = withContext(Dispatchers.IO) {
        try {
            val raw = CoreClient(baseUrl, "", clientVersion).pairingPoll(baseUrl, requestId)
            when (val s = Api.pairState(raw)) {
                null -> PairOutcome.Expired
                else -> when (s.status) {
                    "approved" -> if (s.token != null) PairOutcome.Approved(s.token, s.port) else PairOutcome.Expired
                    "denied", "expired" -> PairOutcome.Expired
                    else -> PairOutcome.Pending
                }
            }
        } catch (e: CoreHttpException) {
            // 404 means unknown/deleted or a different source IP: both are terminal for this request.
            if (e.status == 404) PairOutcome.Expired else PairOutcome.Network("http_${e.status}")
        } catch (e: Exception) {
            PairOutcome.Network(e.message ?: "network_error")
        }
    }

    companion object {
        /** "482913" -> "482 913", the two groups the person compares. */
        fun formatCode(code: String): String {
            val d = code.filter { it.isDigit() }
            return if (d.length == 6) "${d.substring(0, 3)} ${d.substring(3)}" else code
        }
    }
}
