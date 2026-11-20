package uk.mazworks.piblo

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import uk.mazworks.piblo.proto.Api

/** Parsers must survive real Core bodies (and older/newer ones) without crashing. */
class ApiTypesTest {

    @Test
    fun deviceOnlineParsesBatteryWifiAndHub() {
        val raw = JSONObject()
            .put("device", JSONObject().put("version", "1.5.2").put("app", "home").put("battery", 73)
                .put("charging", true).put("wifi_connected", true).put("wifi_ssid", "MazNet"))
            .put("online", true)
            .put("hub_fw", JSONObject().put("version", "1.5.3"))
            .put("update_available", true)
            .toString()
        val d = Api.device(raw)
        assertTrue(d.online)
        assertEquals(73, d.battery)
        assertTrue(d.charging)
        assertEquals("MazNet", d.wifiSsid)
        assertEquals("1.5.2", d.version)
        assertEquals("1.5.3", d.hubVersion)
        assertTrue(d.updateAvailable)
    }

    @Test
    fun deviceWithoutVersionIsOffline() {
        val d = Api.device(JSONObject().put("device", JSONObject().put("error", "timeout")).toString())
        assertFalse(d.online)
        assertNull(d.battery)
    }

    @Test
    fun deviceNeverCrashesOnGarbage() {
        val d = Api.device("not json")
        assertFalse(d.online)
    }

    @Test
    fun historyReadsNotifications() {
        val raw = JSONObject().put("notifications", JSONArray()
            .put(JSONObject().put("id", "ev:1").put("kind", "done").put("text", "finished").put("source", "claude").put("ts", 100)))
            .toString()
        val list = Api.history(raw)
        assertEquals(1, list.size)
        assertEquals("finished", list[0].text)
        assertEquals(100L, list[0].at)
    }

    @Test
    fun promisesCardsCarryActions() {
        val raw = JSONObject().put("cards", JSONArray()
            .put(JSONObject().put("id", "p1").put("state", "due").put("title", "Call").put("when", "10:00")
                .put("actions", JSONArray().put("done").put("later"))))
            .put("counts", JSONObject().put("due", 1).put("waiting", 0).put("done", 0))
            .put("empty", false)
            .toString()
        val p = Api.promisesToday(raw)
        assertEquals(1, p.cards.size)
        assertEquals(listOf("done", "later"), p.cards[0].actions)
        assertEquals(1, p.due)
    }

    @Test
    fun haltToleratesNullUntil() {
        val h = Api.halt(JSONObject().put("halted", true).put("until", JSONObject.NULL).toString())
        assertTrue(h!!.halted)
        assertNull(h.until)
    }

    @Test
    fun flowDictionaryReadsBothShapes() {
        val entries = Api.flowDictionary(JSONObject().put("entries", JSONArray()
            .put(JSONObject().put("term", "Rampage").put("aliases", JSONArray().put("ram page")))).toString())
        assertEquals("Rampage", entries[0].term)
        assertEquals(listOf("ram page"), entries[0].aliases)

        val words = Api.flowDictionary(JSONObject().put("words", JSONArray().put("Nod")).toString())
        assertEquals("Nod", words[0].term)
    }

    @Test
    fun healthReadsFirmwareObject() {
        val raw = JSONObject().put("version", "1.5.2")
            .put("fw_latest", JSONObject().put("version", "1.5.3"))
            .put("core", JSONObject().put("ok", true))
            .toString()
        val h = Api.health(raw)
        assertEquals("1.5.2", h.version)
        assertEquals("1.5.3", h.fwLatest)
        assertTrue(h.coreEnabled)
    }

    @Test
    fun pairStateApprovedCarriesToken() {
        val s = Api.pairState(JSONObject().put("status", "approved").put("token", "abc").put("port", 8787).toString())
        assertEquals("approved", s!!.status)
        assertEquals("abc", s.token)
        assertNull(Api.pairState(JSONObject().put("status", "pending").toString())!!.token)
    }
}
