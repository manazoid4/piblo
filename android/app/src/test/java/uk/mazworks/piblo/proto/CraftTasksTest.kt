package uk.mazworks.piblo.proto

import org.json.JSONException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class CraftTasksTest {
    @Test fun readsExistingBoardRows() {
        val tasks = parseCraftTasks(
            """{"ok":true,"tasks":[
                {"id":"mc-laptop-1","agent":"Codex","title":"Check Android","status":"running","progress":50,"detail":"Tests"},
                {"id":"mc-laptop-2","title":"Build APK","status":"waiting","progress":null}
            ]}""",
        )
        assertEquals(CraftTask("mc-laptop-1", "Check Android", "Codex", "running", 50, "Tests"), tasks[0])
        assertEquals("Running", tasks[0].statusLabel)
        assertEquals("", tasks[1].agent)
        assertNull(tasks[1].progress)
    }

    @Test fun acceptsEmptyBoard() {
        assertEquals(emptyList<CraftTask>(), parseCraftTasks("""{"tasks":[]}"""))
    }

    @Test(expected = JSONException::class)
    fun rejectsMissingTaskList() {
        parseCraftTasks("""{"ok":true}""")
    }
}
