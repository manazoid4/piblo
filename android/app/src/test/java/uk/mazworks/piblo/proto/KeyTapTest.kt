package uk.mazworks.piblo.proto

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class KeyTapTest {
    private val p = "0123456789abcdef0123456789ABCDEF"
    private val c = "0011223344556677"

    @Test fun acceptsTheCardUrl() {
        assertTrue(KeyTap.isKeyUrl("https://piblo.co.uk/k?p=$p&c=$c"))
        assertTrue(KeyTap.isKeyUrl("https://piblo.co.uk/k?c=$c&p=$p"))
    }

    @Test fun rejectsAnythingElse() {
        assertFalse(KeyTap.isKeyUrl(null))
        assertFalse(KeyTap.isKeyUrl("http://piblo.co.uk/k?p=$p&c=$c"))
        assertFalse(KeyTap.isKeyUrl("https://evil.example/k?p=$p&c=$c"))
        assertFalse(KeyTap.isKeyUrl("https://piblo.co.uk.evil.example/k?p=$p&c=$c"))
        assertFalse(KeyTap.isKeyUrl("https://piblo.co.uk/k?p=${p.take(30)}&c=$c"))
        assertFalse(KeyTap.isKeyUrl("https://piblo.co.uk/k?p=$p"))
        assertFalse(KeyTap.isKeyUrl("https://piblo.co.uk/k?p=$p&c=${"z".repeat(16)}"))
    }

    @Test fun sendsTheFullUrlUnchanged() {
        val url = "https://piblo.co.uk/k?p=$p&c=$c"
        assertEquals(url, JSONObject(KeyTap.json(url)).getString("url"))
    }

    @Test fun messagesAreShortAndPlain() {
        assertEquals("Piblo Key accepted", KeyTap.message(null))
        assertTrue(KeyTap.message("replay").contains("already used"))
        assertTrue(KeyTap.message("unknown_key").contains("isn't added"))
        assertEquals("Piblo Key not accepted", KeyTap.message("something_new"))
    }

    @Test fun readsCoreAuthErrorsWithoutADetailField() {
        assertEquals("bad_mac", Proto.parseError("""{"error":"bad_mac","message":"x"}"""))
        assertEquals("request_not_found", Proto.parseError("""{"detail":"request_not_found"}"""))
    }
}
