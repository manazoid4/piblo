package uk.mazworks.piblo.proto

import org.json.JSONArray
import org.json.JSONObject

/** piblo-proto v1 = the existing Core buddy HTTP+JSON routes. Pure Kotlin (org.json only) so it unit-tests on the JVM. */

const val TITLE_MAX = 60      // docs/ALERTS.md
const val BODY_MAX = 200      // docs/ALERTS.md
const val SOURCE_MAX = 40     // AlertIn.source
const val TEXT_MAX = 500      // phone keyboard text (easy-mode-task.md, POST /buddy/text)
const val KEY_MAX = 400       // cardputer key names (POST /core/cardputer/key)
const val KIND_FIND_PHONE = "find_phone"

/** POST /buddy/text `mode`: what the device does with the text in the focused field. */
val TEXT_MODES = setOf("insert", "replace", "send")

enum class Priority(val wire: String) {
    LOW("low"), NORMAL("normal"), HIGH("high"), URGENT("urgent");
    companion object { fun parse(s: String?) = entries.firstOrNull { it.wire == s } ?: NORMAL }
}

data class PendingApproval(val id: String, val tool: String, val summary: String, val project: String, val leftSeconds: Int)

data class AlertMsg(
    val id: String, val source: String, val kind: String, val title: String, val body: String,
    val priority: Priority, val actions: List<String>,
) {
    /** Additive kind from the Android lane. Until Core accepts it, `source: "find_phone"` is the interim wire form. */
    val isFindPhone get() = kind == KIND_FIND_PHONE || source == KIND_FIND_PHONE
}

data class CoreEvent(val id: Int, val kind: String, val text: String, val source: String, val alert: AlertMsg?)
data class Summary(val agent: String, val count: Int, val items: List<PendingApproval>)
data class EventsPage(val events: List<CoreEvent>, val last: Int)

class ProtoException(message: String) : Exception(message)

object Proto {
    fun parseSummary(json: String): Summary = guard {
        val o = JSONObject(json)
        val items = o.optJSONArray("items") ?: JSONArray()
        Summary(
            o.optString("agent", "idle"), o.optInt("count", items.length()),
            (0 until items.length()).mapNotNull { i ->
                val it = items.optJSONObject(i) ?: return@mapNotNull null
                val id = it.optString("id")
                if (id.isEmpty()) null
                else PendingApproval(id, it.optString("tool"), it.optString("summary"), it.optString("project"), it.optInt("left", 0))
            },
        )
    }

    fun parseEvents(json: String): EventsPage = guard {
        val o = JSONObject(json)
        val arr = o.optJSONArray("events") ?: JSONArray()
        EventsPage(
            (0 until arr.length()).mapNotNull { i ->
                val e = arr.optJSONObject(i) ?: return@mapNotNull null
                // Unknown kinds are kept (old/new peers must ignore, not crash): the caller decides what to show.
                CoreEvent(e.optInt("id", -1), e.optString("kind"), e.optString("text"), e.optString("source"), e.optJSONObject("alert")?.let(::parseAlert))
            },
            o.optInt("last", 0),
        )
    }

    private fun parseAlert(a: JSONObject): AlertMsg {
        val acts = a.optJSONArray("actions")
        return AlertMsg(
            a.optString("id"), a.optString("source"), a.optString("kind", "info"), a.optString("title"), a.optString("body"),
            Priority.parse(a.optString("priority")), if (acts == null) listOf("ack") else (0 until acts.length()).map { acts.optString(it) },
        )
    }

    /** FastAPI errors look like {"detail":"authentication_required"}; 422 has a list, so flatten defensively. */
    fun parseError(body: String): String = try {
        val d = JSONObject(body).opt("detail")
        if (d is String) d else "bad_request"
    } catch (_: Exception) { "" }

    fun parseToken(json: String): String = guard { JSONObject(json).getString("token") }

    fun parsePostedId(json: String): String = guard { JSONObject(json).getString("id") }

    /** Truncates by code point (Core counts Python characters), never splitting a surrogate pair. */
    fun truncate(s: String, max: Int): String {
        val t = s.trim()
        return if (t.codePointCount(0, t.length) <= max) t else t.substring(0, t.offsetByCodePoints(0, max))
    }

    fun alertJson(source: String, kind: String, title: String, body: String, priority: Priority, actions: List<String> = listOf("ack")): String =
        JSONObject()
            .put("source", truncate(source, SOURCE_MAX).ifEmpty { "phone" })
            .put("kind", kind)
            .put("title", truncate(title, TITLE_MAX).ifEmpty { "(no title)" })
            .put("body", truncate(body, BODY_MAX))
            .put("priority", priority.wire)
            .put("actions", JSONArray(actions))
            .toString()

    fun decideJson(id: String, allow: Boolean): String = JSONObject().put("id", id).put("decision", if (allow) "allow" else "deny").toString()

    /** POST /buddy/text body. Text is always literal characters — it is never read as keys or commands. */
    fun textJson(id: String, text: String, mode: String): String =
        JSONObject().put("id", id).put("text", truncate(text, TEXT_MAX)).put("mode", mode).toString()

    fun parseTextId(json: String): String = guard { JSONObject(json).getString("id") }

    /** POST /core/cardputer/key body. Key names the device presses, optionally repeated ("down*2"). */
    /** POST /share body. text contains the shared content, title is optional, url is optional, idempotencyKey allows Core to dedupe. */
    fun shareJson(text: String, title: String, url: String, idempotencyId: String): String =
        JSONObject()
            .put("text", truncate(text, TEXT_MAX))
            .put("title", title)
            .put("url", url)
            .put("idempotency_id", idempotencyId)
            .toString()

    fun keysJson(keys: String): String = JSONObject().put("keys", truncate(keys, KEY_MAX)).toString()

    /** GET /buddy/text/{id} status: pending / inserted / no_field / too_long (device acks). */
    fun parseTextStatus(json: String): String = guard { JSONObject(json).optString("status", "pending") }

    private inline fun <T> guard(block: () -> T): T = try { block() } catch (e: ProtoException) { throw e } catch (e: Exception) { throw ProtoException("unexpected response: ${e.message}") }
}
