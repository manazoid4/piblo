package uk.mazworks.piblo.proto

import org.json.JSONArray
import org.json.JSONObject

/**
 * Response types for the app's own screens. Every parser is forgiving: a missing or
 * unknown field becomes a default, because Core is additive-only and may be newer
 * or older than this app. Nothing here may crash on an unexpected body.
 */

data class DeviceStatus(
    val name: String,
    val online: Boolean,
    val version: String,
    val app: String,
    val battery: Int?,
    val charging: Boolean,
    val wifiConnected: Boolean,
    val wifiSsid: String,
    val rssi: Int?,
    val hostLink: String,
    val hubVersion: String?,
    val updateAvailable: Boolean,
    val lastStage: String?,
    val lastError: String?,
)

data class HistoryItem(val id: String, val kind: String, val text: String, val source: String, val agent: String, val at: Long)

data class NeedItem(val id: String, val tier: String, val title: String, val project: String, val age: Long, val detail: String)

data class NeedsPage(val items: List<NeedItem>, val warnings: List<String>)

data class TodayPage(
    val done: Int, val needs: Int, val errors: Int, val approved: Int, val denied: Int,
    val lastText: String?, val lastSource: String?,
)

data class FocusState(val state: String, val task: String, val remainingS: Int, val muted: Boolean)

data class FlowStatus(val count: Int, val avgMs: Int, val p95Ms: Int, val errors: List<String>)

data class FlowSettings(
    val inputMode: String, val pressEnter: Boolean, val cleanup: String, val stt: String,
    val effectiveCleanup: String, val hasGroqKey: Boolean,
)

data class FlowWord(val term: String, val aliases: List<String>)

data class DumpItem(
    val id: String, val summary: String, val status: String, val project: String,
    val created: String, val source: String, val claimedBy: String?,
)

data class LibraryHit(
    val id: String, val kind: String, val url: String?, val author: String?,
    val project: String?, val snippet: String?, val saved: String?,
)

data class AskResult(val reply: String, val provider: String?)

data class ModelsStatus(
    val local: Boolean, val localModel: String?, val localModelInstalled: Boolean?,
    val localEngine: String?, val defaultRoute: String?, val lastProvider: String?,
)

data class PromiseCard(
    val id: String, val state: String, val title: String, val whenText: String,
    val note: String, val overdue: Boolean, val actions: List<String>,
)

data class PromisesToday(val cards: List<PromiseCard>, val due: Int, val waiting: Int, val done: Int, val empty: Boolean)

data class PairRequestInfo(val requestId: String, val code: String, val expiresIn: Int)

data class PairState(val status: String, val token: String?, val port: Int)

data class Health(val version: String, val coreEnabled: Boolean, val deviceKind: String?, val fwLatest: String?)

object Api {
    private fun obj(o: JSONObject?, key: String): JSONObject? = if (o == null) null else o.optJSONObject(key)

    fun device(raw: String): DeviceStatus = try {
        val o = JSONObject(raw)
        val d = obj(o, "device") ?: JSONObject()
        val hub = obj(o, "hub_fw")
        val report = obj(o, "last_report")
        DeviceStatus(
            name = d.optString("name").ifEmpty { d.optString("hostname") }.ifEmpty { "Piblo" },
            online = o.optBoolean("online", d.has("version")),
            version = d.optString("version"),
            app = d.optString("app"),
            battery = if (d.has("battery") && !d.isNull("battery")) d.optInt("battery") else null,
            charging = d.optBoolean("charging"),
            wifiConnected = d.optBoolean("wifi_connected"),
            wifiSsid = d.optString("wifi_ssid"),
            rssi = if (d.has("rssi") && !d.isNull("rssi")) d.optInt("rssi") else null,
            hostLink = d.optString("host_link"),
            hubVersion = hub?.optString("version")?.takeIf { it.isNotBlank() },
            updateAvailable = o.optBoolean("update_available"),
            lastStage = report?.optString("stage")?.takeIf { it.isNotBlank() },
            lastError = report?.optString("error")?.takeIf { it.isNotBlank() },
        )
    } catch (e: Exception) {
        DeviceStatus("Piblo", false, "", "", null, false, false, "", null, "", null, false, null, null)
    }

    fun history(raw: String): List<HistoryItem> = try {
        val o = JSONObject(raw)
        val arr = o.optJSONArray("notifications") ?: JSONArray()
        (0 until arr.length()).mapNotNull { i ->
            val e = arr.optJSONObject(i) ?: return@mapNotNull null
            HistoryItem(
                e.optString("id"), e.optString("kind"), e.optString("text"),
                e.optString("source"), e.optString("agent"), e.optLong("ts", 0L),
            )
        }
    } catch (_: Exception) { emptyList() }

    fun needs(raw: String): NeedsPage = try {
        val o = JSONObject(raw)
        val arr = o.optJSONArray("items") ?: JSONArray()
        val warn = o.optJSONArray("warnings") ?: JSONArray()
        NeedsPage(
            (0 until arr.length()).mapNotNull { i ->
                val e = arr.optJSONObject(i) ?: return@mapNotNull null
                NeedItem(e.optString("id"), e.optString("tier"), e.optString("title"), e.optString("project"),
                    e.optLong("age", 0L), e.optString("detail"))
            },
            (0 until warn.length()).map { warn.optString(it) },
        )
    } catch (_: Exception) { NeedsPage(emptyList(), emptyList()) }

    fun today(raw: String): TodayPage = try {
        val o = JSONObject(raw)
        val last = obj(o, "last")
        TodayPage(o.optInt("done"), o.optInt("needs"), o.optInt("errors"), o.optInt("approved"),
            o.optInt("denied"), last?.optString("text"), last?.optString("source"))
    } catch (_: Exception) { TodayPage(0, 0, 0, 0, 0, null, null) }

    fun focus(raw: String): FocusState = try {
        val o = JSONObject(raw)
        FocusState(o.optString("state"), o.optString("task"), o.optInt("remaining_s"), o.optBoolean("muted"))
    } catch (_: Exception) { FocusState("", "", 0, false) }

    fun flowStatus(raw: String): FlowStatus = try {
        val o = JSONObject(raw)
        val errs = o.optJSONArray("errors") ?: JSONArray()
        FlowStatus(o.optInt("count"), o.optInt("avg_total_ms"), o.optInt("p95_total_ms"),
            (0 until errs.length()).map { errs.optString(it) })
    } catch (_: Exception) { FlowStatus(0, 0, 0, emptyList()) }

    fun flowSettings(raw: String): FlowSettings = try {
        val o = JSONObject(raw)
        FlowSettings(o.optString("input_mode", "paste"), o.optBoolean("press_enter"),
            o.optString("cleanup", "auto"), o.optString("stt", "auto"),
            o.optString("effective_cleanup"), o.optBoolean("has_groq_key"))
    } catch (_: Exception) { FlowSettings("paste", false, "auto", "auto", "", false) }

    fun flowDictionary(raw: String): List<FlowWord> = try {
        val o = JSONObject(raw)
        val entries = o.optJSONArray("entries")
        if (entries != null) {
            (0 until entries.length()).mapNotNull { i ->
                val e = entries.optJSONObject(i) ?: return@mapNotNull null
                val aliases = e.optJSONArray("aliases") ?: JSONArray()
                FlowWord(e.optString("term"), (0 until aliases.length()).map { aliases.optString(it) })
            }
        } else {
            val words = o.optJSONArray("words") ?: JSONArray()
            (0 until words.length()).map { FlowWord(words.optString(it), emptyList()) }
        }
    } catch (_: Exception) { emptyList() }

    fun dumps(raw: String): List<DumpItem> = try {
        val o = JSONObject(raw)
        val arr = o.optJSONArray("dumps") ?: JSONArray()
        (0 until arr.length()).mapNotNull { i ->
            val e = arr.optJSONObject(i) ?: return@mapNotNull null
            DumpItem(e.optString("id"), e.optString("summary"), e.optString("status", "new"),
                e.optString("project"), e.optString("created"), e.optString("source"),
                e.optString("claimed_by").takeIf { it.isNotBlank() })
        }
    } catch (_: Exception) { emptyList() }

    fun dumpDetail(raw: String): String? = try { JSONObject(raw).optString("transcript").takeIf { it.isNotBlank() } } catch (_: Exception) { null }

    fun library(raw: String): List<LibraryHit> = try {
        val o = JSONObject(raw)
        val arr = o.optJSONArray("results") ?: JSONArray()
        (0 until arr.length()).mapNotNull { i ->
            val e = arr.optJSONObject(i) ?: return@mapNotNull null
            LibraryHit(e.optString("id"), e.optString("kind"), e.optString("url").takeIf { it.isNotBlank() },
                e.optString("author").takeIf { it.isNotBlank() }, e.optString("project").takeIf { it.isNotBlank() },
                e.optString("snippet").takeIf { it.isNotBlank() }, e.optString("saved").takeIf { it.isNotBlank() })
        }
    } catch (_: Exception) { emptyList() }

    fun ask(raw: String): AskResult = try {
        val o = JSONObject(raw)
        AskResult(o.optString("reply"), o.optString("provider").takeIf { it.isNotBlank() })
    } catch (_: Exception) { AskResult("", null) }

    fun models(raw: String): ModelsStatus = try {
        val o = JSONObject(raw)
        val last = obj(o, "last_route")
        ModelsStatus(o.optBoolean("local"), o.optString("local_model").takeIf { it.isNotBlank() },
            if (o.has("local_model_installed")) o.optBoolean("local_model_installed") else null,
            o.optString("local_engine").takeIf { it.isNotBlank() },
            o.optString("default_route").takeIf { it.isNotBlank() },
            last?.optString("provider")?.takeIf { it.isNotBlank() })
    } catch (_: Exception) { ModelsStatus(false, null, null, null, null, null) }

    fun promisesToday(raw: String): PromisesToday = try {
        val o = JSONObject(raw)
        val arr = o.optJSONArray("cards") ?: JSONArray()
        val counts = obj(o, "counts")
        PromisesToday(
            (0 until arr.length()).mapNotNull { i ->
                val e = arr.optJSONObject(i) ?: return@mapNotNull null
                val acts = e.optJSONArray("actions") ?: JSONArray()
                PromiseCard(e.optString("id"), e.optString("state"), e.optString("title"), e.optString("when"),
                    e.optString("note"), e.optBoolean("overdue"), (0 until acts.length()).map { acts.optString(it) })
            },
            counts?.optInt("due") ?: 0, counts?.optInt("waiting") ?: 0,
            counts?.optInt("done") ?: 0, o.optBoolean("empty"),
        )
    } catch (_: Exception) { PromisesToday(emptyList(), 0, 0, 0, true) }

    fun pairRequest(raw: String): PairRequestInfo? = try {
        val o = JSONObject(raw)
        val id = o.optString("request_id")
        if (id.isBlank()) null else PairRequestInfo(id, o.optString("code"), o.optInt("expires_in_seconds", 120))
    } catch (_: Exception) { null }

    /** Pair poll: pending / approved (token, port) / denied / expired, or null on a transport/parse failure (404 is pending-or-gone). */
    fun pairState(raw: String): PairState? = try {
        val o = JSONObject(raw)
        val status = o.optString("status")
        if (status.isBlank()) null else PairState(status, o.optString("token").takeIf { it.isNotBlank() }, o.optInt("port", 8787))
    } catch (_: Exception) { null }

    fun halt(raw: String): HaltState? = try { Proto.parseHaltState(raw) } catch (_: Exception) { null }

    fun health(raw: String): Health = try {
        val o = JSONObject(raw)
        val fw = obj(o, "fw_latest")
        val core = obj(o, "core")
        Health(o.optString("version"), core?.optBoolean("ok") ?: o.optBoolean("core_enabled", true),
            o.optString("device_kind").takeIf { it.isNotBlank() },
            fw?.optString("version")?.takeIf { it.isNotBlank() })
    } catch (_: Exception) { Health("", false, null, null) }
}
