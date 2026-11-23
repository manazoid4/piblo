package uk.mazworks.piblo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import uk.mazworks.piblo.ui.AppUpdate
import java.io.File

class AppUpdateTest {
    private val json = """{"version_code":142,"version_name":"0.2.0-beta+ci42","label":"PR #222 (abc1234)","sha256":"ff","size":5,"staged_at":"x"}"""

    @Test fun parsesCoreManifest() {
        val b = AppUpdate.parse(json)
        assertEquals(142, b.code); assertEquals("PR #222 (abc1234)", b.label); assertEquals(5L, b.size)
    }

    @Test fun onlyNewerBuildsAreOffered() {
        val b = AppUpdate.parse(json)
        assertTrue(AppUpdate.isNewer(b, 3)); assertFalse(AppUpdate.isNewer(b, 142)); assertFalse(AppUpdate.isNewer(b, 150))
    }

    @Test fun hashMatchesCoreSha256() {
        val f = File.createTempFile("apk", ".bin").apply { writeBytes("hello".toByteArray()); deleteOnExit() }
        assertEquals("2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824", AppUpdate.sha256(f))
    }
}
