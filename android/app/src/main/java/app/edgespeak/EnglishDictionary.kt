package app.edgespeak

import android.database.sqlite.SQLiteDatabase
import java.io.File

data class EnglishWord(
    val word: String,
    val phonetic: String, // 已转成国际音标写法，可能为空
    val lines: List<String>, // 中文释义，每个词性一行，如 "n. 薪水"
    val tags: List<String>, // 考试标签：四级、六级……
    val forms: List<Pair<String, String>>, // （类型，词形），如（过去式，ate）
    val via: String = "", // 从变形跳到原词时的说明，如 "eating 是 eat 的现在分词"
)

/** 点到的英文单词（选字菜单、悬浮取词里点到的不是日语时）。context / spanStart / spanEnd 和 WordInfo 的同名字段一样：
 *  给 AI 当上下文，也用来在句子里高亮。 */
data class EnglishHit(
    val surface: String, // 原文里的写法：Running、don't、well-known
    val entry: EnglishWord?, // 词典里没有时为 null
    val context: String,
    val spanStart: Int, // surface 在 context 里的位置
    val spanEnd: Int,
) : LookupResult

/** ECDICT 英汉词典（离线：日语释义里会出现的词，加上有考试标签、变形或音标的普通英文单词）。 */
class EnglishDictionary(dir: File) {
    private val db = SQLiteDatabase.openDatabase(File(dir, "ecdict.sqlite").path, null, SQLiteDatabase.OPEN_READONLY)

    private data class Row(val word: String, val phonetic: String, val translation: String, val tag: String,
                           val exchange: Map<String, String>)

    private fun row(key: String): Row? =
        db.rawQuery("SELECT word, phonetic, translation, tag, exchange FROM en WHERE key = ?", arrayOf(key)).use { c ->
            if (!c.moveToFirst()) null
            else Row(c.getString(0), c.getString(1) ?: "", c.getString(2) ?: "", c.getString(3) ?: "",
                parseExchange(c.getString(4) ?: ""))
        }

    private fun lemmaOf(key: String): String? =
        db.rawQuery("SELECT lemma FROM en_forms WHERE form = ?", arrayOf(key)).use { c -> if (c.moveToFirst()) c.getString(0) else null }

    fun lookup(word: String): EnglishWord? {
        val key = word.lowercase()
        var found = row(key)
        var via = ""
        val lemma = found?.exchange?.get("0") ?: if (found == null) lemmaOf(key) else null
        if (lemma != null && lemma != key) {
            row(lemma)?.let { lemmaRow ->
                // 变形词跳到原词，并说明是哪种变形
                val kinds = lemmaRow.exchange.filter { (k, v) -> v.lowercase() == key && k in FORMS }.keys.map { FORMS.getValue(it) }
                via = "$word 是 ${lemmaRow.word} 的${kinds.joinToString("、").ifEmpty { "变形" }}"
                found = lemmaRow
            }
        }
        val r = found ?: return null
        return EnglishWord(r.word, ipa(r.phonetic), r.translation.lines().map { it.trim() }.filter { it.isNotEmpty() },
            r.tag.split(" ").mapNotNull { TAGS[it] }, FORMS.mapNotNull { (k, label) -> r.exchange[k]?.let { label to it } }, via)
    }

    /** text[offset] 处的英文单词 → EnglishHit。不在英文单词上时返回 null。
     *  先查整体（well-known、don't），查不到再查光标所在的那一段（state-of-the-art 的 art）和去掉 's 的形式。 */
    fun lookupAt(text: String, offset: Int): EnglishHit? {
        val plain = halfwidth(text)
        val (s, e) = wordAt(plain, offset) ?: return null
        val run = plain.substring(s, e)
        val candidates = mutableListOf(s to e)
        if ('-' in run) {
            val partStart = maxOf(s, plain.lastIndexOf('-', offset - 1) + 1)
            val partEnd = plain.indexOf('-', offset).takeIf { it in 0 until e } ?: e
            candidates += partStart to partEnd
        }
        if (run.takeLast(2).lowercase() in setOf("'s", "’s")) candidates += s to e - 2 // John's、today's
        for ((cs, ce) in candidates.distinct()) {
            val entry = lookup(plain.substring(cs, ce).replace('’', '\''))
            if (entry != null) return EnglishHit(text.substring(cs, ce), entry, text, cs, ce)
        }
        // 词典里没有：普通单词照样显示卡片（专有名词、新词可以让 AI 解释）；驼峰式的多半是代码，不显示
        if (run.length < 2 || Regex("[a-z][A-Z]").containsMatchIn(run)) return null
        return EnglishHit(text.substring(s, e), null, text, s, e)
    }

    companion object {
        private val TAGS = mapOf("zk" to "中考", "gk" to "高考", "cet4" to "四级", "cet6" to "六级", "ky" to "考研",
            "toefl" to "托福", "ielts" to "雅思", "gre" to "GRE")
        private val FORMS = linkedMapOf("p" to "过去式", "d" to "过去分词", "i" to "现在分词", "3" to "三单",
            "s" to "复数", "r" to "比较级", "t" to "最高级")

        private fun parseExchange(text: String) = text.split("/").mapNotNull { part ->
            part.split(":", limit = 2).takeIf { it.size == 2 && it[1].isNotEmpty() }?.let { it[0] to it[1] }
        }.toMap()

        /** ECDICT 的音标用了几个替代字符，换成标准的国际音标。 */
        private fun ipa(p: String) = p.replace("ә", "ə").replace(":", "ː").replace("'", "ˈ").replace("ɒː", "ɔː")
    }
}

// ---------------------------------------------------------------- 英文取词（规则和电脑版 jp_dict.py 一致）

private const val EN_RUN_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_'’-"
private const val EN_JOINERS = "./\\@:#=" // 夹在两个字母数字之间时，说明是网址、路径、邮箱、代码的一部分

private fun isAsciiLetter(c: Char) = c in 'a'..'z' || c in 'A'..'Z'

/** 全角英数（ＡＢＣ）转成半角。逐字替换，长度不变，位置都对得上。 */
fun halfwidth(text: String): String = buildString(text.length) {
    for (c in text) append(if (c in '！'..'～') c - 0xFEE0 else c)
}

/** text[offset] 处英文单词的（起点，终点）。不在字母上，或是代码标识符、网址、路径里的一段时返回 null。 */
fun englishWordAt(text: String, offset: Int): Pair<Int, Int>? = wordAt(halfwidth(text), offset)

/** 同 englishWordAt，plain 已经转成半角。 */
private fun wordAt(plain: String, offset: Int): Pair<Int, Int>? {
    if (offset !in plain.indices || !isAsciiLetter(plain[offset])) return null
    var s = offset
    var e = offset + 1
    while (s > 0 && plain[s - 1] in EN_RUN_CHARS) s--
    while (e < plain.length && plain[e] in EN_RUN_CHARS) e++
    // 两头的撇号、连字符不算（'quoted'、破折号 --），光标在字母上所以不会越过 offset
    while (plain[s] in "'’-") s++
    while (plain[e - 1] in "'’-") e--
    if ((s until e).any { plain[it] in '0'..'9' || plain[it] == '_' }) return null // mp3、v2、my_var
    if (s >= 2 && plain[s - 1] in EN_JOINERS && plain[s - 2].isLetterOrDigit()) return null // example.com、C:\Users、user@mail
    if (e + 1 < plain.length && plain[e] in EN_JOINERS && plain[e + 1].isLetterOrDigit()) return null
    return s to e
}

/** text 里第一个能查的英文单词的起点，没有时返回 -1。 */
fun firstEnglishWord(text: String): Int {
    val plain = halfwidth(text)
    var k = 0
    while (k < plain.length) {
        if (!isAsciiLetter(plain[k])) {
            k++
            continue
        }
        wordAt(plain, k)?.let { return it.first }
        while (k < plain.length && plain[k] in EN_RUN_CHARS) k++ // 这一段不能查（mp3、网址的一段），整段跳过
    }
    return -1
}

// 英文的句号后面要跟空白才算句末（3.5、e.g. 里的点不算）；中日文的句号、换行照旧
private val EN_SENTENCE_END = Regex("""[.!?]+["'”’)\]]*(?=\s|$)|[。！？\n]""")

/** 和 sentenceAround 一样，按英文的断句规则。 */
fun englishSentenceAround(text: String, start: Int, end: Int): Pair<String, Int> {
    var s = 0
    for (m in EN_SENTENCE_END.findAll(text.substring(0, start))) s = m.range.last + 1
    val e = EN_SENTENCE_END.find(text, end)?.let { it.range.last + 1 } ?: text.length
    return clipSentence(text, s, e, start, end)
}
