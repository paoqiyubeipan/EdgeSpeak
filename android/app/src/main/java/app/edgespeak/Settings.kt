package app.edgespeak

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** 用户设置。API 密钥用安卓 Keystore 里的密钥加密后存放（和电脑版的 DPAPI 一样，只有本机本应用能解开）。 */
class Settings(context: Context) {
    private val prefs = context.getSharedPreferences("edgespeak", Context.MODE_PRIVATE)

    var aiProvider: String
        get() = prefs.getString("ai_provider", "cohere")!!
        set(value) = prefs.edit().putString("ai_provider", value).apply()

    var aiAuto: Boolean // 卡片打开后自动请求 AI 解释
        get() = prefs.getBoolean("ai_auto", true)
        set(value) = prefs.edit().putBoolean("ai_auto", value).apply()

    var lookupEnglish: Boolean // 点到的不是日语时查英文单词（ECDICT 英汉词典）
        get() = prefs.getBoolean("lookup_english", true)
        set(value) = prefs.edit().putBoolean("lookup_english", value).apply()

    var bubble: Boolean // 开了悬浮取词服务时显示悬浮按钮
        get() = prefs.getBoolean("bubble", true)
        set(value) = prefs.edit().putBoolean("bubble", value).apply()

    var bubbleX: Int
        get() = prefs.getInt("bubble_x", -1)
        set(value) = prefs.edit().putInt("bubble_x", value).apply()

    var bubbleY: Int
        get() = prefs.getInt("bubble_y", -1)
        set(value) = prefs.edit().putInt("bubble_y", value).apply()

    fun model(provider: String): String = prefs.getString("model_$provider", "")!!

    fun setModel(provider: String, model: String) = prefs.edit().putString("model_$provider", model).apply()

    fun apiKey(provider: String): String = prefs.getString("key_$provider", null)?.let {
        runCatching { Secrets.decrypt(it) }.getOrDefault("")
    } ?: ""

    fun setApiKey(provider: String, key: String) =
        prefs.edit().putString("key_$provider", if (key.isEmpty()) null else Secrets.encrypt(key)).apply()

    /** 备用模型：主模型限速、额度用完或出错时按顺序换。 */
    var fallbacks: List<Pair<String, String>>
        get() = runCatching {
            val array = JSONArray(prefs.getString("ai_fallbacks", "[]"))
            List(array.length()) { array.getJSONObject(it).let { o -> o.getString("provider") to o.optString("model") } }
        }.getOrDefault(emptyList())
        set(value) = prefs.edit().putString("ai_fallbacks", JSONArray(value.map {
            JSONObject().put("provider", it.first).put("model", it.second)
        }).toString()).apply()
}

private object Secrets {
    private const val ALIAS = "edgespeak_api_keys"

    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getEntry(ALIAS, null) as? KeyStore.SecretKeyEntry)?.let { return it.secretKey }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .build())
        }.generateKey()
    }

    fun encrypt(text: String): String {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()) }
        return Base64.encodeToString(cipher.iv + cipher.doFinal(text.toByteArray()), Base64.NO_WRAP)
    }

    fun decrypt(blob: String): String {
        val bytes = Base64.decode(blob, Base64.NO_WRAP)
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, bytes, 0, 12))
        return String(cipher.doFinal(bytes, 12, bytes.size - 12))
    }
}
