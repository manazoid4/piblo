package uk.mazworks.piblo

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import uk.mazworks.piblo.proto.CoreClient
import uk.mazworks.piblo.proto.HaltState
import uk.mazworks.piblo.proto.Priority
import uk.mazworks.piblo.proto.Proto
import uk.mazworks.piblo.proto.ProtoException
import uk.mazworks.piblo.proto.TEXT_MAX
import uk.mazworks.piblo.proto.TEXT_MODES
import uk.mazworks.piblo.proto.KEY_MAX

/** Fixtures are real responses from Core (host/mazhost) captured by android/tools/gen-fixtures.sh. */
fun fixture(name: String): String =
    ProtoTest::class.java.getResource("/fixtures/$name.json")!!.readText()

class ProtoTest {
    @Test fun summaryIdle() {
        val s = Proto.parseSummary(fixture("summary_idle"))
        assertEquals("idle", s.agent); assertEquals(0, s.count); assertTrue(s.items.isEmpty())
    }

    @Test fun summaryWithPendingApproval() {
        val s = Proto.parseSummary(fixture("summary_pending"))
        assertEquals("needs_you", s.agent)
        val a = s.items.single()
        assertEquals("Bash", a.tool); assertEquals("piblo", a.project); assertEquals(59, a.leftSeconds)
        assertEquals("rm -rf build/ && npm run build", a.summary)
    }

    @Test fun eventsMixedKeepsAlertsAndOtherKinds() {
        val p = Proto.parseEvents(fixture("events_mixed"))
        assertEquals(3, p.last)
        assertEquals(listOf("done", "alert", "alert"), p.events.map { it.kind })
        val sla = p.events[1].alert!!
        assertEquals("sla", sla.kind); assertEquals(Priority.URGENT, sla.priority)
        assertEquals(listOf("ack", "snooze"), sla.actions)
        assertEquals("SLA breach: ticket #123", sla.title)
        assertFalse(sla.isFindPhone)
        assertNull(p.events[0].alert)
    }

    @Test fun emptyEvents() {
        val p = Proto.parseEvents(fixture("events_empty"))
        assertTrue(p.events.isEmpty()); assertEquals(0, p.last)
    }

    @Test fun unknownKindsAndFieldsAreIgnoredNotFatal() {
        val p = Proto.parseEvents("""{"ok":true,"last":9,"events":[{"id":9,"kind":"hologram","text":"x","future":{"a":1}},{"id":8,"kind":"alert","alert":{"id":"al-1","kind":"find_phone","title":"t","priority":"urgent"}}]}""")
        assertEquals(2, p.events.size)
        assertTrue(p.events[1].alert!!.isFindPhone)
        assertEquals(listOf("ack"), p.events[1].alert!!.actions)   // default when the field is missing
    }

    @Test fun findPhoneInterimWireFormIsRecognised() {
        val e = Proto.parseEvents("""{"events":[{"id":1,"kind":"alert","alert":{"id":"al-2","source":"find_phone","kind":"info","title":"Find my phone"}}],"last":1}""")
        assertTrue(e.events.single().alert!!.isFindPhone)
    }

    @Test fun garbageThrowsProtoException() {
        try { Proto.parseSummary("<html>"); fail() } catch (_: ProtoException) {}
        try { Proto.parseEvents("not json"); fail() } catch (_: ProtoException) {}
    }

    @Test fun errorDetails() {
        assertEquals("authentication_required", Proto.parseError(fixture("error_401")))
        assertEquals("request_not_found", Proto.parseError(fixture("decide_not_found")))
        assertEquals("action_not_offered", Proto.parseError(fixture("alert_snooze_not_offered")))
        assertEquals("invalid_or_expired_code", Proto.parseError(fixture("pair_claim_bad")))
        assertEquals("bad_request", Proto.parseError("""{"detail":[{"loc":["body"],"msg":"x"}]}"""))   // FastAPI 422 shape
        assertEquals("", Proto.parseError("garbage"))
    }

    @Test fun claimAndPostResponses() {
        assertTrue(Proto.parseToken(fixture("pair_claim_ok")).isNotEmpty())
        assertEquals("al-88c94838", Proto.parsePostedId(fixture("alert_posted")))
    }

    @Test fun alertJsonTruncatesToCoreLimits() {
        val j = JSONObject(Proto.alertJson("s".repeat(80), "info", "t".repeat(100), "b".repeat(500), Priority.HIGH))
        assertEquals(40, j.getString("source").length); assertEquals(60, j.getString("title").length); assertEquals(200, j.getString("body").length)
        assertEquals("high", j.getString("priority")); assertEquals("ack", j.getJSONArray("actions").getString(0))
    }

    @Test fun truncateNeverSplitsASurrogatePair() {
        val s = "a" + "😀".repeat(70)   // 71 code points, 141 UTF-16 units
        val t = Proto.truncate(s, 60)
        assertEquals(60, t.codePointCount(0, t.length))
        assertFalse(Character.isHighSurrogate(t.last()))
    }

    @Test fun emptyTitleGetsPlaceholder() {
        assertEquals("(no title)", JSONObject(Proto.alertJson("x", "info", "  ", "", Priority.LOW)).getString("title"))
    }

    @Test fun decideJsonUsesCoreWords() {
        assertEquals("allow", JSONObject(Proto.decideJson("a1", true)).getString("decision"))
        assertEquals("deny", JSONObject(Proto.decideJson("a1", false)).getString("decision"))
    }

    @Test fun textJsonIsLiteralAndTruncated() {
        val id = "e5c1b3f0-1234-4abc-9def-000000000000"
        val j = JSONObject(Proto.textJson(id, "t".repeat(700), "insert"))
        assertEquals(id, j.getString("id"))
        assertEquals(TEXT_MAX, j.getString("text").length)   // capped at 500, never rejected
        assertEquals("insert", j.getString("mode"))
        assertEquals(setOf("insert", "replace", "send"), TEXT_MODES)
    }

    @Test fun keysJsonTrimsCapsAndEscapes() {
        assertEquals("enter", JSONObject(Proto.keysJson("  enter  ")).getString("keys"))
        assertEquals(KEY_MAX, JSONObject(Proto.keysJson("x".repeat(600))).getString("keys").length)
        // quotes and backslashes must round-trip through JSON escaping, not break the body
        assertEquals("""a"b\c""", JSONObject(Proto.keysJson("""a"b\c""")).getString("keys"))
    }

    @Test fun parseTextStatusAndId() {
        assertEquals("pending", Proto.parseTextStatus("""{"id":"x","status":"pending"}"""))
        assertEquals("inserted", Proto.parseTextStatus("""{"id":"x","status":"inserted"}"""))
        assertEquals("pending", Proto.parseTextStatus("""{"id":"x"}"""))  // no status yet = still pending
        assertEquals("e5c1", Proto.parseTextId("""{"id":"e5c1"}"""))
        // Core's answer for emoji / accents / other scripts, and the device ack for the same
        assertEquals("unsupported_characters", Proto.parseError("""{"detail":"unsupported_characters"}"""))
        assertEquals("unsupported", Proto.parseTextStatus("""{"id":"x","status":"unsupported"}"""))
    }

    @Test fun normalizeBase() {
        assertEquals("http://192.168.1.20:8787", CoreClient.normalizeBase("192.168.1.20"))
        assertEquals("http://pc:9000", CoreClient.normalizeBase(" pc:9000/ "))
        assertEquals("https://core.example.net", CoreClient.normalizeBase("https://core.example.net"))
        assertNull(CoreClient.normalizeBase("")); assertNull(CoreClient.normalizeBase("ftp://x")); assertNull(CoreClient.normalizeBase("a b"))
        assertNotNull(CoreClient.normalizeBase("localhost"))
        assertEquals("http://100.101.102.103:8787", CoreClient.normalizeBase("100.101.102.103"))
        assertEquals("http://piblo.local:8787", CoreClient.normalizeBase("piblo.local"))
        assertNull(CoreClient.normalizeBase("http://example.com:8787"))
        assertNull(CoreClient.normalizeBase("http://8.8.8.8:8787"))
        assertEquals("https://example.com", CoreClient.normalizeBase("https://example.com"))
    }

    @Test fun shareKeepsTheWholeTextUpToCoreLimit() {
        val long = "a".repeat(5_000)
        assertEquals(5_000, JSONObject(Proto.shareJson(long, "t", "", "id1")).getString("text").length)
        val huge = "b".repeat(60_000)
        assertEquals(uk.mazworks.piblo.proto.SHARE_TEXT_MAX, JSONObject(Proto.shareJson(huge, "t", "", "id2")).getString("text").length)
    }

    @Test fun shareBoundsTitleAndUrlToCoreLimits() {
        val o = JSONObject(Proto.shareJson("x", "t".repeat(300), "https://e.com/" + "u".repeat(3000), "id3"))
        assertEquals(uk.mazworks.piblo.proto.SHARE_TITLE_MAX, o.getString("title").length)
        assertEquals(uk.mazworks.piblo.proto.SHARE_URL_MAX, o.getString("url").length)
    }

    @Test fun shareDrainsOnReconnectOrRequestOnly() {
        val on = uk.mazworks.piblo.hub.LinkState.ONLINE
        val off = uk.mazworks.piblo.hub.LinkState.OFFLINE
        assertTrue(uk.mazworks.piblo.hub.shouldDrainShares(on, off, false))   // just reconnected
        assertTrue(uk.mazworks.piblo.hub.shouldDrainShares(on, null, false))  // first online cycle
        assertTrue(uk.mazworks.piblo.hub.shouldDrainShares(on, on, true))     // new enqueue
        assertFalse(uk.mazworks.piblo.hub.shouldDrainShares(on, on, false))   // steady state: no re-send of a slow /share
        assertFalse(uk.mazworks.piblo.hub.shouldDrainShares(off, off, true))  // never while offline
    }

    @Test fun doubleTappedNoteIsNotQueuedTwice() {
            val e = uk.mazworks.piblo.data.ShareEntry(text = "buy milk", title = "Note from phone", url = "", createdAt = 1)
            assertTrue(uk.mazworks.piblo.data.hasQueuedShare(listOf(e), "buy milk", "Note from phone"))
            assertFalse(uk.mazworks.piblo.data.hasQueuedShare(listOf(e), "buy eggs", "Note from phone"))
            assertFalse(uk.mazworks.piblo.data.hasQueuedShare(emptyList(), "buy milk", "Note from phone"))
    }

        @Test fun parseHaltStateReadsHaltedAndUntil() {
            val hs = Proto.parseHaltState("{\"halted\":true,\"until\":\"2025-01-01T12:00:00Z\"}")
            assertTrue(hs.halted)
            assertEquals("2025-01-01T12:00:00Z", hs.until)

            val hs2 = Proto.parseHaltState("{\"halted\":false}")
            assertFalse(hs2.halted)
            assertNull(hs2.until)

            val hs3 = Proto.parseHaltState("{\"halted\":true,\"until\":\"\"}")
            assertTrue(hs3.halted)
            assertNull(hs3.until)   // blank string -> null

            assertNull(Proto.parseHaltState("{\"halted\":false,\"until\":null}").until)  // Core sends null when not halted
        }

        @Test fun haltJsonEncodesOnAndOptionalMinutes() {
            val j = JSONObject(Proto.haltJson(true))
            assertTrue(j.getBoolean("on"))
            assertFalse(j.has("minutes"))

            val j2 = JSONObject(Proto.haltJson(false, 30))
            assertFalse(j2.getBoolean("on"))
            assertEquals(30, j2.getInt("minutes"))
        }
}
