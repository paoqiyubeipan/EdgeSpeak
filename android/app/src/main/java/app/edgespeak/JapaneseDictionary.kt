package app.edgespeak

import android.database.sqlite.SQLiteDatabase
import com.worksap.nlp.sudachi.Config
import com.worksap.nlp.sudachi.DictionaryFactory
import com.worksap.nlp.sudachi.Tokenizer
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/** 日语查词：分词（Sudachi）、还原原型、查 JMdict / 维基词典释义和 Kanjium 音高。规则和电脑版 jp_dict.py 一致。
 *  不是线程安全的：只在 App.lookupThread 里用。 */

data class Sense(val pos: List<String>, val glosses: List<String>, val misc: List<String>)

data class TailPart(val surface: String, val base: String, val pos1: String, val lemma: String)

data class ZhSense(val text: String, val tags: List<String>)

data class WordInfo(
    val surface: String, // 光标处的整个词，含变形：食べなかった
    val ruby: List<Ruby>,
    val base: String, // 原型：食べる
    val baseReading: String, // 原型读音：たべる
    val accents: List<Int>, // 音调型，如 [2]
    val senses: List<Sense>, // JMdict 的；JMdict 没有这个词时用英文维基词典的
    val context: String, // 取到的上下文，给 AI 用
    val spanStart: Int, // surface 在 context 里的位置
    val spanEnd: Int,
    val pos: List<String>,
    val tail: List<TailPart>, // 原型后面的变形词尾，用来拆解
    val zh: List<ZhSense>, // 中文维基词典的释义
) {
    /** 标题这几个字在这句话里的读音（平假名）。 */
    val surfaceReading get() = toHiragana(ruby.joinToString("") { it.reading ?: it.text })
}

private class Token(
    val surface: String, val start: Int, val end: Int, val pos1: String, val pos2: String,
    val base: String, // 原型（书写形式）
    val kana: String, // 本处读音（平假名）
    val lemma: String, // 词元：读んだ 的 だ 是 た（过去）
) {
    var kanaBase: String? = null // 原型读音，用到时才算
    val isSymbol get() = pos1 in setOf("補助記号", "空白", "記号") || surface.isBlank()
}

private data class WiktRow(val reading: String, val pos: String, val senses: List<Pair<String, List<String>>>)

class JapaneseDictionary(dir: File) {
    private val tokenizer: Tokenizer
    private val db: SQLiteDatabase = open(File(dir, "jmdict.sqlite"))
    private val wikt: SQLiteDatabase = open(File(dir, "wikt.sqlite"))

    init {
        val config = Config.defaultConfig().systemDictionary(File(dir, "system_core.dic").toPath())
        tokenizer = DictionaryFactory().create(config).create()
    }

    private fun tokenize(text: String): List<Token> {
        val tokens = mutableListOf<Token>()
        for (m in tokenizer.tokenize(Tokenizer.SplitMode.A, text)) {
            val pos = m.partOfSpeech()
            val base = m.dictionaryForm().takeIf { it.isNotEmpty() && it != "*" } ?: m.surface()
            // Sudachi 不分过去的「だ」和断定的「だ」：接在动词后面的（読んだ、泳いだ）是过去
            val lemma = if (pos[0] == "助動詞" && base == "だ" && tokens.lastOrNull()?.pos1 == "動詞") "た" else base
            tokens += Token(m.surface(), m.begin(), m.end(), pos[0], pos.getOrElse(1) { "" }, base,
                toHiragana(m.readingForm()), lemma)
        }
        return tokens
    }

    private fun kanaBase(t: Token): String = t.kanaBase ?: run {
        val reading = if (t.base == t.surface) t.kana
        else tokenize(t.base).joinToString("") { it.kana }.ifEmpty { t.kana }
        t.kanaBase = reading
        reading
    }

    private fun entries(form: String): List<JSONObject> {
        val result = mutableListOf<JSONObject>()
        db.rawQuery("SELECT e.data FROM forms f JOIN entries e ON e.id = f.id WHERE f.form = ? ORDER BY e.common DESC",
            arrayOf(form)).use { c -> while (c.moveToNext()) result += JSONObject(c.getString(0)) }
        return result
    }

    private fun accents(word: String, reading: String): List<Int> {
        db.rawQuery("SELECT reading, accent FROM accents WHERE word = ?", arrayOf(word)).use { c ->
            while (c.moveToNext()) {
                if (c.getString(0) == reading) return c.getString(1).split(",").mapNotNull { it.trim().toIntOrNull() }
            }
        }
        return emptyList()
    }

    /** 维基词典里 form 的词条。给了读音时只要读音对得上的和没标读音的（都对不上就全要）。 */
    private fun wiktionary(form: String, lang: String, reading: String = ""): List<WiktRow> {
        var rows = mutableListOf<WiktRow>()
        wikt.rawQuery("SELECT reading, pos, senses FROM wikt WHERE word = ? AND lang = ?", arrayOf(form, lang)).use { c ->
            while (c.moveToNext()) {
                val senses = JSONArray(c.getString(2)).let { a ->
                    List(a.length()) { k -> a.getJSONArray(k).let { it.getString(0) to it.getJSONArray(1).strings() } }
                }
                rows += WiktRow(c.getString(0) ?: "", c.getString(1) ?: "", senses)
            }
        }
        if (reading.isNotEmpty()) {
            val same = rows.filter { toHiragana(it.reading) == toHiragana(reading) }
            if (same.isNotEmpty()) rows = (same + rows.filter { it.reading.isEmpty() }).toMutableList()
        }
        return rows
    }

    private fun known(form: String): Boolean {
        if (entries(form).isNotEmpty()) return true
        wikt.rawQuery("SELECT 1 FROM wikt WHERE word = ? LIMIT 1", arrayOf(form)).use { return it.moveToFirst() }
    }

    /** 以助词结尾的多词匹配要读音对得上：「今日は暑い」不能当成寒暄语「今日は（こんにちは）」。 */
    private fun plausible(form: String, span: List<Token>): Boolean {
        if (span.size == 1 || span.last().pos1 != "助詞") return true
        val reading = span.joinToString("") { it.kana }
        val found = entries(form)
        if (found.isNotEmpty()) return found.any { e -> e.getJSONArray("r").let { r -> (0 until r.length()).any { r.getJSONArray(it).getString(0) == reading } } }
        return (wiktionary(form, "en") + wiktionary(form, "zh")).any { toHiragana(it.reading) == reading }
    }

    /** text[offset] 处的词。不是日语词时返回 null。 */
    fun lookup(text: String, offset: Int): WordInfo? {
        val tokens = tokenize(text)
        var i = tokens.indexOfFirst { offset >= it.start && offset < it.end }
        if (i < 0 || tokens[i].isSymbol || !isJapanese(tokens[i].surface)) return null
        i = headOf(tokens, i)

        // 从光标所在的词开始找词典里最长的匹配；最后一个词按原型匹配（取り扱っ → 取り扱う）
        var best: Triple<Int, String, List<JSONObject>>? = null
        for (k in minOf(MAX_SPAN, tokens.size - i) downTo 1) {
            val span = tokens.subList(i, i + k)
            if (span.any { it.isSymbol }) continue
            val forms = mutableListOf(span.joinToString("") { it.surface })
            if (span.last().pos1 in INFLECTABLE || span.last().pos1 == "助動詞") {
                // 先按原型查：飲みたい 要查到 飲む，而不是名词 飲み
                forms.add(0, span.dropLast(1).joinToString("") { it.surface } + span.last().base)
            }
            for (form in forms.distinct()) {
                if (known(form) && plausible(form, span)) { // JMdict 或维基词典里有（网络用语常常只有维基词典收了）
                    best = Triple(k, form, entries(form))
                    break
                }
            }
            if (best != null) break
        }
        val (k, form, found) = best ?: Triple(1, tokens[i].base, emptyList())
        val span = tokens.subList(i, i + k)
        val last = span.last()

        // 把后面的变形词尾并进来：食べ + なかっ + た、連絡 + し + て + ください
        var j = i + k
        var suru = false
        var afterTe = false
        if (last.pos1 == "名詞" && j < tokens.size && tokens[j].pos1 == "動詞" && tokens[j].base == "する") {
            suru = true
            j++
        }
        if (last.pos1 in INFLECTABLE || suru) {
            while (j < tokens.size) {
                val t = tokens[j]
                afterTe = when {
                    t.pos1 == "助動詞" -> false
                    t.pos1 == "助詞" && t.pos2 == "接続助詞" && t.surface in setOf("て", "で", "ば", "ちゃ", "じゃ") -> true
                    afterTe && t.pos1 in setOf("動詞", "形容詞") && t.pos2 == "非自立可能" -> false
                    else -> break
                }
                j++
            }
        }

        val (entry, picked) = pickEntry(found, form, span)
        var reading = picked
        val wiktEn = wiktionary(form, "en", reading)
        if (entry == null && wiktEn.isNotEmpty() && wiktEn[0].reading.isNotEmpty() &&
            (reading.isEmpty() || reading == form || span.size > 1)) {
            reading = toHiragana(wiktEn[0].reading) // 分词器不认识的词，读音以维基词典为准
        }
        val base = form + if (suru) "する" else ""
        val baseReading = reading + if (suru) "する" else ""
        val surface = text.substring(tokens[i].start, tokens[j - 1].end)
        val ruby = if (surface == form && entry != null) rubySegments(surface, reading)
        else tokens.subList(i, j).flatMap { rubySegments(it.surface, it.kana) }

        var senses = if (entry != null) senses(entry, form, reading) else emptyList()
        senses = if (senses.isNotEmpty()) senses + extraSlangSenses(senses, wiktEn)
        else wiktEn.flatMap { row -> row.senses.map { (gloss, tags) -> Sense(listOf(row.pos), listOf(gloss), tags) } }

        val zh = mutableListOf<ZhSense>()
        for (row in wiktionary(form, "zh", reading)) {
            for ((gloss, tags) in row.senses) if (zh.none { it.text == gloss }) zh += ZhSense(gloss, tags)
        }
        // 太多时只留前几条，但网络用语 / 俚语义项总要留着
        val zhShown = zh.filterIndexed { n, z -> n < MAX_ZH || z.tags.any { it in SLANG_TAGS } }

        return WordInfo(surface, ruby, base, baseReading, accents(form, reading), senses, text,
            tokens[i].start, tokens[j - 1].end, senses.firstOrNull()?.pos ?: emptyList(),
            tokens.subList(i + k, j).map { TailPart(it.surface, it.base, it.pos1, it.lemma) }, zhShown)
    }

    /** 指在词尾（なかっ、た、て、する……）上时，退回到它所属的词。 */
    private fun headOf(tokens: List<Token>, start: Int): Int {
        var i = start
        while (i > 0) {
            val t = tokens[i]
            val prev = tokens[i - 1]
            val te = setOf("て", "で", "ば")
            val tail = t.pos1 == "助動詞" ||
                (t.pos1 == "助詞" && t.pos2 == "接続助詞" && t.surface in te) ||
                (t.pos1 in setOf("動詞", "形容詞") && t.pos2 == "非自立可能" &&
                    (prev.surface in te || (t.base == "する" && prev.pos1 == "名詞")))
            if (!tail || prev.isSymbol || (prev.pos1 == "助詞" && prev.surface !in te)) break
            i--
        }
        return i
    }

    /** 同形词（生物：せいぶつ / なまもの 是两个词条）里挑一个：优先挑分词器读音所在的词条；
     *  词条内部用词典排在前面的常用读音（日本語 读 にほんご 而不是 にっぽんご）。 */
    private fun pickEntry(entries: List<JSONObject>, form: String, span: List<Token>): Pair<JSONObject?, String> {
        val tokenReading = span.joinToString("") { if (it === span.last()) kanaBase(it) else it.kana }
        fun readings(e: JSONObject): List<String> {
            val r = e.getJSONArray("r")
            val kana = (0 until r.length()).map { r.getJSONArray(it) }
            if (kana.any { it.getString(0) == form }) return listOf(form) // 查到的本身就是假名写法
            return kana.filter { it.isNull(1) || it.getJSONArray(1).strings().contains(form) }.map { it.getString(0) }
        }
        val candidates = entries.map { it to readings(it) }.filter { it.second.isNotEmpty() }
        candidates.firstOrNull { tokenReading in it.second }?.let { return it.first to it.second[0] }
        candidates.firstOrNull()?.let { return it.first to it.second[0] }
        return null to tokenReading.ifEmpty { form }
    }

    private fun senses(entry: JSONObject, form: String, reading: String): List<Sense> {
        val kanji = entry.getJSONArray("k").strings()
        val s = entry.getJSONArray("s")
        return (0 until s.length()).mapNotNull { n ->
            val sense = s.getJSONArray(n)
            val onlyKanji = if (sense.isNull(3)) null else sense.getJSONArray(3).strings()
            val onlyKana = if (sense.isNull(4)) null else sense.getJSONArray(4).strings()
            when {
                onlyKanji != null && form in kanji && form !in onlyKanji -> null
                onlyKana != null && reading !in onlyKana -> null
                else -> Sense(sense.getJSONArray(0).strings(), sense.getJSONArray(1).strings(), sense.getJSONArray(2).strings())
            }
        }
    }

    /** JMdict 有这个词时，从维基词典补上它还没收的网络用语 / 俚语义项（意思重复的不补）。 */
    private fun extraSlangSenses(senses: List<Sense>, wiktRows: List<WiktRow>): List<Sense> {
        val tags = senses.flatMap { it.misc }.toSet()
        if ("net-sl" in tags) return emptyList()
        val wanted = if ("sl" in tags) NET_TAGS else SLANG_TAGS
        val known = glossPhrases(senses.flatMap { it.glosses }).toMutableSet()
        val extra = mutableListOf<Sense>()
        for (row in wiktRows) for ((gloss, glossTags) in row.senses) {
            val phrases = glossPhrases(listOf(gloss))
            if (glossTags.any { it in wanted } && phrases.none { it in known }) {
                extra += Sense(listOf(row.pos), listOf(gloss), glossTags)
                known += phrases
            }
        }
        return extra
    }

    companion object {
        private const val MAX_SPAN = 6 // 最长匹配最多跨几个词
        private const val MAX_ZH = 5
        private val INFLECTABLE = setOf("動詞", "形容詞", "形状詞")

        private fun open(file: File) = SQLiteDatabase.openDatabase(file.path, null, SQLiteDatabase.OPEN_READONLY)

        /** 释义里的每个短语，去掉开头的 to 和括号注释，用来判断两本词典是不是说的同一个意思。 */
        private fun glossPhrases(glosses: List<String>): Set<String> = glosses.flatMap { gloss ->
            gloss.replace(Regex("\\([^)]*\\)"), "").split(Regex("[;,]")).map {
                it.trim().lowercase().replace(Regex("^(to|a|an|the)\\s+"), "").trim()
            }
        }.filter { it.isNotEmpty() }.toSet()
    }
}

fun JSONArray.strings(): List<String> = List(length()) { getString(it) }
