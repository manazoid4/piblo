package uk.mazworks.piblo

import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import java.net.URLEncoder
import java.util.Locale
import org.json.JSONObject

/** Free, keyless GitHub discovery; screenshot OCR is performed on-device elsewhere.
 * Only a bounded repo path or short phrase leaves the phone. Call on IO dispatcher.
 */
object ScreenshotRepoFinder {
    data class Clue(val query: String, val exactRepo: String? = null)
    data class Repo(val fullName: String, val url: String, val description: String = "",
                    val verified: Boolean = false)
    data class Lookup(val repos: List<Repo>, val message: String, val query: String)

    private val github = Regex(
        """(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_.-]{1,100})(?:\b|/|$)""",
        RegexOption.IGNORE_CASE,
    )
    private val ownerRepo = Regex("""\b([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9_.-]{2,100})\b""")
    private val word = Regex("""[A-Za-z][A-Za-z0-9_.-]{2,29}""")
    private val noise = setOf(
        "instagram","reels","reel","follow","following","followers","likes","liked",
        "comments","comment","share","views","watch","audio","music","original","profile",
        "home","sponsored","and","with","about","from","this","that","your","the",
        "for","new","check","out","link","bio","more","source","code","github",
        "project","repository","open","built","build","made","using","best","tools",
        "tool","free","available","how","make","you","just","what","now","today",
    )
    private val signal = Regex(
        """(?i)\b(?:github|open.source|repo|software|assistant|app|framework|model|cli|automation|local|voice|robot|terminal|plugin|library)\b""",
    )

    fun clue(ocr: String): Clue? {
        val lines = ocr.take(16_000).lineSequence().map(String::trim)
            .filter(String::isNotEmpty).take(120).toList()
        github.find(ocr.take(16_000))?.let {
            val key = it.groupValues[1] + "/" + it.groupValues[2].trimEnd('.', ',')
            return Clue(key, key)
        }
        for ((i, line) in lines.withIndex()) {
            val context = listOfNotNull(lines.getOrNull(i - 1), line, lines.getOrNull(i + 1))
                .joinToString(" ").lowercase(Locale.ROOT)
            if (!context.contains("github") && !context.contains("repo")) continue
            ownerRepo.find(line)?.let {
                val key = it.groupValues[1] + "/" + it.groupValues[2]
                return Clue(key, key)
            }
        }
        val ranked = lines.mapIndexedNotNull { i, line ->
            if (line.contains('@') || line.contains("://") || line.length > 130) return@mapIndexedNotNull null
            val words = word.findAll(line).map { it.value }
                .filter { it.lowercase(Locale.ROOT) !in noise }
                .distinctBy { it.lowercase(Locale.ROOT) }.take(4).toList()
            if (words.isEmpty()) return@mapIndexedNotNull null
            val priority = (if (signal.containsMatchIn(line)) 10 else 0) +
                (if (words.size >= 2) 3 else 0) - (if (i > 30) 2 else 0)
            priority to words
        }.sortedByDescending { it.first }
        val words = ranked.firstOrNull()?.second ?: return null
        return Clue(words.joinToString(" "))
    }

    fun manualQuery(text: String): Clue? {
        github.find(text.trim())?.let {
            val key = it.groupValues[1] + "/" + it.groupValues[2].trimEnd('.', ',')
            return Clue(key, key)
        }
        val words = word.findAll(text.take(140)).map { it.value }.take(4).toList()
        return if (words.isEmpty()) null else Clue(words.joinToString(" "))
    }

    /** One unauthenticated GitHub API request per lookup. Unknown results are never "verified". */
    fun lookup(clue: Clue, request: (String) -> Pair<Int, String> = ::fetch): Lookup {
        val endpoint = if (clue.exactRepo != null) {
            "https://api.github.com/repos/" + clue.exactRepo.split("/").joinToString("/") {
                URLEncoder.encode(it, "UTF-8")
            }
        } else {
            "https://api.github.com/search/repositories?q=" +
                URLEncoder.encode(clue.query + " in:name,description", "UTF-8") + "&per_page=5"
        }
        return try {
            val (status, body) = request(endpoint)
            if (status == 403 || status == 429)
                return Lookup(emptyList(), "GitHub's free search limit was reached. Try again shortly.", clue.query)
            if (status == 404 && clue.exactRepo != null)
                return Lookup(emptyList(), "Repository not found. Edit the search words.", clue.query)
            if (status !in 200..299)
                return Lookup(emptyList(), "GitHub search unavailable (HTTP " + status + ").", clue.query)
            val json = JSONObject(body)
            val arr = json.optJSONArray("items")
            val items = if (clue.exactRepo != null) listOf(json)
                else if (arr == null) emptyList()
                else (0 until minOf(5, arr.length())).mapNotNull(arr::optJSONObject)
            val repos = items.mapNotNull { obj ->
                val name = obj.optString("full_name")
                val url = obj.optString("html_url")
                if (!safeRepo(name, url)) null else Repo(name, url,
                    obj.optString("description").take(160), clue.exactRepo != null)
            }
            Lookup(repos, if (repos.isEmpty()) "No public repositories matched."
                else if (clue.exactRepo != null) "Verified GitHub path seen in screenshot."
                else "Possible matches — confirm the correct project before saving.", clue.query)
        } catch (_: IOException) {
            Lookup(emptyList(), "Offline. Screenshot text was read locally; retry GitHub search later.", clue.query)
        } catch (_: Exception) {
            Lookup(emptyList(), "Could not read GitHub's result. Try a shorter search.", clue.query)
        }
    }

    private fun safeRepo(fullName: String, url: String): Boolean {
        if (fullName.count { it == '/' } != 1) return false
        return try {
            val u = URI(url)
            u.scheme == "https" && u.host == "github.com" && u.userInfo == null &&
                u.path.trim('/') == fullName
        } catch (_: Exception) { false }
    }

    private fun fetch(url: String): Pair<Int, String> {
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = 3500
        c.readTimeout = 5500
        c.instanceFollowRedirects = false
        c.setRequestProperty("Accept", "application/vnd.github+json")
        c.setRequestProperty("User-Agent", "Piblo-RepoFinder/1.0")
        try {
            val status = c.responseCode
            if (status !in 200..299) return status to ""
            val response = c.inputStream.use { it.readNBytes(192_000) }
            return status to response.toString(Charsets.UTF_8)
        } finally { c.disconnect() }
    }
}
