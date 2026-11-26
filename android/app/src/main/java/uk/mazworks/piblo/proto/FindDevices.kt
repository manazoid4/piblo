package uk.mazworks.piblo.proto

import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.round

/** One of the user's devices as Piblo Core's /finddevices returns it. */
internal data class FoundDevice(
    val id: String,
    val name: String,
    val kind: String,
    val proximityLabel: String,
    val proximityState: String,
    val lastSeenAgoS: Long?,
    val actions: List<DeviceAction>,
    val bluetoothAddresses: List<String>,
    val appleModelId: String,
) {
    val canRing get() = actions.any { it.kind == "ring" }
    val handoff get() = actions.firstOrNull { it.kind == "handoff" }
}

internal data class DeviceAction(val id: String, val label: String, val provider: String, val kind: String, val url: String)

/** A device Piblo can hear but that is not in the book (or not provably the user's). */
internal data class NearbyDevice(val label: String, val address: String, val name: String, val rssi: Int?,
                                 val bonded: Boolean, val appleModelId: String)

internal data class FindOverview(val devices: List<FoundDevice>, val nearby: List<NearbyDevice>)

internal fun parseFindOverview(body: String): FindOverview {
    val root = JSONObject(body)
    val devices = root.optJSONArray("devices") ?: JSONArray()
    val nearby = root.optJSONArray("nearby") ?: JSONArray()
    return FindOverview(
        List(devices.length()) { parseFoundDevice(devices.getJSONObject(it)) },
        List(nearby.length()) { i ->
            val n = nearby.getJSONObject(i)
            NearbyDevice(
                label = n.optString("label"), address = n.optString("address"), name = n.optString("name"),
                rssi = if (n.isNull("rssi")) null else n.optInt("rssi"), bonded = n.optBoolean("bonded"),
                appleModelId = n.optJSONObject("apple")?.optString("model_id").orEmpty(),
            )
        },
    )
}

internal fun parseFoundDevice(d: JSONObject): FoundDevice {
    val prox = d.optJSONObject("proximity") ?: JSONObject()
    val actions = d.optJSONArray("actions") ?: JSONArray()
    val bt = d.optJSONObject("providers")?.optJSONObject("bluetooth")
    val addrs = bt?.optJSONArray("addresses") ?: JSONArray()
    val last = d.optJSONObject("last_seen")
    return FoundDevice(
        id = d.getString("id"), name = d.getString("name"), kind = d.optString("kind", "other"),
        proximityLabel = prox.optString("label", "Not seen by Piblo yet"),
        proximityState = prox.optString("state", "never_seen"),
        lastSeenAgoS = last?.optLong("ago_s"),
        actions = List(actions.length()) { i ->
            val a = actions.getJSONObject(i)
            DeviceAction(a.optString("id"), a.optString("label"), a.optString("provider"), a.optString("kind"), a.optString("url"))
        },
        bluetoothAddresses = List(addrs.length()) { addrs.getString(it).uppercase() },
        appleModelId = bt?.optString("apple_model_id").orEmpty().uppercase(),
    )
}

/** One BLE advert this phone heard. `appleData` is Apple (0x004C) manufacturer data, hex, company id stripped. */
internal data class Sighting(
    val atMs: Long, val address: String, val name: String, val rssi: Int, val txPower: Int?,
    val bonded: Boolean, val appleData: String, val deviceId: String = "",
)

internal fun sightingsJson(observer: String, rows: List<Sighting>): String {
    val arr = JSONArray()
    rows.take(200).forEach { s ->
        val o = JSONObject().put("at", s.atMs / 1000.0).put("address", s.address).put("name", s.name)
            .put("rssi", s.rssi).put("bonded", s.bonded)
        s.txPower?.let { o.put("tx_power", it) }
        if (s.appleData.isNotEmpty()) o.put("apple_data", s.appleData)
        if (s.deviceId.isNotEmpty()) o.put("device_id", s.deviceId)
        arr.put(o)
    }
    return JSONObject().put("observer", observer).put("sightings", arr).toString()
}

/** Add-device body for Core. Bonded devices are matched by address; Apple lookalikes by model only. */
internal fun addDeviceJson(name: String, kind: String, providers: Map<String, JSONObject>, id: String = ""): String {
    val p = JSONObject()
    providers.forEach { (k, v) -> p.put(k, v) }
    return JSONObject().put("id", id).put("name", name).put("kind", kind).put("providers", p).toString()
}

/** The Apple proximity-pairing model id (e.g. "0E20" = AirPods Pro) in manufacturer data, or "". */
internal fun appleModelId(hex: String): String {
    val bytes = hex.chunked(2).mapNotNull { it.toIntOrNull(16) }
    var i = 0
    while (i + 2 <= bytes.size) {
        val type = bytes[i]; val len = bytes[i + 1]
        if (type == 0x07 && i + 5 <= bytes.size) return "%02X%02X".format(bytes[i + 3], bytes[i + 4])
        i += 2 + len
    }
    return ""
}

/**
 * Live hotter/colder meter, computed on the phone so it updates on every advert.
 * Same bands as Core's find_devices.proximity; RSSI is noisy, so the range is wide on purpose.
 */
internal object Proximity {
    data class Reading(val band: String, val label: String, val rssi: Int, val lowM: Double, val highM: Double,
                       val trend: String, val confident: Boolean, val fill: Float)

    fun of(rssis: List<Int>, txPower: Int? = null): Reading? {
        if (rssis.isEmpty()) return null
        val recent = rssis.takeLast(5)
        val m = median(recent)
        val (band, label) = when {
            m >= -55 -> "very_close" to "Very close, within about a metre"
            m >= -67 -> "close" to "Close, probably in the same room"
            m >= -80 -> "nearby" to "Nearby, maybe a room or two away"
            else -> "edge" to "At the edge of Bluetooth range"
        }
        val ref = if (txPower != null && txPower in -99..-1) txPower else -59
        val low = max(0.1, 10.0.pow((ref - m) / 35.0))
        val high = min(100.0, max(low, 10.0.pow((ref - m) / 20.0)))
        var trend = "steady"
        if (rssis.size >= 6) {
            val before = median(rssis.takeLast(6).take(3)); val after = median(rssis.takeLast(3))
            if (after - before >= 4) trend = "closer" else if (before - after >= 4) trend = "further"
        }
        val spread = recent.max() - recent.min()
        val fill = ((m + 100).coerceIn(0, 60) / 60f)
        return Reading(band, label, m, round1(low), round1(high), trend, recent.size >= 3 && spread <= 10, fill)
    }

    private fun median(v: List<Int>): Int = v.sorted().let { if (it.size % 2 == 1) it[it.size / 2] else (it[it.size / 2 - 1] + it[it.size / 2]) / 2 }
    private fun round1(x: Double) = round(x * 10) / 10
}
