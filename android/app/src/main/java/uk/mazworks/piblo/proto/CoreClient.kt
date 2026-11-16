package uk.mazworks.piblo.proto

import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import org.json.JSONObject

/** HTTP status + Core's `detail` string (e.g. 401 authentication_required, 409 approval_expired). */
class CoreHttpException(val status: Int, val detail: String) : IOException("HTTP $status $detail")

/** Blocking client for the routes the firmware uses. Call from Dispatchers.IO. */
class CoreClient(baseUrl: String, private val token: String, private val clientVersion: String) {
    private val base = baseUrl.trimEnd('/')

    fun summary(): Summary = Proto.parseSummary(call("GET", "/buddy/summary"))
        fun events(since: Int): EventsPage = Proto.parseEvents(call("GET", "/buddy/events?since=$since"))
        fun haltState(): HaltState = Proto.parseHaltState(call("GET", "/buddy/halt"))
        fun setHalt(on: Boolean, minutes: Int? = null) { call("POST", "/buddy/halt", Proto.haltJson(on, minutes)) }
        fun decide(id: String, allow: Boolean) { call("POST", "/buddy/decide", Proto.decideJson(id, allow)) }
    fun alertAction(id: String, action: String, minutes: Int = 10) {
        require(action == "ack" || action == "snooze")
        call("POST", "/buddy/alert/${java.net.URLEncoder.encode(id, "UTF-8")}/$action", if (action == "snooze") """{"minutes":$minutes}""" else "{}")
    }
    fun postAlert(json: String): String = Proto.parsePostedId(call("POST", "/buddy/alert", json))
    fun share(json: String) { call("POST", "/share", json) }

    /** Phone keyboard mode: send literal text to the focused Piblo field. Returns the record id. */
    fun postText(id: String, text: String, mode: String): String = Proto.parseTextId(call("POST", "/buddy/text", Proto.textJson(id, text, mode)))

    /** The device's answer for a text send: pending / inserted / no_field / too_long. */
    fun textStatus(id: String): String = Proto.parseTextStatus(call("GET", "/buddy/text/${java.net.URLEncoder.encode(id, "UTF-8")}"))

    /** The live Cardputer frame (240x135 PNG). 503 when the device isn't reachable. */
    fun screenPng(): ByteArray = requestBytes(base, "/core/cardputer/screen.png", token, clientVersion)

    /** Press Cardputer keys by name (esc/up/enter/...), optionally repeated, e.g. "down*2". */
    fun pressKeys(keys: String) { call("POST", "/core/cardputer/key", Proto.keysJson(keys)) }

    /** Forward a portal action (update, reboot, key:..., open:...). Returns the device's reply. */
    fun deviceAction(action: String): String = call("POST", "/core/cardputer/action", JSONObject().put("action", action).toString())

    /** Authenticated GET/POST for the app's own screens. Call from Dispatchers.IO. */
    fun get(path: String): String = call("GET", path)
    fun post(path: String, body: String? = null): String = call("POST", path, body)
    fun patch(path: String, body: String? = null): String = call("PATCH", path, body)
    fun delete(path: String): String = call("DELETE", path)

    /** For slow brain turns: a long read timeout, still bounded so a stuck call can never spin forever. */
    fun postSlow(path: String, body: String? = null): String = request(base, "POST", path, body, token, clientVersion, readTimeoutMs = 120_000)

    private fun call(method: String, path: String, body: String? = null): String = request(base, method, path, body, token, clientVersion)

    /** Device pairing (public routes, no bearer). See docs/protocol/DEVICE-PAIRING.md. */
    fun health(): String = request(base, "GET", "/health", null, null, clientVersion, readTimeoutMs = 3000)

    fun pairingRequest(baseUrl: String, deviceId: String, name: String): String = request(
        baseUrl.trimEnd('/'), "POST", "/pair/device/request",
        JSONObject().put("device_id", deviceId).put("name", name).toString(), null, clientVersion,
    )

    fun pairingPoll(baseUrl: String, requestId: String): String = request(
        baseUrl.trimEnd('/'), "GET", "/pair/device/request/" + URLEncoder.encode(requestId, "UTF-8"),
        null, null, clientVersion,
    )

    companion object {
        const val DEFAULT_PORT = 8787
        private const val MAX_BODY = 64 * 1024
        private const val MAX_SCREEN = 512 * 1024

        /**
         * The legacy device/Core protocol uses cleartext HTTP on a trusted local network.
         * Never send bearer tokens over plain HTTP to arbitrary public hosts.
         * Away from the local network, configure HTTPS (e.g. through a secure tunnel).
         */
        private fun permitsLocalHttp(host: String): Boolean {
            val h = host.lowercase().removePrefix("[").removeSuffix("]")
            if (h == "localhost" || h == "::1" || h.endsWith(".local") || h.endsWith(".ts.net")) return true
            if ('.' !in h && ':' !in h) return true  // a short local machine name
            val octets = h.split('.').map { it.toIntOrNull() }
            if (octets.size != 4 || octets.any { it == null || it !in 0..255 }) return false
            val a = octets[0]!!; val b = octets[1]!!
            return a == 10 || a == 127 || (a == 172 && b in 16..31) ||
                (a == 192 && b == 168) || (a == 169 && b == 254) ||
                (a == 100 && b in 64..127) // Tailscale / RFC6598 private overlay
        }

        /** "192.168.1.20" / "pc:8787" / "http://pc:8787/" -> "http://host:port". Returns null if it is not a usable address. */
        fun normalizeBase(input: String): String? {
            var s = input.trim().trimEnd('/')
            if (s.isEmpty() || s.any { it.isWhitespace() }) return null
            if (!s.contains("://")) s = "http://$s"
            val u = try { URL(s) } catch (_: Exception) { return null }
            if (u.protocol != "http" && u.protocol != "https") return null
            if (u.host.isNullOrEmpty()) return null
            if (u.protocol == "http" && !permitsLocalHttp(u.host)) return null
            val port = if (u.port == -1 && u.protocol == "http") ":$DEFAULT_PORT" else if (u.port == -1) "" else ":${u.port}"
            return "${u.protocol}://${u.host}$port"
        }

        /** Pairing: the code shown by Core is single-use; the reply is the token (stored encrypted by the caller). */
        fun claim(baseUrl: String, code: String, clientVersion: String): String =
            Proto.parseToken(request(baseUrl.trimEnd('/'), "POST", "/pair/claim", """{"code":"${code.trim().uppercase().filter { it.isLetterOrDigit() }}"}""", null, clientVersion))

        private fun readCapped(s: java.io.InputStream): String {
            val out = java.io.ByteArrayOutputStream()
            val buf = ByteArray(4096)
            while (out.size() < MAX_BODY) {
                val n = s.read(buf)
                if (n < 0) break
                out.write(buf, 0, n)
            }
            return out.toString("UTF-8")
        }

        private fun readBytesCapped(s: java.io.InputStream): ByteArray {
            val out = java.io.ByteArrayOutputStream()
            val buf = ByteArray(4096)
            while (out.size() < MAX_SCREEN) {
                val n = s.read(buf)
                if (n < 0) break
                out.write(buf, 0, n)
            }
            return out.toByteArray()
        }

        internal fun requestBytes(base: String, path: String, token: String?, clientVersion: String): ByteArray {
            val url = URL(base + path)
            if (url.protocol == "http" && !permitsLocalHttp(url.host))
                throw IOException("HTTPS required for non-local Piblo Core")
            val c = url.openConnection() as HttpURLConnection
            try {
                c.requestMethod = "GET"
                c.connectTimeout = 4000
                c.readTimeout = 8000
                c.instanceFollowRedirects = false
                c.setRequestProperty("X-Piblo-Client", "android/$clientVersion")
                if (token != null) c.setRequestProperty("Authorization", "Bearer $token")
                val status = c.responseCode
                if (status !in 200..299) {
                    val text = c.errorStream?.use { readCapped(it) } ?: ""
                    throw CoreHttpException(status, Proto.parseError(text))
                }
                return c.inputStream.use { readBytesCapped(it) }
            } finally { c.disconnect() }
        }

        internal fun request(base: String, method: String, path: String, body: String?, token: String?, clientVersion: String, readTimeoutMs: Int = 8000): String {
            val url = URL(base + path)
            if (url.protocol == "http" && !permitsLocalHttp(url.host))
                throw IOException("HTTPS required for non-local Piblo Core")
            val c = url.openConnection() as HttpURLConnection
            try {
                c.requestMethod = method
                c.connectTimeout = 4000
                c.readTimeout = readTimeoutMs
                c.instanceFollowRedirects = false
                c.setRequestProperty("X-Piblo-Client", "android/$clientVersion")
                c.setRequestProperty("Accept", "application/json")
                if (token != null) c.setRequestProperty("Authorization", "Bearer $token")
                if (body != null) {
                    c.doOutput = true
                    c.setRequestProperty("Content-Type", "application/json")
                    c.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
                }
                val status = c.responseCode
                val stream = if (status in 200..299) c.inputStream else c.errorStream
                val text = stream?.use { readCapped(it) } ?: ""
                if (status !in 200..299) throw CoreHttpException(status, Proto.parseError(text))
                return text
            } finally { c.disconnect() }
        }
    }
}
