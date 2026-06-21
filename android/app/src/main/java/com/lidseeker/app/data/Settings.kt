package com.lidseeker.app.data

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.withContext

private val Context.dataStore by preferencesDataStore(name = "lidseeker_settings")

/**
 * Persists the backend base URL (plain DataStore) and the auth token.
 *
 * The token is a bearer credential, so it lives in [EncryptedSharedPreferences]
 * (AndroidKeyStore-backed) rather than plaintext DataStore — it can't be read off
 * disk or out of a device cloud backup. The non-secret server URL stays in DataStore.
 */
class Settings(private val context: Context) {
    private val keyServerUrl = stringPreferencesKey("server_url")

    val serverUrl: Flow<String> = context.dataStore.data.map { it[keyServerUrl] ?: "" }

    private val securePrefs by lazy {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context,
            "lidseeker_secure",
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    // Read once off the main thread; writes also update Repository's in-memory copy.
    val token: Flow<String> =
        flow { emit(securePrefs.getString(KEY_TOKEN, "").orEmpty()) }.flowOn(Dispatchers.IO)

    suspend fun setServerUrl(url: String) {
        context.dataStore.edit { it[keyServerUrl] = url.trim().trimEnd('/') }
    }

    suspend fun setToken(token: String) = withContext(Dispatchers.IO) {
        securePrefs.edit().putString(KEY_TOKEN, token).apply()
    }

    suspend fun clearToken() = withContext(Dispatchers.IO) {
        securePrefs.edit().remove(KEY_TOKEN).apply()
    }

    private companion object {
        const val KEY_TOKEN = "token"
    }
}
