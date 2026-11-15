package uk.mazworks.piblo.data

import android.content.Context
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Delete
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import kotlinx.coroutines.flow.Flow

@Entity(tableName = "notes")
data class Note(@PrimaryKey(autoGenerate = true) val id: Long = 0, val text: String, val createdAt: Long)

/** kind: "reminder" or "timer" (a timer is just a reminder a few minutes away). */
@Entity(tableName = "reminders")
data class Reminder(@PrimaryKey(autoGenerate = true) val id: Long = 0, val text: String, val atMs: Long, val kind: String = "reminder", val done: Boolean = false)

/** Everything that was shown on Piblo, newest first in the Messages tab. Capped at MAX_MESSAGES rows. */
@Entity(tableName = "messages")
data class Message(@PrimaryKey(autoGenerate = true) val id: Long = 0, val atMs: Long, val source: String, val title: String, val body: String, val priority: String)

/** A share queued while offline, sent when the Core link reconnects. */
@Entity(tableName = "share_queue")
data class ShareEntry(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val text: String,
    val title: String,
    val url: String,
    val createdAt: Long,
    val retries: Int = 0,
    val idempotencyId: String = "",
)

const val MAX_MESSAGES = 200
const val MAX_NOTES = 500
const val MAX_SHARE_RETRIES = 3
const val MAX_SHARE_QUEUE = 50

@Dao
interface PibloDao {
    @Query("SELECT * FROM notes ORDER BY createdAt DESC") fun notes(): Flow<List<Note>>
    @Insert suspend fun insert(n: Note): Long
    @Delete suspend fun delete(n: Note)
    @Query("SELECT COUNT(*) FROM notes") suspend fun noteCount(): Int

    @Query("SELECT * FROM reminders ORDER BY done ASC, atMs ASC") fun reminders(): Flow<List<Reminder>>
    @Query("SELECT * FROM reminders WHERE done = 0 ORDER BY atMs ASC") suspend fun pendingReminders(): List<Reminder>
    @Query("SELECT * FROM reminders WHERE id = :id") suspend fun reminder(id: Long): Reminder?
    @Insert suspend fun insert(r: Reminder): Long
    @Delete suspend fun delete(r: Reminder)
    @Query("UPDATE reminders SET done = 1 WHERE id = :id") suspend fun markDone(id: Long)
    @Query("DELETE FROM reminders WHERE done = 1") suspend fun clearDone()

    @Query("SELECT * FROM messages ORDER BY atMs DESC, id DESC") fun messages(): Flow<List<Message>>
    @Insert suspend fun insert(m: Message): Long
    @Query("DELETE FROM messages WHERE id NOT IN (SELECT id FROM messages ORDER BY atMs DESC, id DESC LIMIT :keep)") suspend fun trimMessages(keep: Int = MAX_MESSAGES)
    @Query("DELETE FROM messages") suspend fun clearMessages()

    // ---- share queue ----
    @Query("SELECT * FROM share_queue ORDER BY createdAt ASC") suspend fun pendingShares(): List<ShareEntry>
    @Insert suspend fun enqueueShare(s: ShareEntry): Long
    @Query("DELETE FROM share_queue WHERE id = :id") suspend fun dequeueShare(id: Long)
    @Query("DELETE FROM share_queue") suspend fun clearShares()
    @Query("UPDATE share_queue SET retries = retries + 1 WHERE id = :id") suspend fun markShareRetry(id: Long)
    @Query("DELETE FROM share_queue WHERE retries >= :maxRetries") suspend fun drainStaleShares(maxRetries: Int = MAX_SHARE_RETRIES)
    @Query("SELECT COUNT(*) FROM share_queue") suspend fun shareQueueCount(): Int
}

@Database(entities = [Note::class, Reminder::class, Message::class, ShareEntry::class], version = 2, exportSchema = true)
abstract class PibloDb : RoomDatabase() {
    abstract fun dao(): PibloDao

    companion object {
        @Volatile private var inst: PibloDb? = null
        private val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("CREATE TABLE IF NOT EXISTS share_queue (id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, text TEXT NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL, createdAt INTEGER NOT NULL, retries INTEGER NOT NULL, idempotencyId TEXT NOT NULL)")
            }
        }

        fun get(ctx: Context): PibloDb = inst ?: synchronized(this) {
            inst ?: Room.databaseBuilder(ctx.applicationContext, PibloDb::class.java, "piblo.db")
                .addMigrations(MIGRATION_1_2)
                .build().also { inst = it }
        }
    }
}
