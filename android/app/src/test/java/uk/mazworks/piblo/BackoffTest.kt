package uk.mazworks.piblo

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import uk.mazworks.piblo.hub.Backoff

class BackoffTest {

    @Test
    fun firstDelayIsMinMsWithJitter() {
        val b = Backoff(minMs = 2000, maxMs = 30000)
        // With 25% jitter subtracted, delay should be in [minMs*0.75, minMs] = [1500, 2000]
        // Floor is 1000, so actual range [1000, 2000]
        repeat(100) {
            val d = b.next()
            assertTrue("delay $d should be >= 1000", d >= 1000)
            assertTrue("delay $d should be <= 2000", d <= 2000)
        }
    }

    @Test
    fun delayDoublesEachStepUpToCap() {
        val b = Backoff(minMs = 2000, maxMs = 30000)
        val first = b.next()
        assertTrue("first $first should be in [1000, 2000]", first in 1000..2000)

        // After first, cur is 4000 (doubled)
        val second = b.next()
        assertTrue("second $second should be in [2000, 4000]", second in 2000..4000)

        // After second, cur is 8000
        val third = b.next()
        assertTrue("third $third should be in [4000, 8000]", third in 4000..8000)
    }

    @Test
    fun delayCapsAtMaxMs() {
        val b = Backoff(minMs = 2000, maxMs = 30000)
        // Double: 2k -> 4k -> 8k -> 16k -> 32k (capped at 30k)
        repeat(4) { b.next() }
        // cur is now 30_000 (capped)
        val capped = b.next()
        // With 25% jitter: in [22500, 30000]
        assertTrue("capped delay $capped should be >= 22500", capped >= 22500)
        assertTrue("capped delay $capped should be <= 30000", capped <= 30000)

        // Subsequent calls stay at cap
        repeat(10) {
            val d = b.next()
            assertTrue("capped delay $d should be >= 22500", d >= 22500)
            assertTrue("capped delay $d should be <= 30000", d <= 30000)
        }
    }

    @Test
    fun resetBringsBackToMinMs() {
        val b = Backoff(minMs = 2000, maxMs = 30000)
        b.next() // advances to 4000
        b.next() // advances to 8000
        b.next() // advances to 16000
        b.reset()
        // After reset, next call should be back at minMs with jitter
        val after = b.next()
        assertTrue("after reset $after should be in [1000, 2000]", after in 1000..2000)
    }

    @Test
    fun currentRawIsCorrect() {
        val b = Backoff(minMs = 2000, maxMs = 30000)
        assertEquals(2000, b.currentRaw()) // initial
        b.next() // advances to 4000
        assertEquals(4000, b.currentRaw())
        b.next() // advances to 8000
        assertEquals(8000, b.currentRaw())
        b.reset()
        assertEquals(2000, b.currentRaw())
    }

    @Test
    fun capHonoredWhenMinMsIsAboveMaxMs() {
        val b = Backoff(minMs = 5000, maxMs = 10000)
        b.next()
        assertEquals(10000, b.currentRaw()) // capped immediately
        repeat(5) {
            val d = b.next()
            assertTrue("delay $d should be in [5000, 10000]", d in 5000..10000)
            assertEquals(10000, b.currentRaw())
        }
    }
}