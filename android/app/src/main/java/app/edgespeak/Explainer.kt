package app.edgespeak

import android.os.Handler
import android.os.Looper
import android.util.Log
import java.util.concurrent.Executors

/** 结合上下文解释日语词：把句子、目标词和词典释义发给 AI，流式返回中文解释（和电脑版 jp_ai.py 一致）。 */

private const val MAX_SENSES = 12
private const val CACHE_SIZE = 200
private const val UPDATE_INTERVAL_MS = 120L // 流式输出时多久刷新一次卡片
private const val AUTO_PER_MINUTE = 15 // 自动请求每分钟最多几次（Cohere 试用、OpenRouter 免费都是 20 次，留出手动点的余量）

private const val SYSTEM_PROMPT = """你是日语老师，帮中文母语的学习者理解句子里的一个日语词。
用户会给出：句子（目标词用【】标出）、分词器给的读音、原型、词典里的英文义项（带编号）和中文释义。
句子里可能有网络用语、俚语或新词，按它在这句话里的实际用法解释。
只按下面四行的格式用简体中文回答，不要寒暄，不要用 Markdown：
释义：这个词在这句话里的具体意思，一两句话
读音：【】里的部分在这句话里的读音，只写平假名
义项：最贴切的英文义项编号，只写数字；都不贴切就写“无”
翻译：整句的中文翻译"""

private const val EN_SYSTEM_PROMPT = """你是英语老师，学习者是中文母语者，正在用日英词典查日语词。
用户会给出：日语词、它的一条英文释义、释义里看不懂的一个英文单词。
只按下面两行的格式用简体中文回答，不要寒暄，不要用 Markdown：
意思：这个英文单词在这条释义里的意思，一两句话
整条：整条英文释义的中文翻译"""

private val FIELDS = listOf("释义", "读音", "义项", "拆解", "翻译", "意思", "整条")
private val FIRST_FIELDS = listOf("释义", "意思") // 两种回答格式各自的第一行

data class AiRequest(val system: String, val prompt: String)

private fun labelled(text: String, tags: List<String>): String {
    val labels = registerLabels(tags)
    return if (labels.isEmpty()) text else "（${labels.joinToString("、")}）$text"
}

fun jpRequest(word: WordInfo): AiRequest {
    val (sentence, k) = sentenceAround(word.context, word.spanStart, word.spanEnd)
    val marked = sentence.substring(0, k) + "【${word.surface}】" + sentence.substring(minOf(sentence.length, k + word.surface.length))
    val senses = word.senses.take(MAX_SENSES).mapIndexed { n, s -> "${n + 1}. ${labelled(s.glosses.joinToString("; "), s.misc)}" }
    val zh = word.zh.map { "- ${labelled(it.text, it.tags)}" }
    return AiRequest(SYSTEM_PROMPT,
        "句子：$marked\n目标词：${word.surface}（分词器读作 ${word.surfaceReading}）\n原型：${word.base}（${word.baseReading}）\n" +
            "英文义项：\n${senses.joinToString("\n").ifEmpty { "（英文词典里没有这个词）" }}\n" +
            "中文释义：\n${zh.joinToString("\n").ifEmpty { "（无）" }}")
}

fun enRequest(enWord: String, gloss: String, word: WordInfo) =
    AiRequest(EN_SYSTEM_PROMPT, "日语词：${word.base}（${word.baseReading}）\n英文释义：$gloss\n看不懂的英文单词：$enWord")

/** 去掉正文里的 <think>…</think>；思考还没结束（只有开头标签）时，后面的都不算正文。 */
fun stripThinking(text: String): String {
    val t = text.replace(Regex("<think>.*?</think>", RegexOption.DOT_MATCHES_ALL), "")
    val k = t.indexOf("<think>")
    return if (k < 0) t else t.substring(0, k)
}

/** 有的推理模型关掉思考后会把思考过程写进正文（里面还会打草稿），答案在最后：从最后一次出现的第一行标签开始截取。 */
fun answerPart(text: String): String {
    val start = Regex("(${FIRST_FIELDS.joinToString("|")})\\s*[：:]").findAll(text).lastOrNull()?.range?.first
    return if (start == null) text else text.substring(start)
}

private fun looksLikeAnswer(text: String) = Regex("^\\s*(${FIRST_FIELDS.joinToString("|")})\\s*[：:]").containsMatchIn(text)

/** 把“释义：……”这样的几行解析成字段；还在流式输出时也能用（只解析已经出现的部分）。 */
fun parseAnswer(text: String): Map<String, String> {
    val fields = linkedMapOf<String, String>()
    var current: String? = null
    val line = Regex("^\\s*(${FIELDS.joinToString("|")})\\s*[：:]\\s*(.*)$")
    for (l in answerPart(text).lines()) {
        val m = line.find(l)
        if (m != null) {
            current = m.groupValues[1]
            fields[current] = m.groupValues[2].trim()
        } else if (current != null && l.isNotBlank()) {
            fields[current] = fields[current] + " " + l.trim()
        }
    }
    return fields
}

fun chosenSense(fields: Map<String, String>): Int? = Regex("\\d+").find(fields["义项"] ?: "")?.value?.toIntOrNull()

/** 卡片上 AI 区的状态。state：null（还没请求）/ loading / thinking / streaming / done / error / off。 */
data class AiState(val state: String?, val text: String = "")

class Explainer(private val settings: Settings) {
    private val pool = Executors.newCachedThreadPool()
    private val main = Handler(Looper.getMainLooper())
    private val cache = object : LinkedHashMap<List<Any>, String>(16, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<List<Any>, String>?) = size > CACHE_SIZE
    }
    private val running = mutableSetOf<List<Any>>()
    private val defaultModels = mutableMapOf<String, String>() // 服务名 → 模型留空时实际用的模型
    private val cooling = mutableMapOf<Pair<String, String>, Long>() // （服务名，模型）→ 冷却到什么时候
    private val recent = ArrayDeque<Long>() // 最近一分钟里发出的请求时间

    /** 按顺序试的（服务名，模型）：先是主模型，再是备用模型。模型为空串表示该服务的默认模型。 */
    fun modelChain(): List<Pair<String, String>> {
        val chain = mutableListOf(settings.aiProvider to settings.model(settings.aiProvider))
        for (entry in settings.fallbacks) if (PROVIDERS.any { it.name == entry.first } && entry !in chain) chain += entry
        return chain
    }

    /** 主模型或任何一个备用模型的服务填了密钥。 */
    fun hasKey() = modelChain().any { settings.apiKey(it.first).isNotEmpty() }

    private fun key(request: AiRequest): List<Any> = listOf(modelChain(), request.system, request.prompt)

    @Synchronized
    fun initial(request: AiRequest): AiState = when (val k = key(request)) {
        in cache -> AiState("done", cache.getValue(k))
        in running -> AiState("loading")
        else -> AiState(null)
    }

    /** 发起请求；自动请求（manual=false）在一分钟内超过 AUTO_PER_MINUTE 次时不发，返回 false。
     *  onUpdate 在主线程里调用。 */
    fun explain(request: AiRequest, manual: Boolean, onUpdate: (AiState) -> Unit): Boolean {
        val key = key(request)
        synchronized(this) {
            if (key in running) return true
            val now = System.currentTimeMillis()
            while (recent.isNotEmpty() && now - recent.first() > 60_000) recent.removeFirst()
            if (!manual && recent.size >= AUTO_PER_MINUTE) return false
            recent.addLast(now)
            running += key
        }
        val post: (AiState) -> Unit = { state -> main.post { onUpdate(state) } }
        pool.execute { run(key, request, post) }
        return true
    }

    /** 不在冷却中的按原顺序排前面，冷却中的排后面（都在冷却时也照样试，总比直接报错好）。 */
    private fun ordered(chain: List<Pair<String, String>>): List<Pair<String, String>> {
        val now = System.currentTimeMillis()
        val ready = synchronized(this) { chain.filter { (cooling[it] ?: 0L) <= now } }
        return ready + chain.filter { it !in ready }
    }

    private fun run(key: List<Any>, request: AiRequest, post: (AiState) -> Unit) {
        @Suppress("UNCHECKED_CAST") val chain = key[0] as List<Pair<String, String>>
        val failures = mutableListOf<Triple<Provider, String, Exception?>>() // 异常为 null 表示没填密钥
        try {
            for (entry in ordered(chain)) {
                val provider = provider(entry.first)
                val apiKey = settings.apiKey(provider.name)
                if (apiKey.isEmpty()) {
                    failures += Triple(provider, entry.second, null)
                    continue
                }
                post(AiState("loading"))
                var model = entry.second
                try {
                    model = model.ifEmpty {
                        synchronized(this) { defaultModels[provider.name] } ?: (provider.defaultModel(apiKey)
                            ?: throw ApiError(404, "没有可用的默认模型")).also { synchronized(this) { defaultModels[provider.name] = it } }
                    }
                    val started = System.currentTimeMillis()
                    val (raw, finish) = collect(provider, apiKey, model, request, post)
                    Log.i(TAG, "AI 解释完成（${provider.label} $model，${System.currentTimeMillis() - started}ms，${raw.length} 字，$finish）")
                    val text = answerPart(stripThinking(raw)).trim()
                    if (text.isEmpty()) throw IllegalStateException("模型没有给出回答（${finish ?: "未知原因"}），可以在设置里换一个模型试试。")
                    synchronized(this) { cache[key] = text }
                    post(AiState("done", text))
                    return
                } catch (e: Exception) {
                    Log.w(TAG, "AI 解释失败（${provider.label} $model）：$e")
                    // 出错后多久内先不找这个模型：限速一般一分钟就恢复，余额不足、密钥无效要等用户处理
                    val seconds = when ((e as? ApiError)?.status) { 429 -> 60; 401, 402, 403, 404 -> 3600; else -> 30 }
                    synchronized(this) { cooling[entry] = System.currentTimeMillis() + seconds * 1000L }
                    failures += Triple(provider, model, e)
                }
            }
            post(AiState("error", describeFailures(failures)))
        } finally {
            synchronized(this) { running -= key }
        }
    }

    /** （正文，结束原因）。思考内容不显示，思考期间卡片显示“正在思考”。 */
    private fun collect(provider: Provider, key: String, model: String, request: AiRequest,
                        post: (AiState) -> Unit): Pair<String, String?> {
        val text = StringBuilder()
        var finish: String? = null
        var last = 0L
        var thinkingShown = false
        provider.stream(key, model, request.system, request.prompt, object : StreamSink {
            override fun text(piece: String) {
                text.append(piece)
                val visible = stripThinking(text.toString())
                if (!looksLikeAnswer(visible)) {
                    // 思考过程混在正文里：先显示“正在思考”，等完整结束后再从末尾取答案
                    if (!thinkingShown && text.trim().length > 12) {
                        thinkingShown = true
                        post(AiState("thinking"))
                    }
                } else if (System.currentTimeMillis() - last > UPDATE_INTERVAL_MS) {
                    last = System.currentTimeMillis()
                    post(AiState("streaming", visible))
                }
            }

            override fun thinking() {
                if (!thinkingShown) {
                    thinkingShown = true
                    post(AiState("thinking"))
                }
            }

            override fun finish(reason: String?) {
                finish = reason
            }
        })
        return text.toString() to finish
    }

    /** 所有模型都没成功时卡片上显示的说明：主要的错误为主，有备用模型时补一句。 */
    private fun describeFailures(failures: List<Triple<Provider, String, Exception?>>): String {
        val tried = failures.filter { it.third != null }
        if (tried.isEmpty()) return "还没有设置 ${failures.first().first.label} API 密钥：打开 EdgeSpeak 设置填写。"
        val (provider, model, e) = tried.first()
        val message = if (e is IllegalStateException) e.message!! else friendlyError(e!!, model.ifEmpty { "（默认模型）" }, provider)
        return message + if (tried.size > 1) "（备用模型也都没成功）" else ""
    }

    companion object {
        private const val TAG = "EdgeSpeak"
    }
}
