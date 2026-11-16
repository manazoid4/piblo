package uk.mazworks.piblo

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.launch
import uk.mazworks.piblo.data.Prefs
import uk.mazworks.piblo.ui.ActionsScreen
import uk.mazworks.piblo.ui.AskScreen
import uk.mazworks.piblo.ui.HomeScreen
import uk.mazworks.piblo.ui.LiveViewScreen
import uk.mazworks.piblo.ui.MemoryScreen
import uk.mazworks.piblo.ui.OnboardingScreen
import uk.mazworks.piblo.ui.SettingsScreen

private val tabs = listOf("Home" to "⌂", "Ask" to "?", "Actions" to "✓", "Memory" to "✉", "Settings" to "⚙")

private val Light = lightColorScheme(primary = Color(0xFF0B1B3A), onPrimary = Color(0xFFFFF6E9), secondary = Color(0xFFB88700), background = Color(0xFFFFF6E9), surface = Color(0xFFFFFBF5))
private val Dark = darkColorScheme(primary = Color(0xFFFFD23F), onPrimary = Color(0xFF0B1B3A), secondary = Color(0xFF4CE0B3), background = Color(0xFF0B1B3A), surface = Color(0xFF13264D))

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            val app = application as PibloApp
            val prefs by app.settings.prefs.collectAsStateWithLifecycle(initialValue = Prefs())
            MaterialTheme(colorScheme = if (isSystemInDarkTheme()) Dark else Light) {
                if (!prefs.onboarded) {
                    // First launch: find the computer and pair. No demo, no fake data.
                    OnboardingScreen(onDone = { app.scope.launch { app.settings.setOnboarded(true) } })
                    return@MaterialTheme
                }
                var tab by rememberSaveable { mutableIntStateOf(0) }
                var live by rememberSaveable { mutableStateOf(false) }
                if (live) {
                    LiveViewScreen(onClose = { live = false })
                    return@MaterialTheme
                }
                Scaffold(
                    bottomBar = {
                        NavigationBar {
                            tabs.forEachIndexed { i, (label, glyph) ->
                                NavigationBarItem(
                                    selected = tab == i, onClick = { tab = i },
                                    icon = { Text(glyph) }, label = { Text(label, maxLines = 1) },
                                    modifier = Modifier.testTag("tab_$label"),
                                )
                            }
                        }
                    },
                ) { pad ->
                    Surface(Modifier.padding(pad)) {
                        when (tab) {
                            0 -> HomeScreen(openLive = { live = true }, onConnect = { tab = 4 })
                            1 -> AskScreen()
                            2 -> ActionsScreen(openLive = { live = true })
                            3 -> MemoryScreen()
                            else -> SettingsScreen(openLive = { live = true })
                        }
                    }
                }
            }
        }
    }
}
