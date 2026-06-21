package com.lidseeker.app.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Logout
import androidx.compose.material.icons.filled.Explore
import androidx.compose.material.icons.filled.LibraryMusic
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import kotlinx.coroutines.launch
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.lidseeker.app.repository
import com.lidseeker.app.ui.detail.ArtistDetailScreen
import com.lidseeker.app.ui.discover.DiscoverScreen
import com.lidseeker.app.ui.login.LoginScreen
import com.lidseeker.app.ui.requests.RequestsScreen
import com.lidseeker.app.ui.search.SearchScreen
import com.lidseeker.app.ui.settings.SettingsScreen

object Routes {
    const val LOGIN = "login"
    const val DISCOVER = "discover"
    const val SEARCH = "search"
    const val REQUESTS = "requests"
    const val SETTINGS = "settings"
    const val ARTIST = "artist/{foreignId}?name={name}"
    fun artist(foreignId: String, name: String) =
        "artist/$foreignId?name=${java.net.URLEncoder.encode(name, "UTF-8")}"
}

@Composable
fun AppRoot() {
    val context = LocalContext.current
    val repo = context.repository
    var ready by remember { mutableStateOf(false) }
    var start by remember { mutableStateOf(Routes.LOGIN) }

    LaunchedEffect(Unit) {
        repo.bootstrap()
        start = if (repo.hasServer() && repo.isLoggedIn()) Routes.DISCOVER else Routes.LOGIN
        ready = true
    }

    if (!ready) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            CircularProgressIndicator()
        }
        return
    }

    val nav = rememberNavController()
    val scope = rememberCoroutineScope()
    val onLogout: () -> Unit = {
        scope.launch {
            repo.logout()
            nav.navigate(Routes.LOGIN) { popUpTo(0) { inclusive = true } }
        }
    }

    // Server rejected our token (expired/revoked) anywhere in the app → log out.
    LaunchedEffect(Unit) {
        repo.authExpired.collect {
            repo.logout()
            if (nav.currentDestination?.route != Routes.LOGIN) {
                nav.navigate(Routes.LOGIN) { popUpTo(0) { inclusive = true } }
            }
        }
    }

    NavHost(navController = nav, startDestination = start) {
        composable(Routes.LOGIN) {
            LoginScreen(onLoggedIn = {
                nav.navigate(Routes.DISCOVER) {
                    popUpTo(Routes.LOGIN) { inclusive = true }
                }
            })
        }
        composable(Routes.DISCOVER) {
            HomeScaffold(nav, Routes.DISCOVER, "Discover", onLogout) { inner ->
                DiscoverScreen(modifier = Modifier.padding(inner))
            }
        }
        composable(Routes.SEARCH) {
            HomeScaffold(nav, Routes.SEARCH, "Search", onLogout) { inner ->
                SearchScreen(
                    modifier = Modifier.padding(inner),
                    onArtistClick = { r -> nav.navigate(Routes.artist(r.foreignId, r.title)) },
                )
            }
        }
        composable(Routes.REQUESTS) {
            HomeScaffold(nav, Routes.REQUESTS, "My Requests", onLogout) { inner ->
                RequestsScreen(modifier = Modifier.padding(inner))
            }
        }
        composable(Routes.SETTINGS) {
            SettingsScreen(onBack = { nav.popBackStack() })
        }
        composable(Routes.ARTIST) { entry ->
            ArtistDetailScreen(
                foreignId = entry.arguments?.getString("foreignId").orEmpty(),
                artistName = entry.arguments?.getString("name").orEmpty(),
                onBack = { nav.popBackStack() },
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun HomeScaffold(
    nav: NavHostController,
    current: String,
    title: String,
    onLogout: () -> Unit,
    content: @Composable (androidx.compose.foundation.layout.PaddingValues) -> Unit,
) {
    val navItemColors = NavigationBarItemDefaults.colors(
        selectedIconColor = MaterialTheme.colorScheme.onPrimary,
        selectedTextColor = MaterialTheme.colorScheme.primary,
        indicatorColor = MaterialTheme.colorScheme.primary,
        unselectedIconColor = MaterialTheme.colorScheme.onSurfaceVariant,
        unselectedTextColor = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = { Text(title, fontWeight = FontWeight.Bold) },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surfaceContainer,
                    titleContentColor = MaterialTheme.colorScheme.onSurface,
                    actionIconContentColor = MaterialTheme.colorScheme.onSurfaceVariant,
                ),
                actions = {
                    IconButton(onClick = { nav.navigate(Routes.SETTINGS) }) {
                        Icon(Icons.Filled.Settings, contentDescription = "Settings")
                    }
                    IconButton(onClick = onLogout) {
                        Icon(
                            Icons.AutoMirrored.Filled.Logout,
                            contentDescription = "Log out",
                        )
                    }
                },
            )
        },
        bottomBar = {
            NavigationBar(containerColor = MaterialTheme.colorScheme.surfaceContainer) {
                NavigationBarItem(
                    selected = current == Routes.DISCOVER,
                    onClick = { navigateTab(nav, Routes.DISCOVER) },
                    icon = { Icon(Icons.Filled.Explore, contentDescription = null) },
                    label = { Text("Discover") },
                    colors = navItemColors,
                )
                NavigationBarItem(
                    selected = current == Routes.SEARCH,
                    onClick = { navigateTab(nav, Routes.SEARCH) },
                    icon = { Icon(Icons.Filled.Search, contentDescription = null) },
                    label = { Text("Search") },
                    colors = navItemColors,
                )
                NavigationBarItem(
                    selected = current == Routes.REQUESTS,
                    onClick = { navigateTab(nav, Routes.REQUESTS) },
                    icon = { Icon(Icons.Filled.LibraryMusic, contentDescription = null) },
                    label = { Text("Requests") },
                    colors = navItemColors,
                )
            }
        },
    ) { inner -> content(inner) }
}

private fun navigateTab(nav: NavHostController, route: String) {
    nav.navigate(route) {
        popUpTo(nav.graph.findStartDestination().id) { saveState = true }
        launchSingleTop = true
        restoreState = true
    }
}
