package uk.mazworks.piblo

import java.io.IOException
import org.junit.Assert.*
import org.junit.Test

/** No screenshot files, paid models, networks or credentials in these JVM tests. */
class ScreenshotRepoFinderTest {
    @Test fun extractsExactRepoFromScreenshotText() {
        val clue = ScreenshotRepoFinder.clue(
            "Instagram  Following\nHow I built a helper\nSource code:\nhttps://github.com/Storytold/craft-anything\nLike  Comment",
        )!!
        assertEquals("Storytold/craft-anything", clue.exactRepo)
    }

    @Test fun searchesWithShortClueNotWholeScreenshotOrInstagramHandle() {
        val clue = ScreenshotRepoFinder.clue(
            "@someprivateprofile\nFollowing\n10:32\nOpen source voice automation WhisperLive\n" +
            "Followers 1900\nComment and like",
        )!!
        assertNull(clue.exactRepo)
        assertTrue(clue.query.length <= 120)
        assertFalse(clue.query.contains("someprivateprofile"))
        assertFalse(clue.query.contains("Following"))
        assertTrue(clue.query.contains("WhisperLive"))
    }

    @Test fun queryIsBoundedAndGitHubSearchIsSingleUnauthenticatedRequest() {
        val clue = ScreenshotRepoFinder.manualQuery("A Useful Local AI Agent With 30 Features")!!
        var calls = 0
        val result = ScreenshotRepoFinder.lookup(clue) { url ->
            calls++
            assertTrue(url.startsWith("https://api.github.com/search/repositories?q="))
            assertFalse(url.contains("Features")) // max four tokens only
            200 to """{"items":[{"full_name":"demo/agent","html_url":"https://github.com/demo/agent","description":"An agent"}]}"""
        }
        assertEquals(1, calls)
        assertEquals(1, result.repos.size)
        assertFalse(result.repos[0].verified)
        assertTrue(result.message.contains("Possible"))
    }

    @Test fun exactRepoMustBeVerifiedByOfficialGitHubApi() {
        val clue = ScreenshotRepoFinder.manualQuery("https://github.com/storytold/craft-anything")!!
        val result = ScreenshotRepoFinder.lookup(clue) { url ->
            assertEquals("https://api.github.com/repos/storytold/craft-anything", url)
            200 to """{"full_name":"storytold/craft-anything","html_url":"https://github.com/storytold/craft-anything"}"""
        }
        assertTrue(result.repos.single().verified)
    }

    @Test fun rejectsSpoofedSearchResults() {
        val result = ScreenshotRepoFinder.lookup(ScreenshotRepoFinder.Clue("voice agent")) {
            200 to """{"items":[{"full_name":"demo/repo","html_url":"https://github.com.evil.test/demo/repo"},{"full_name":"safe/app","html_url":"https://github.com/safe/app"}]}"""
        }
        assertEquals(listOf("safe/app"), result.repos.map { it.fullName })
    }

    @Test fun handlesOfflineAndFreeRateLimitWithoutInventingRepos() {
        val clue = ScreenshotRepoFinder.Clue("voice")
        val offline = ScreenshotRepoFinder.lookup(clue) { throw IOException("offline") }
        assertTrue(offline.repos.isEmpty())
        assertTrue(offline.message.contains("Offline"))
        val limited = ScreenshotRepoFinder.lookup(clue) { 403 to "" }
        assertTrue(limited.repos.isEmpty())
        assertTrue(limited.message.contains("limit"))
    }

    @Test fun noClueAndManualFallback() {
        assertNull(ScreenshotRepoFinder.clue("Instagram\nFollowing\nLike\nComments"))
        assertEquals("whisperlive", ScreenshotRepoFinder.manualQuery("whisperlive")?.query)
    }
}
