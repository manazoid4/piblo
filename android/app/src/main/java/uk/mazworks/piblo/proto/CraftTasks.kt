package uk.mazworks.piblo.proto

import org.json.JSONObject

internal data class CraftTask(
    val id: String,
    val title: String,
    val agent: String,
    val status: String,
    val progress: Int?,
    val detail: String,
) {
    val statusLabel: String get() = when (status) {
        "waiting" -> "Waiting"
        "running" -> "Running"
        "done" -> "Done"
        "failed" -> "Failed"
        else -> "Unknown status"
    }
}

internal fun parseCraftTasks(body: String): List<CraftTask> {
    val tasks = JSONObject(body).getJSONArray("tasks")
    return List(tasks.length()) { index ->
        val task = tasks.getJSONObject(index)
        CraftTask(
            id = task.getString("id"),
            title = task.getString("title"),
            agent = if (task.isNull("agent")) "" else task.getString("agent"),
            status = task.getString("status"),
            progress = task.optInt("progress", -1).takeIf { it in 0..100 },
            detail = if (task.isNull("detail")) "" else task.getString("detail"),
        )
    }
}
