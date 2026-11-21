package uk.mazworks.piblo

import org.junit.Assert.assertEquals
import org.junit.Test

class ShareCaptureTest {
    @Test fun githubLinkFromSentenceIsDetected() {
        val url = ShareCapture.firstUrl("Useful tool: https://github.com/storytold/craft-anything/tree/main).")
        assertEquals("https://github.com/storytold/craft-anything/tree/main", url)
        assertEquals("GitHub repository", ShareCapture.kind(url))
    }

    @Test fun instagramReelIsRecognisedAsLinkOnly() {
        val url = ShareCapture.firstUrl("Watch this: https://www.instagram.com/reel/ABC123/?igsh=abc")
        assertEquals("https://www.instagram.com/reel/ABC123/?igsh=abc", url)
        assertEquals("Instagram post or Reel", ShareCapture.kind(url))
    }

    @Test fun notesWithoutLinksStayNotes() {
        assertEquals("", ShareCapture.firstUrl("My voice idea for Piblo"))
        assertEquals("note", ShareCapture.kind(""))
    }

    @Test fun lookalikeDomainsAreNotCategorisedAsInstagramOrGitHub() {
        assertEquals("link", ShareCapture.kind("https://github.com.evil.example/a/b"))
        assertEquals("link", ShareCapture.kind("https://instagram.com.evil.example/reel/x"))
    }
}
