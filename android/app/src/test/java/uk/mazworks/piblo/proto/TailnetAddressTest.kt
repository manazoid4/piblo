package uk.mazworks.piblo.proto

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TailnetAddressTest {
    @Test fun recognisesTailscaleAddressForms() {
        assertTrue(CoreClient.isTailnetAddress("http://100.64.10.20:8787"))
        assertTrue(CoreClient.isTailnetAddress("https://piblo.example.ts.net"))
        assertTrue(CoreClient.isTailnetAddress("http://[fd7a:115c:a1e0::1234]:8787"))
        assertFalse(CoreClient.isTailnetAddress("http://100.128.10.20:8787"))
        assertFalse(CoreClient.isTailnetAddress("http://piblo.ts.net.example.com"))
        assertFalse(CoreClient.isTailnetAddress("http://192.168.1.2:8787"))
        assertFalse(CoreClient.isTailnetAddress(""))
    }
}
