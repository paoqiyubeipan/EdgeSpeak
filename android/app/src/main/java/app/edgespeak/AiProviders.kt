package app.edgespeak

import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/** AI 服务：Cohere、Mistral、OpenRouter（和电脑版 ai_providers.py 一致）。
 *  查词用不上推理，请求时都先关掉思考；模型不允许关时自动改用允许思考的请求。 */

class ApiError(val status: Int, override val message: String) : Exception("$status: $message")

interface StreamSink {
    fun text(piece: String)
    fun thinking()
    fun finish(reason: String?)
}

private const val MAX_TOKENS = 4000 // 只防模型失控：回答长短靠提示词里的固定格式控制
private const val TEMPERATURE = 0.3

private fun open(url: String, method: String, headers: Map<String, String>, body: JSONObject? = null): HttpURLConnection {
    val conn = URL(url).openConnection() as HttpURLConnection
    conn.requestMethod = method
    conn.connectTimeout = 15_000
    conn.readTimeout = 60_000
    headers.forEach { (k, v) -> conn.setRequestProperty(k, v) }
    if (body != null) {
        conn.doOutput = true
        conn.setRequestProperty("Content-Type", "application/json")
        conn.outputStream.use { it.write(body.toString().toByteArray()) }
    }
    if (conn.responseCode >= 400) {
        val text = conn.errorStream?.bufferedReader()?.readText() ?: ""
        conn.disconnect()
        throw ApiError(conn.responseCode, errorMessage(text).ifEmpty { conn.responseMessage ?: "" })
    }
    return conn
}

/** 从错误响应里取出说明：{"error": {"message": ...}}、{"message": ...} 或 {"detail": ...}。 */
private fun errorMessage(text: String): String = runCatching {
    val body = JSONObject(text)
    val error = body.opt("error")
    when {
        error is JSONObject -> error.optString("message").ifEmpty { error.toString() }
        error is String -> error
        body.has("message") -> body.getString("message")
        body.has("detail") -> body.get("detail").toString()
        else -> text
    }
}.getOrDefault(text).take(300)

private fun getJson(url: String, headers: Map<String, String>) =
    open(url, "GET", headers).let { conn -> conn.inputStream.bufferedReader().use { JSONObject(it.readText()) } }

/** SSE：逐个返回 data: 后面的内容，到 [DONE] 为止。 */
private fun sse(conn: HttpURLConnection, onData: (String) -> Unit) {
    conn.inputStream.bufferedReader().useLines { lines ->
        for (line in lines) {
            if (!line.startsWith("data:")) continue // 空行、event: 行和 “: OPENROUTER PROCESSING” 这类保活注释
            val data = line.substring(5).trim()
            if (data == "[DONE]") break
            onData(data)
        }
    }
}

abstract class Provider(
    val name: String, val label: String, val keyUrl: String, val hint: String, val defaultHint: String,
) {
    abstract fun listModels(key: String): List<String>
    abstract fun defaultModel(key: String): String?
    protected abstract fun streamOnce(key: String, model: String, messages: JSONArray, disableReasoning: Boolean, sink: StreamSink)

    fun stream(key: String, model: String, system: String, prompt: String, sink: StreamSink) {
        val messages = JSONArray()
            .put(JSONObject().put("role", "system").put("content", system))
            .put(JSONObject().put("role", "user").put("content", prompt))
        try {
            streamOnce(key, model, messages, true, sink)
        } catch (e: ApiError) {
            // 请求因为“关闭思考”这个参数被拒（模型必须思考，或不支持这个参数）
            val text = e.message.lowercase()
            if (e.status !in setOf(400, 422) || ("think" !in text && "reason" !in text)) throw e
            streamOnce(key, model, messages, false, sink)
        }
    }

    /** （是否成功，说明）。用一次很短的请求验证密钥和模型。 */
    fun test(key: String, model: String): Pair<Boolean, String> {
        val started = System.nanoTime()
        return try {
            stream(key, model, "只回答「はい」。", "「はい」とだけ答えて", object : StreamSink {
                override fun text(piece: String) {}
                override fun thinking() {}
                override fun finish(reason: String?) {}
            })
            true to "连接成功，用时 %.1f 秒。".format((System.nanoTime() - started) / 1e9)
        } catch (e: Exception) {
            false to friendlyError(e, model, this)
        }
    }
}

object Cohere : Provider("cohere", "Cohere", "https://dashboard.cohere.com/api-keys",
    "试用密钥免费：每月 1,000 次、每分钟 20 次，不能商用。", "留空则用 Cohere 的默认聊天模型。") {
    private const val BASE = "https://api.cohere.com"
    private fun auth(key: String) = mapOf("Authorization" to "Bearer $key", "Accept" to "application/json")

    override fun listModels(key: String): List<String> {
        val models = getJson("$BASE/v1/models?endpoint=chat&page_size=1000", auth(key)).optJSONArray("models") ?: JSONArray()
        return List(models.length()) { models.getJSONObject(it) }
            .filter { !it.optBoolean("is_deprecated") }.map { it.getString("name") }.sorted()
    }

    override fun defaultModel(key: String): String? {
        val models = getJson("$BASE/v1/models?endpoint=chat&default_only=true", auth(key)).optJSONArray("models")
        return models?.optJSONObject(0)?.optString("name")?.ifEmpty { null }
    }

    override fun streamOnce(key: String, model: String, messages: JSONArray, disableReasoning: Boolean, sink: StreamSink) {
        val body = JSONObject().put("model", model).put("messages", messages).put("stream", true)
            .put("temperature", TEMPERATURE).put("max_tokens", MAX_TOKENS)
        if (disableReasoning) body.put("thinking", JSONObject().put("type", "disabled"))
        val conn = open("$BASE/v2/chat", "POST", auth(key) + ("Accept" to "text/event-stream"), body)
        sse(conn) { data ->
            val event = JSONObject(data)
            when (event.optString("type")) {
                "content-delta" -> {
                    val content = event.optJSONObject("delta")?.optJSONObject("message")?.optJSONObject("content")
                    val text = content?.optString("text").orEmpty()
                    if (text.isNotEmpty()) sink.text(text) else if (content?.has("thinking") == true) sink.thinking()
                }
                "message-end" -> sink.finish(event.optJSONObject("delta")?.optString("finish_reason"))
            }
        }
    }
}

/** OpenAI 兼容接口（Mistral、OpenRouter）。 */
abstract class OpenAICompatible(
    name: String, label: String, keyUrl: String, hint: String, defaultHint: String,
    private val baseUrl: String, private val extraHeaders: Map<String, String> = emptyMap(),
) : Provider(name, label, keyUrl, hint, defaultHint) {
    protected fun headers(key: String?) = extraHeaders + (if (key.isNullOrEmpty()) emptyMap() else mapOf("Authorization" to "Bearer $key"))
    protected fun get(path: String, key: String?) = getJson(baseUrl + path, headers(key))
    protected abstract fun reasoning(disable: Boolean): Map<String, Any>

    override fun streamOnce(key: String, model: String, messages: JSONArray, disableReasoning: Boolean, sink: StreamSink) {
        val body = JSONObject().put("model", model).put("messages", messages).put("temperature", TEMPERATURE)
            .put("max_tokens", MAX_TOKENS).put("stream", true)
        reasoning(disableReasoning).forEach { (k, v) -> body.put(k, v) }
        val conn = open("$baseUrl/chat/completions", "POST", headers(key), body)
        sse(conn) { data ->
            val chunk = JSONObject(data)
            chunk.optJSONObject("error")?.let { throw ApiError(it.optInt("code", 500), it.optString("message", it.toString())) }
            val choice = chunk.optJSONArray("choices")?.optJSONObject(0) ?: return@sse
            val delta = choice.optJSONObject("delta") ?: JSONObject()
            when (val content = delta.opt("content")) {
                is String -> if (content.isNotEmpty()) sink.text(content)
                is JSONArray -> for (k in 0 until content.length()) { // Mistral 的思考模型：[{type: thinking}, {type: text}]
                    val part = content.optJSONObject(k) ?: continue
                    when (part.optString("type")) {
                        "text" -> part.optString("text").takeIf { it.isNotEmpty() }?.let(sink::text)
                        "thinking" -> sink.thinking()
                    }
                }
            }
            if (delta.optString("reasoning").isNotEmpty() || delta.has("reasoning_details")) sink.thinking()
            if (!choice.isNull("finish_reason")) sink.finish(choice.optString("finish_reason"))
        }
    }
}

object Mistral : OpenAICompatible("mistral", "Mistral", "https://console.mistral.ai/api-keys",
    "有免费的 Experiment 计划（需要验证手机号，有速率限制）。", "留空则用 mistral-small-latest。",
    "https://api.mistral.ai/v1") {
    override fun reasoning(disable: Boolean): Map<String, Any> = if (disable) mapOf("reasoning_effort" to "none") else emptyMap()

    override fun listModels(key: String): List<String> {
        val data = get("/models", key).optJSONArray("data") ?: JSONArray()
        return List(data.length()) { data.getJSONObject(it) }
            .filter { it.optJSONObject("capabilities")?.optBoolean("completion_chat") == true && it.isNull("deprecation") }
            .map { it.getString("id") }.distinct().sorted()
    }

    override fun defaultModel(key: String) = "mistral-small-latest"
}

object OpenRouter : OpenAICompatible("openrouter", "OpenRouter", "https://openrouter.ai/keys",
    "只列免费模型。openrouter/free 会自动挑一个当前可用的免费模型。免费模型按账户限次：每分钟 20 次，" +
        "每天 50 次（累计充值满 10 美元后每天 1000 次）。",
    "留空则用 openrouter/free。", "https://openrouter.ai/api/v1", mapOf("X-Title" to "EdgeSpeak")) {
    private const val FREE_ROUTER = "openrouter/free" // 免费模型路由；openrouter/auto 会路由到收费模型，不列

    // 必须思考的模型不接受 effort: none，退一步：照常思考但不返回思考内容
    override fun reasoning(disable: Boolean): Map<String, Any> =
        mapOf("reasoning" to JSONObject(if (disable) mapOf("effort" to "none") else mapOf("exclude" to true)))

    /** 免费的文字模型（模型列表不需要密钥），免费路由排第一。 */
    override fun listModels(key: String): List<String> {
        val data = get("/models", null).optJSONArray("data") ?: JSONArray()
        val free = List(data.length()) { data.getJSONObject(it) }.filter { m ->
            val id = m.getString("id")
            val outputs = m.optJSONObject("architecture")?.optJSONArray("output_modalities")?.strings() ?: listOf("text")
            // 只认带 :free 的：标价为 0 的预览模型里有不能聊天的；安全分类模型也不能用来解释
            id.endsWith(":free") && "text" in outputs && "safety" !in id && "guard" !in id
        }.map { it.getString("id") }
        return listOf(FREE_ROUTER) + free.sorted()
    }

    override fun defaultModel(key: String) = FREE_ROUTER
}

val PROVIDERS: List<Provider> = listOf(Cohere, Mistral, OpenRouter)

fun provider(name: String): Provider = PROVIDERS.firstOrNull { it.name == name } ?: Cohere

fun friendlyError(e: Exception, model: String, provider: Provider): String {
    val label = provider.label
    return when (val status = (e as? ApiError)?.status) {
        401 -> "$label API 密钥无效，请在 EdgeSpeak 设置里检查。"
        402 -> "$label 账户余额不足：换一个名字带 :free 的免费模型，或充值后再试。"
        429 -> if (provider === Cohere) "请求太频繁，或本月免费额度（1,000 次）已用完。"
        else "请求太频繁，或 $label 的免费额度用完了，稍后再试。"
        404 -> "找不到模型 $model，请在设置里换一个。"
        400, 422 -> "请求被拒绝：${e.message}"
        null -> if (e is IOException) "连不上 $label：${e.javaClass.simpleName}。请检查网络。" else "出错了：${e.message}"
        else -> "$label 服务出错（$status），请稍后再试。"
    }
}
