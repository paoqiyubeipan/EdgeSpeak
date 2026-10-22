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

/** ECDICT 英汉词典（离线，只留日语释义里会出现的词）。 */
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
