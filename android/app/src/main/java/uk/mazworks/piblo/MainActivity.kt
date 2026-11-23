package uk.mazworks.piblo

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.foundation.layout.fillMaxSize
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
import uk.mazworks.piblo.ui.CraftScreen
import uk.mazworks.piblo.ui.FindDevicesScreen
import uk.mazworks.piblo.ui.HomeScreen
import uk.mazworks.piblo.ui.LiveViewScreen
import uk.mazworks.piblo.ui.MemoryScreen
import uk.mazworks.piblo.ui.OnboardingScreen
import uk.mazworks.piblo.ui.SettingsScreen

private val tabs = listOf("Home" to "⌂", "Ask" to "?", "Actions" to "✓", "Memory" to "✉", "Settings" to "⚙")

// Match the Windows palette in app/src/styles/tokens.css.
private val Light = lightColorScheme(
    primary = Color(0xFFB3301A), onPrimary = Color(0xFFFFF4E0),
    secondary = Color(0xFF0D6E4F), secondaryContainer = Color(0xFFF7D7C0), onSecondaryContainer = Color(0xFF2A1A12),
    background = Color(0xFFFBFAF7), onBackground = Color(0xFF2A1A12),
    surface = Color(0xFFFFFAF0), onSurface = Color(0xFF2A1A12), onSurfaceVariant = Color(0xFF5B4336),
    surfaceVariant = Color(0xFFFBE3BD), outline = Color(0xFF8C6D5A), outlineVariant = Color(0xFFEAD3AC),
)
private val Dark = darkColorScheme(
    primary = Color(0xFFEE5A36), onPrimary = Color(0xFF2A1A12),
    secondary = Color(0xFF8FDCB8), secondaryContainer = Color(0xFF54402D), onSecondaryContainer = Color(0xFFFFF4E0),
    background = Color(0xFF2A1A12), onBackground = Color(0xFFFFF4E0),
    surface = Color(0xFF352318), onSurface = Color(0xFFFFF4E0), onSurfaceVariant = Color(0xFFC8B8A8),
    surfaceVariant = Color(0xFF402D20), outline = Color(0xFFAB9078), outlineVariant = Color(0xFF5A4536),
)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            val app = application as PibloApp
            val prefs by app.settings.prefs.collectAsStateWithLifecycle(initialValue = Prefs())
            MaterialTheme(colorScheme = if (isSystemInDarkTheme()) Dark else Light) {
                if (!prefs.onboarded) {
                    // First launch: find the computer and pair. No demo, no fake data.
                    Surface(Modifier.fillMaxSize()) { OnboardingScreen(onDone = { app.scope.launch { app.settings.setOnboarded(true) } }) }
                    return@MaterialTheme
                }
                var tab by rememberSaveable { mutableIntStateOf(0) }
                var live by rememberSaveable { mutableStateOf(false) }
                var craft by rememberSaveable { mutableStateOf(false) }
                var find by rememberSaveable { mutableStateOf(false) }
                if (live) {
                    Surface(Modifier.fillMaxSize()) { LiveViewScreen(onClose = { live = false }) }
                    return@MaterialTheme
                }
                if (find) {
                    BackHandler { find = false }
                    Surface(Modifier.fillMaxSize()) {
                        FindDevicesScreen(onClose = { find = false }, onConnect = { find = false; tab = 4 })
                    }
                    return@MaterialTheme
                }
                if (craft) {
                    BackHandler { craft = false }
                    Surface(Modifier.fillMaxSize()) {
                        CraftScreen(onClose = { craft = false }, onConnect = { craft = false; tab = 4 })
                    }
                    return@MaterialTheme
                }
                Scaffold(
                    bottomBar = {
                        NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                            tabs.forEachIndexed { i, (label, glyph) ->
                                NavigationBarItem(
                                    selected = tab == i, onClick = { tab = i },
                                    icon = { Text(glyph) }, label = { Text(label, maxLines = 1) },
                                    colors = NavigationBarItemDefaults.colors(
                                        selectedTextColor = MaterialTheme.colorScheme.onBackground,
                                        unselectedTextColor = MaterialTheme.colorScheme.onSurfaceVariant,
                                    ),
                                    modifier = Modifier.testTag("tab_$label"),
                                )
                            }
                        }
                    },
                ) { pad ->
                    Surface(Modifier.fillMaxSize().padding(pad), color = MaterialTheme.colorScheme.background) {
                        when (tab) {
                            0 -> HomeScreen(openLive = { live = true }, onConnect = { tab = 4 }, openCraft = { craft = true }, openFind = { find = true })
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
