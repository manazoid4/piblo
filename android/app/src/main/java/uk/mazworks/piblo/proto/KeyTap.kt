package uk.mazworks.piblo.proto

import org.json.JSONObject

/** Piblo Key (NTAG 424 DNA) taps arrive as https://piblo.co.uk/k?p=<32 hex>&c=<16 hex>. See docs/protocol/AUTH.md. */
object KeyTap {
    private const val MAX_URL = 256
    private val HEX32 = Regex("^[0-9a-fA-F]{32}$")
    private val HEX16 = Regex("^[0-9a-fA-F]{16}$")

    /** True only for the exact card URL shape; anything else is never sent to the computer. */
    fun isKeyUrl(url: String?): Boolean {
        if (url == null || url.length > MAX_URL) return false
        val m = Regex("^https://piblo\\.co\\.uk/k\\?(.*)$").matchEntire(url) ?: return false
        val q = m.groupValues[1].split('&').mapNotNull {
            val i = it.indexOf('=')
            if (i <= 0) null else it.substring(0, i) to it.substring(i + 1)
        }.toMap()
        return HEX32.matches(q["p"].orEmpty()) && HEX16.matches(q["c"].orEmpty())
    }

    /** POST /auth/key/tap body: the full URL, unchanged. */
    fun json(url: String): String = JSONObject().put("url", url).toString()

    /** One short line for the person. `code` is Core's error (unknown_key, bad_mac, replay) or null on success. */
    fun message(code: String?): String = when (code) {
        null -> "Piblo Key accepted"
        "unknown_key" -> "This Piblo Key isn't added on your computer"
        "bad_mac" -> "That tap didn't check out. Try again"
        "replay" -> "That tap was already used. Tap again"
        "offline" -> "Can't reach your computer"
        "not_paired" -> "Connect Piblo to your computer first"
        "not_a_key" -> "That isn't a Piblo Key link"
        else -> "Piblo Key not accepted"
    }
}
