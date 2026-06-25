package com.lidseeker.app.data

import kotlinx.serialization.json.Json
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Response
import retrofit2.Retrofit
import retrofit2.converter.kotlinx.serialization.asConverterFactory
import kotlin.time.Duration.Companion.seconds

/** Snapshot of the mutable connection config, read fresh on every request. */
data class ConnConfig(val baseUrl: String, val token: String)

object ApiClient {
    private val json = Json { ignoreUnknownKeys = true }

    fun create(
        configProvider: () -> ConnConfig,
        onUnauthorized: () -> Unit = {},
    ): ApiService {
        val client = OkHttpClient.Builder()
            .addInterceptor(DynamicHostInterceptor(configProvider))
            .addInterceptor(UnauthorizedInterceptor(onUnauthorized))
            .connectTimeout(15.seconds)
            .readTimeout(40.seconds)
            .build()

        return Retrofit.Builder()
            // Placeholder; DynamicHostInterceptor rewrites the host per request.
            .baseUrl("http://localhost/")
            .client(client)
            .addConverterFactory(json.asConverterFactory("application/json".toMediaType()))
            .build()
            .create(ApiService::class.java)
    }
}

private class DynamicHostInterceptor(
    private val configProvider: () -> ConnConfig,
) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val cfg = configProvider()
        val base = cfg.baseUrl.toHttpUrlOrNull()
        var request = chain.request()
        if (base != null) {
            val newUrl = request.url.newBuilder()
                .scheme(base.scheme)
                .host(base.host)
                .port(base.port)
                .build()
            val builder = request.newBuilder().url(newUrl)
            if (cfg.token.isNotEmpty()) {
                builder.header("Authorization", "Bearer ${cfg.token}")
            }
            request = builder.build()
        }
        return chain.proceed(request)
    }
}

/**
 * Centralised auth-expiry handling: any 401 (except on the login call itself,
 * where a 401 is just bad credentials) fires [onUnauthorized] so the app can drop
 * the token and bounce to Login, instead of every screen showing a stuck error.
 */
private class UnauthorizedInterceptor(
    private val onUnauthorized: () -> Unit,
) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val response = chain.proceed(chain.request())
        if (response.code == 401 && !chain.request().url.encodedPath.endsWith("/auth/login")) {
            onUnauthorized()
        }
        return response
    }
}
