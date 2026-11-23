package uk.mazworks.piblo.proto

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import uk.mazworks.piblo.ui.nearbyToDevice

class FindDevicesTest {
    private val core = """{"ok":true,"devices":[{"id":"pods","name":"AirPods Pro","kind":"earbuds",
        "providers":{"apple_findmy":{},"bluetooth":{"addresses":["aa:bb:cc:dd:ee:ff"],"apple_model_id":"0e20"}},
        "proximity":{"state":"in_range","label":"Close, probably in the same room"},
        "last_seen":{"at":1,"ago_s":42,"observer":"android:p"},
        "actions":[{"id":"handoff_apple","label":"Open Apple Find My","provider":"apple_findmy","kind":"handoff","url":"https://www.icloud.com/find"},
                   {"id":"scan","label":"Walk","provider":"bluetooth","kind":"scan","url":""}]},
        {"id":"phone-1","name":"Pixel","kind":"phone","providers":{"piblo_phone":{}},"proximity":{"state":"never_seen","label":"Not seen by Piblo yet"},
         "last_seen":null,"actions":[{"id":"ring","label":"Ring","provider":"piblo_phone","kind":"ring","url":""}]}],
        "nearby":[{"label":"An AirPods Pro-type device (identity unverified)","address":"7A:01","name":"","rssi":-61,"bonded":false,
                   "apple":{"family":"apple_audio","model_id":"0E20","model":"AirPods Pro"}}]}"""

    @Test fun parsesCoreOverview() {
        val o = parseFindOverview(core)
        val pods = o.devices[0]
        assertEquals(listOf("AA:BB:CC:DD:EE:FF"), pods.bluetoothAddresses)
        assertEquals("0E20", pods.appleModelId)
        assertEquals("apple_findmy", pods.handoff?.provider)
        assertFalse(pods.canRing)
        assertEquals(42L, pods.lastSeenAgoS)
        assertTrue(o.devices[1].canRing)
        assertNull(o.devices[1].lastSeenAgoS)
        assertEquals("0E20", o.nearby[0].appleModelId)
    }

    @Test fun appleModelFromManufacturerData() {
        assertEquals("0E20", appleModelId("0719010E2000981500"))
        assertEquals("", appleModelId("10050112345678"))
        assertEquals("", appleModelId("zz"))
    }

    @Test fun proximityBandsTrendAndRange() {
        val r = Proximity.of(listOf(-85, -84, -83, -60, -58, -57))!!
        assertEquals("closer", r.trend)
        assertEquals("close", r.band)
        assertTrue(r.lowM <= r.highM)
        assertEquals("very_close", Proximity.of(listOf(-40, -41, -42))!!.band)
        assertEquals("edge", Proximity.of(listOf(-95))!!.band)
        assertFalse(Proximity.of(listOf(-95))!!.confident)
        assertNull(Proximity.of(emptyList()))
    }

    @Test fun sightingsBodyMatchesCoreContract() {
        val body = JSONObject(sightingsJson("android:p", listOf(Sighting(2_000, "AA", "Pods", -50, -59, true, "0719"))))
        assertEquals("android:p", body.getString("observer"))
        val s = body.getJSONArray("sightings").getJSONObject(0)
        assertEquals(2.0, s.getDouble("at"), 0.0)
        assertEquals(-50, s.getInt("rssi"))
        assertTrue(s.getBoolean("bonded"))
        assertEquals("0719", s.getString("apple_data"))
    }

    @Test fun nearbyAppleLookalikeBecomesFindMyDeviceWithoutAddress() {
        val o = parseFindOverview(core)
        val body = JSONObject(nearbyToDevice(o.nearby[0]))
        assertEquals("AirPods Pro", body.getString("name"))
        assertEquals("earbuds", body.getString("kind"))
        val p = body.getJSONObject("providers")
        assertTrue(p.has("apple_findmy"))
        assertFalse(p.getJSONObject("bluetooth").has("addresses"))   // rotating address is never stored as identity
        val bonded = JSONObject(nearbyToDevice(NearbyDevice("Galaxy Buds", "11:22", "Galaxy Buds", -50, true, "")))
        assertEquals("11:22", bonded.getJSONObject("providers").getJSONObject("bluetooth").getJSONArray("addresses").getString(0))
    }
}
