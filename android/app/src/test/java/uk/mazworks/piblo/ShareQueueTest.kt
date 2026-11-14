package uk.mazworks.piblo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Tests for the offline share queue: queued items are drained in order,
 * succeed, retried on failure, and evicted after max retries.
 * Uses an in-memory queue and a fake transport to avoid Room/Android.
 */
class ShareQueueTest {

    /** Simplified in-memory share entry matching the DB schema's relevant fields. */
    data class TestShare(val id: Long, val text: String, val title: String, val url: String, val retries: Int)

    /** In-memory share queue mirroring the DAO operations. */
    class MemShareQueue {
        private val entries = mutableListOf<TestShare>()
        private var nextId = 1L

        fun enqueue(text: String, title: String, url: String): Long {
            val e = TestShare(nextId, text, title, url, 0)
            entries.add(e)
            return nextId.also { nextId++ }
        }

        fun pendingShares(): List<TestShare> = entries.toList().sortedBy { it.id }

        fun dequeue(id: Long) { entries.removeAll { it.id == id } }

        fun markRetry(id: Long) {
            entries.replaceAll { if (it.id == id) it.copy(retries = it.retries + 1) else it }
        }

        fun clear() { entries.clear() }
        fun size() = entries.size
    }

    /** Fake transport that records calls and conditionally fails. */
    class FakeTransport(private val failOnNth: Int = Int.MAX_VALUE, private val fail: Boolean = false) {
        val sentTexts = CopyOnWriteArrayList<String>()
        private var callCount = 0

        fun postText(id: String, text: String) {
            callCount++
            if (fail || callCount > failOnNth) throw IOException("simulated failure")
            sentTexts.add(text)
        }
    }

    @Test
    fun drainSendsAllPendingShares() {
        val queue = MemShareQueue()
        queue.enqueue("hello", "Greeting", "")
        queue.enqueue("https://example.com", "Link", "https://example.com")
        queue.enqueue("check this out", "Note", "")

        val transport = FakeTransport()
        drain(queue, transport)

        assertEquals(3, transport.sentTexts.size)
        assertEquals(listOf("hello", "https://example.com", "check this out"), transport.sentTexts)
        assertTrue(queue.pendingShares().isEmpty())
    }

    @Test
    fun drainStopsOnFirstFailureAndRetriesLater() {
        val queue = MemShareQueue()
        queue.enqueue("first", "", "")
        queue.enqueue("second", "", "")
        queue.enqueue("third", "", "")

        // Fail on the second call
        val transport = FakeTransport(failOnNth = 1)
        drain(queue, transport)

        // Only "first" was sent; "second" and "third" remain
        assertEquals(1, transport.sentTexts.size)
        assertEquals(listOf("first"), transport.sentTexts)

        val pending = queue.pendingShares()
        assertEquals(2, pending.size)
        assertEquals("second", pending[0].text)
        // second's retry was incremented
        assertTrue(pending.any { it.id == 2L && it.retries == 1 })

        // Next drain: sends "second" successfully then "third"
        val transport2 = FakeTransport()
        drain(queue, transport2)
        assertEquals(listOf("second", "third"), transport2.sentTexts)
        assertTrue(queue.pendingShares().isEmpty())
    }

    @Test
    fun drainEvictsAfterMaxRetries() {
        val queue = MemShareQueue()
        queue.enqueue("stubborn", "Stubborn Share", "")
        // Mark it as already retried 3 times (exhausted)
        queue.markRetry(1)
        queue.markRetry(1)
        queue.markRetry(1)

        val transport = FakeTransport(fail = true)
        drain(queue, transport)

        // Since retries >= 3, should be evicted despite the failure
        assertTrue(queue.pendingShares().isEmpty())
        // But also no successful sent (transport always failed)
        assertTrue(transport.sentTexts.isEmpty())
    }

    @Test
    fun drainEmptyQueueDoesNothing() {
        val queue = MemShareQueue()
        val transport = FakeTransport()
        drain(queue, transport)
        assertTrue(transport.sentTexts.isEmpty())
    }

    @Test
    fun drainPreservesOrder() {
        val queue = MemShareQueue()
        queue.enqueue("first", "", "")
        queue.enqueue("second", "", "")
        queue.enqueue("third", "", "")
        val transport = FakeTransport()
        drain(queue, transport)
        assertEquals(listOf("first", "second", "third"), transport.sentTexts)
    }

    /** Helper that mirrors drainShareQueue in PibloApp. */
    private fun drain(queue: MemShareQueue, transport: FakeTransport, maxRetries: Int = 3) {
        val pending = queue.pendingShares()
        for (s in pending) {
            try {
                transport.postText("test-id", s.text)
                queue.dequeue(s.id)
            } catch (e: Exception) {
                queue.markRetry(s.id)
                if (s.retries >= maxRetries) {
                    queue.dequeue(s.id)
                }
                break
            }
        }
    }
}