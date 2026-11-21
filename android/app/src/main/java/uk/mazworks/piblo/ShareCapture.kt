package uk.mazworks.piblo

/**
 * Pure, locally testable extraction for Android ACTION_SEND text.
 * Sharing a Reel or GitHub URL does not grant Piblo access to that site's video or code.
 */
object ShareCapture {
    private val urlPattern = Regex("""https?://[^\s<>"']+""", RegexOption.IGNORE_CASE)

    /** Find the first URL without trailing sentence punctuation and without changing its query parameters. */
    fun firstUrl(text: String): String {
        return urlPattern.find(text)?.value?.trimEnd('.', ',', ';', ':', '!', '?', ')', ']', '}', '"')
            .orEmpty().take(2000)
    }

    /** A *label* only. Core remains responsible for canonicalisation, rights and indexing. */
    fun kind(url: String): String = try {
        val host = java.net.URI(url).host?.lowercase()?.removePrefix("www.").orEmpty()
        when {
            host == "github.com" -> "GitHub repository"
            host == "instagram.com" -> "Instagram post or Reel"
            url.isNotEmpty() -> "link"
            else -> "note"
        }
    } catch (_: Exception) {
        "link"
    }
}
