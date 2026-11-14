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
    data class TestShare(val id: Long, val text: String, val title: String, val url: String,
                         val retries: Int = 0, val idempotencyId: String = "")

    /** In-memory share queue mirroring the DAO operations. */
    class MemShareQueue {
        private val entries = mutableListOf<TestShare>()
        private var nextId = 1L

        fun enqueue(text: String, title: String, url: String, idempotencyId: String): Long {
            val e = TestShare(nextId, text, title, url, 0, idempotencyId)
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
        val sentJsons = CopyOnWriteArrayList<String>()
        private var callCount = 0

        fun postText(id: String, text: String) {
            callCount++
            if (fail || callCount > failOnNth) throw IOException("simulated failure")
            sentTexts.add(text)
        }

        fun share(json: String) {
            callCount++
            if (fail || callCount > failOnNth) throw IOException("simulated failure")
            sentJsons.add(json)
        }
    }

    @Test
    fun drainSendsAllPendingShares() {
        val queue = MemShareQueue()
        queue.enqueue("hello", "Greeting", "", "id-1")
        queue.enqueue("https://example.com", "Link", "https://example.com", "id-2")
        queue.enqueue("check this out", "Note", "", "id-3")

        val transport = FakeTransport()
        drain(queue, transport)

        assertEquals(3, transport.sentJsons.size)
        assertTrue(queue.pendingShares().isEmpty())
    }

    @Test
    fun drainContinuesAfterPermanentFailure() {
        val queue = MemShareQueue()
        queue.enqueue("first", "", "", "id-1")
        queue.enqueue("second", "", "", "id-2")
        queue.enqueue("third", "", "", "id-3")

        // Fail on the second call (sends "first", then fails on "second")
        val transport = FakeTransport(failOnNth = 1)
        drain(queue, transport)

        // "first" was sent; "second" and "third" remain, "second" retry incremented
        assertEquals(1, transport.sentJsons.size)
        val pending = queue.pendingShares()
        assertEquals(2, pending.size)
        assertEquals("second", pending[0].text)
        assertTrue(pending.any { it.id == 2L && it.retries == 1 })

        // Next drain: sends "second" successfully then "third"
        val transport2 = FakeTransport()
        drain(queue, transport2)
        assertEquals(listOf("second", "third"), transport2.sentJsons)
        assertTrue(queue.pendingShares().isEmpty())
    }

    @Test
    fun drainEvictsAfterMaxRetries() {
        val queue = MemShareQueue()
        queue.enqueue("stubborn", "Stubborn Share", "", "id-1")
        // Mark it as already retried 3 times (exhausted)
        queue.markRetry(1)
        queue.markRetry(1)
        queue.markRetry(1)

        val transport = FakeTransport(fail = true)
        drain(queue, transport)

        // Since retries >= 3, should be evicted despite the failure
        assertTrue(queue.pendingShares().isEmpty())
        assertTrue(transport.sentJsons.isEmpty())
    }

    @Test
    fun drainEmptyQueueDoesNothing() {
        val queue = MemShareQueue()
        val transport = FakeTransport()
        drain(queue, transport)
        assertTrue(transport.sentJsons.isEmpty())
    }

    @Test
    fun drainPreservesOrder() {
        val queue = MemShareQueue()
        queue.enqueue("first", "", "", "id-1")
        queue.enqueue("second", "", "", "id-2")
        queue.enqueue("third", "", "", "id-3")
        val transport = FakeTransport()
        drain(queue, transport)
        assertEquals(listOf("first", "second", "third"), transport.sentJsons)
    }

    @Test
    fun idempotencyIdIsStableAcrossRetries() {
        val queue = MemShareQueue()
        queue.enqueue("text", "title", "", "stable-id-123")
        // Simulate a failed send: idempotencyId stays the same
        val transport = FakeTransport(fail = true)
        drain(queue, transport)
        val s = queue.pendingShares()[0]
        assertEquals("stable-id-123", s.idempotencyId)
        assertEquals(1, s.retries)
    }

    /** Helper that mirrors drainShareQueue in PibloApp. */
    private fun drain(queue: MemShareQueue, transport: FakeTransport, maxRetries: Int = 3) {
        val pending = queue.pendingShares()
        for (s in pending) {
            try {
                transport.share("""{"text":"${s.text}","title":null,"url":null,"idempotency_id":"${s.idempotencyId}"}""")
                queue.dequeue(s.id)
            } catch (e: IOException) {
                queue.markRetry(s.id)
                if (s.retries + 1 >= maxRetries) {
                    queue.dequeue(s.id)
                }
                continue
            }
        }
    }
}

