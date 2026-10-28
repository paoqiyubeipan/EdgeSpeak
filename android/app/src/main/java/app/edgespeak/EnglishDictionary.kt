package app.edgespeak

import android.content.res.AssetManager
import android.database.sqlite.SQLiteDatabase
import android.util.Log
import org.json.JSONObject
import java.io.File
import kotlin.math.abs

data class EnglishWord(
    val word: String,
    val phonetic: String, // 已转成国际音标写法，可能为空
    val lines: List<String>, // 中文释义，每个词性一行，如 "n. 薪水"
    val tags: List<String>, // 考试标签：四级、六级……
    val forms: List<Pair<String, String>>, // （类型，词形），如（过去式，ate）
    val via: String = "", // 从变形跳到原词时的说明，如 "eating 是 eat 的现在分词"
    val ukNotes: List<String> = emptyList(), // 英美差异：["美式拼写 color"]、["英式说 lift"]
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

/** ECDICT 英汉词典（离线：日语释义里会出现的词，加上有考试标签、变形或音标的普通英文单词）。以英式英语为准，见 UkEnglish。 */
class EnglishDictionary(dir: File, assets: AssetManager) {
    private val db = SQLiteDatabase.openDatabase(File(dir, "ecdict.sqlite").path, null, SQLiteDatabase.OPEN_READONLY)
    private val uk = UkEnglish.load(assets)

    private data class Row(val word: String, val phonetic: String, val translation: String, val tag: String,
                           val exchange: String)

    private fun row(key: String): Row? =
        db.rawQuery("SELECT word, phonetic, translation, tag, exchange FROM en WHERE key = ?", arrayOf(key)).use { c ->
            if (!c.moveToFirst()) null
            else Row(c.getString(0) ?: "", c.getString(1) ?: "", c.getString(2) ?: "", c.getString(3) ?: "", c.getString(4) ?: "")
        }

    private fun lemmaOf(key: String): String? =
        db.rawQuery("SELECT lemma FROM en_forms WHERE form = ?", arrayOf(key)).use { c -> if (c.moveToFirst()) c.getString(0) else null }

    /** 和电脑版 jp_dict.py 的 EnglishDictionary.lookup 一样（同样的正则、同样的步骤）。 */
    fun lookup(word: String): EnglishWord? {
        val key = word.lowercase()
        var entry = row(key)
        var (lemmaEntry, kinds) = lemmaRow(key, entry)
        val otherSpelling = uk.spelling[key].nz() ?: uk.american[key].nz()
        if (kinds.isEmpty() && otherSpelling != null) {
            // 另一种拼写那边找得到原词时用它的：labeled → labelled → label、colored → coloured → colour
            val (altLemma, altKinds) = lemmaRow(otherSpelling, row(otherSpelling))
            if (altKinds.isNotEmpty() && altLemma != null && (classes(altLemma) - "").isNotEmpty()) {
                lemmaEntry = altLemma
                kinds = altKinds
            }
        }
        if (lemmaEntry != null && kinds.isEmpty() && entry != null && ownSenses(entry).isEmpty()) {
            kinds = listOf("变形") // 自己只写着“get的过去分词”：gotten → get
        }
        // 变形词跳到原词，并说明是哪种变形。原词要列着这个变形才跳（ECDICT 有些假原词：colored → colore）；
        // 只是“复数”而自己另有意思的不跳：they 不是 it 的复数；pediatrics（儿科学）不是形容词 pediatric 的复数
        val lemmaClasses = lemmaEntry?.let { classes(it) } ?: emptySet()
        if ("复数" in kinds && "n" !in lemmaClasses &&
            ((lemmaClasses - setOf("", "num", "interj")).isNotEmpty() || !trustedPlural(entry, lemmaEntry!!))) {
            val lemma3 = parseExchange(lemmaEntry!!.exchange)["3"].orEmpty().lowercase()
            val formRow = entry
            val ownNoun = formRow != null && ownSenses(formRow).any { posClass(it) == "n" }
            if ("v" in lemmaClasses && (lemma3 == key || lemma3 == otherSpelling || lemma3.isEmpty() && !ownNoun)) {
                // 原词只是动词：ECDICT 把三单记成了“复数”（economises → economise）
                kinds = kinds.map { if (it == "复数") "三单" else it }.distinct()
            } else if (kinds == listOf("复数")) {
                // 原词没有名词意思，不是它的复数：they 不是 it 的，pediatrics 不是形容词 pediatric 的，
                // headquarters、tweezers 不是动词 headquarter、tweezer 的三单
                lemmaEntry = null
            } else {
                kinds = kinds.filter { it != "复数" }
            }
        }
        var jump = lemmaEntry != null && (kinds.isNotEmpty() || entry == null)
        // 英式名词是美式原词的“复数”时不跳：maths 不是 math 的复数，而是英式说法
        if (jump && uk.vocab[lemmaEntry!!.word.lowercase()].orEmpty().endsWith("说 $key")) {
            jump = false
            lemmaEntry = null
        }
        var own = emptyList<String>()
        if (jump) {
            // 本身也是个词的把它自己的意思也带上，按句子判断是哪个：词性和原词不同的（tired 疲倦的、left 左边的），
            // 它自己就有考试标签、是个常用词的（pants 裤子、found 建立），或者是复数另有意思的常用词（glasses 眼镜）。
            // 照抄原词释义的后面再去掉
            val lemma = lemmaEntry!!
            val lemmaWords = bigrams(senseLines(lemma).joinToString(""))
            val formRow = entry
            own = if (formRow == null) emptyList() else ownSenses(formRow, POS).filter { l ->
                posClass(l) !in lemmaClasses || pyStrip(formRow.tag).isNotEmpty() ||
                    key in OWN_PLURALS && posClass(l) == "n" && bigrams(l).none { it in lemmaWords }
            }
            entry = lemma
        }
        if (entry == null) return null
        // 以英式为准：标题用英式拼写。英美两种拼写的词条哪个释义好用哪个（ECDICT 里有的英式词条只写“<主英>=…”，
        // 有的美式词条反过来只写“<美>=…”或“[网络]…”）；词形变化优先用英式词条自己的（neighboured，而不是 neighbored）
        var title = entry.word
        val ukNotes = mutableListOf<String>()
        val cardRows = mutableListOf(entry)
        // 不是英美成对的词：变形只改双写字母（traveled → travelled），别的不动（checkered 不能变成 chequered）
        var parts = LinkedHashMap<String, String>()
        for ((k, v) in parseExchange(entry.exchange)) {
            val b = uk.british(v)
            parts[k] = if (undouble(b.lowercase()) == undouble(v.lowercase())) b else v
        }
        // 只有动词英美写法不同的（practice → practise）：动词变形用英式的，三单照现在分词的差异改
        if ("作动词" in uk.partial[entry.word.lowercase()].orEmpty()) {
            val ex = parseExchange(entry.exchange)
            for (k in listOf("p", "d", "i")) ex[k].nz()?.let { parts[k] = uk.british(it) }
            val ing = ex["i"].nz()
            val third = ex["3"].nz()
            if (ing != null && third != null && parts["i"] != ing) parts["3"] = respell(third, ing, parts["i"]!!) ?: third
        }
        val lower = entry.word.lowercase()
        // 名词复数跳到原词时不按光标处的词推：analyses 和动词的 analyzes 拼写不同，和 analysis 无关
        val (us, gb) = pair(lower, if (kinds == listOf("复数")) lower else key)
        if (!us.isNullOrEmpty() && gb != null) {
            val usRow = row(us)
            val ukRow = row(gb)
            cardRows += listOfNotNull(usRow, ukRow)
            title = if (entry.word.take(1).any { it.isUpperCase() }) pyCapitalize(gb) else gb
            val ukParts = if (ukRow != null) parseExchange(ukRow.exchange) else emptyMap()
            val other = parseExchange((usRow ?: entry).exchange)
            parts = LinkedHashMap()
            for (k in FORMS.keys) {
                // 比较级、最高级英美一样（phonier），不改
                val form = ukParts[k].nz() ?: other[k].nz()?.let { if (k in "rt") it else toUk(it, us, gb) }.nz()
                if (form != null) parts[k] = form
            }
            val best = listOfNotNull(ukRow, usRow).maxWithOrNull(RICHNESS) ?: entry // 一样好时用英式的（取第一个）
            // 另一个词条有、选中的词条没有的词性补上（sulphur 补上美式词条的 vt. 用硫磺处理）
            val alt = if (best === ukRow) usRow else ukRow
            val have = senseLines(best).map { posClass(it) }.toSet()
            // 没标词性的、只标了领域的（[化] …）意思不一样的也补上
            val extra = (if (alt != null) senseLines(alt) else emptyList()).filter { l ->
                l !in best.translation && if (ANY_POS.containsMatchIn(pyStrip(l))) posClass(l) !in have
                else "[网络]" !in l && !mostlyIn(l, best.translation)
            }
            // 考试标签两个词条的合起来（centre 的在美式词条 center 上）
            val tags = listOfNotNull(ukRow, usRow).flatMap { it.tag.split(PY_SPACES) }.filter { it.isNotEmpty() }.distinct()
            entry = best.copy(translation = (listOf(best.translation) + extra).joinToString("\n"), tag = tags.joinToString(" "))
            ukNotes += "美式拼写 ${usRow?.word ?: us}"
        } else if (key in uk.spelling) {
            ukNotes += "$word 是美式拼写，英式写作 ${uk.british(word)}" // labeled → label
        }
        var via = ""
        if (jump) {
            via = "$word 是 $title 的${kinds.joinToString("、").ifEmpty { "变形" }}"
            // 照抄原词释义的不算（和卡片上可能用到的每个词条都比）
            val cardText = cardRows.joinToString("\n") { it.translation }
            own = own.filter { !mostlyIn(it, cardText) }
            own.firstOrNull()?.let { m ->
                val short = if (m.codePointCount(0, m.length) <= 24) m else m.substring(0, m.offsetByCodePoints(0, 23)) + "…"
                via += "；本身也是 $short"
            }
        }
        // 说明以光标处的词为准（tyres、lifts），它没有时再用原词的。动词变形（fell、tired、stored）只留讲动词的说明，
        // “指秋天时说 autumn”“指轮胎时写 tyre”这种名词的说明对它不成立
        var lemmaNotes = uk.notes(lower)
        if (jump && "复数" !in kinds) lemmaNotes = lemmaNotes.filter { "动词" in it }
        val notes = (ukNotes + uk.notes(key).ifEmpty { lemmaNotes }).distinct()
        val translation = entry.translation
        val lines = splitLines(translation)
        val plural = parts["s"].orEmpty()
        if (plural.lowercase() == title.lowercase()) {
            parts.remove("s") // tweezers 的“复数”是它自己
        } else if (plural.lowercase() == title.lowercase() + "s" && plural.lowercase() != key &&
            (title.endsWith("s") || 's' in parseExchange(entry.exchange)["1"].orEmpty())) {
            parts.remove("s") // ECDICT 给本来就是复数的词又加了个复数（faecess、childrens）
        }
        if (!parts["s"].isNullOrEmpty() && lines.none { NOUN.containsMatchIn(pyStrip(it)) }) {
            // 没有名词意思的词也列了个“复数”（americanises）：动词的话其实是三单，别的不要
            val verbPlural = parts.remove("s")!!
            // 只有动词原词才挪成三单；approaching 这种分词词条的“复数” approachings 不要
            if ("0" !in parseExchange(entry.exchange) && lines.any { VERB.containsMatchIn(pyStrip(it)) } && "3" !in parts) {
                parts["3"] = verbPlural
            }
        }
        val ing = parts["i"].orEmpty()
        val titleLower = title.lowercase()
        if (ing.lowercase().endsWith("eing") && titleLower.endsWith("e") && titleLower.length > 2 &&
            titleLower[titleLower.length - 2] !in "aeiouyg") {
            parts["i"] = ing.dropLast(4) + "ing" // ECDICT 的 axeing → axing（being、ageing、singeing、cueing 不动）
        }
        val forms = FORMS.mapNotNull { (k, label) -> parts[k].nz()?.let { label to it } }
        return EnglishWord(title, ipa(entry.phonetic), lines.map { pyStrip(it) }.filter { it.isNotEmpty() },
            entry.tag.split(PY_SPACES).mapNotNull { TAGS[it] }, forms, via, notes)
    }

    /** 词条释义的词性大类。英式词条只写“<主英>=economize”时看另一种拼写的。 */
    private fun classes(r: Row): Set<String> {
        var classes = senseLines(r).map { posClass(it) }.toSet()
        val lw = r.word.lowercase()
        val other = uk.american[lw].nz() ?: uk.spelling[lw].nz()
        if (classes.isEmpty() && other != null) {
            row(other)?.let { o -> classes = senseLines(o).map { posClass(it) }.toSet() }
        }
        if ((classes - "").isEmpty()) {
            // 释义都没标词性时，“vt. [主英国英语] =criminalize”这种指向别的词的行也能说明词性
            val rows = listOfNotNull(r, other?.let { row(it) })
            classes = classes + rows.flatMap { splitLines(it.translation) }
                .filter { ANY_POS.containsMatchIn(pyStrip(it)) }.map { posClass(it) }
        }
        return classes
    }

    /** 原词的释义都没标词性（laptop 只写着“[计] 膝上型电脑”）时，不知道它是不是名词：
     *  复数词条自己没有别的意思、没说是别的词的复数（aches 写着“ache的名词复数”，不是 ACH 的），
     *  原词也不只是人名、网络释义（cance、crossbone）时才跳。 */
    private fun trustedPlural(formRow: Row?, lemma: Row): Boolean {
        if (formRow == null) return true
        // corps 军队、belongings 财产、faeces 粪便：自己有意思的不跳。没标词性的行去掉括号里的说明再看，
        // 和原词说的不是一回事才算（discographies 唱片目录 / discography 椎间盘造影术、orthodontics 正牙学 / 牙正常的）
        val lemmaWords = bigrams(senseLines(lemma).joinToString(""))
        val plain = senseLines(formRow).filter { !ANY_POS.containsMatchIn(pyStrip(it)) && "[网络]" !in it }
            .map { dropNames(PAREN_GROUP.replace(it, "")) }
        if (ownSenses(formRow).isNotEmpty() || plain.any { CJK.containsMatchIn(it) && bigrams(it).none { b -> b in lemmaWords } }) {
            return false
        }
        val named = NAMED_PLURAL.findAll(formRow.translation).map { it.groupValues[1].lowercase() }.toSet()
        if ((named - lemma.word.lowercase()).isNotEmpty()) return false
        return senseLines(lemma).any { "[网络]" !in it && dropNames(it).isNotEmpty() }
    }

    /** 原词的（美式拼写，英式拼写）；不是英美成对的词时为（null，null）。 */
    private fun pair(lower: String, key: String): Pair<String?, String?> {
        uk.american[lower]?.let { return it to lower }
        uk.spelling[lower]?.let { return lower to it }
        if (lower in uk.partial) return null to null // practice、license：只有部分义项不同，由说明交代
        // 原词不在对照表里、光标处的词在（disorganised → disorganize）：照它的拼写差异推出原词的另一种写法，词典里有才算
        val keyUs: String
        val keyUk: String
        if (key in uk.spelling) {
            keyUs = key
            keyUk = uk.spelling.getValue(key)
        } else if (key in uk.american) {
            keyUs = uk.american.getValue(key)
            keyUk = key
        } else {
            return null to null
        }
        if (undouble(keyUs) == undouble(keyUk)) {
            return null to null // 只差双写字母的（labeled / labelled）：原词 label 英美一样
        }
        val simple = keyUs.zip(keyUk).count { (a, b) -> a != b } <= 1 && abs(keyUs.length - keyUk.length) <= 1
        val lemmaText = row(lower)?.let { senseLines(it).joinToString("") }.orEmpty()
        for ((src, dst, isUs) in listOf(Triple(keyUs, keyUk, true), Triple(keyUk, keyUs, false))) {
            val other = respell(lower, src, dst)
            if (other != null && other in uk.partial) continue // practise 不和 practice 配对：名词 practice 英式也这么写
            val otherRow = if (!other.isNullOrEmpty() && other != lower) row(other) else null
            if (other == null || otherRow == null) continue
            // 推出来的词要和原词说的是一回事（aging/ageing 推出的 agee 是“半开地”，不是 age）；
            // 只差一个字母的（z → s、补 u）词条残缺也行，别的要是个有词性的正经词条
            val otherBigrams = bigrams(senseLines(otherRow).joinToString(""))
            val same = bigrams(lemmaText).any { it in otherBigrams } || lower in otherRow.translation.lowercase()
            if (same && (simple || senseLines(otherRow).any { ANY_POS.containsMatchIn(pyStrip(it)) })) {
                return if (isUs) lower to other else other to lower
            }
        }
        return null to null
    }

    /** 美式词条列的变形转成英式：查对照表，没有的照原词的拼写差异改（aggrandized → aggrandised）。
     *  改不了的不要（宁可少一个变形，也不在英式卡片上写美式拼写）。 */
    private fun toUk(form: String, us: String, gb: String): String? {
        val british = uk.british(form)
        if (british != form) return british
        val gbLower = gb.lowercase()
        val stem = if (gbLower.endsWith("e")) gbLower.dropLast(1) else gbLower // organise → organis（organising）
        if (form.lowercase().startsWith(stem) || us == gb) return form
        val rest = if (form.length > us.length) form.substring(us.length) else ""
        if (us.lowercase().endsWith("er") && gbLower.endsWith("re") && form.lowercase().startsWith(us.lowercase()) &&
            rest.take(1).lowercase() in setOf("e", "i")) {
            return gb.dropLast(1) + rest // sabered → sabred、sabering → sabring
        }
        var converted = respell(form.lowercase(), us.lowercase(), gbLower)
        if (!converted.isNullOrEmpty() && form.take(1).any { it.isUpperCase() }) {
            converted = converted.substring(0, 1).uppercase() + converted.substring(1)
        }
        // 改出来的要以英式原词开头（aggrandised）或者词典里有（sabred）；phoney 改不出 phoneier 这种
        return if (!converted.isNullOrEmpty() && (converted.lowercase().startsWith(stem) || row(converted.lowercase()) != null)) {
            converted
        } else {
            null
        }
    }

    /** key 的原词词条和 key 是它的哪几种变形：（词条，["过去式", ...]）。不是变形词时为（null，[]）。 */
    private fun lemmaRow(key: String, entry: Row?): Pair<Row?, List<String>> {
        val exchange = if (entry != null) parseExchange(entry.exchange) else emptyMap()
        val lemma = exchange["0"].nz() ?: (if (entry == null) lemmaOf(key).nz() else null) ?: return null to emptyList()
        val lemmaEntry = (if (lemma != key) row(lemma) else null) ?: return null to emptyList()
        val spellings = setOfNotNull(key, uk.spelling[key], uk.american[key])
        var kinds = parseExchange(lemmaEntry.exchange).filter { (k, v) -> v.lowercase() in spellings && k in FORMS }
            .keys.map { FORMS.getValue(it) }
        val paired = lemma.lowercase() in uk.spelling || lemma.lowercase() in uk.american
        // 原词是个正经词条、这个词也确实是它加词尾变来的（trialled ← trial）
        val regular = lemma.codePointCount(0, lemma.length) > 2 && key.startsWith(lemma.lowercase().dropLast(1)) &&
            senseLines(lemmaEntry).any { ANY_POS.containsMatchIn(pyStrip(it)) }
        if (kinds.isEmpty() && (paired || regular)) {
            // ECDICT 的原词常常没列变形（privatise、trial），按这个词自己标的变形类型（1:dp）
            kinds = exchange["1"].orEmpty().mapNotNull { FORMS[it.toString()] }
        }
        return lemmaEntry to kinds
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

        // 下面的正则和电脑版一样。Python 的 \s、\w 默认按 Unicode 算，Java 的只认 ASCII，所以写成显式的字符类
        private const val PY_SPACE = "\\t-\\r\\u001C-\\u0020\\u0085\\u00A0\\u1680\\u2000-\\u200A\\u2028\\u2029\\u202F\\u205F\\u3000"
        private val PY_SPACES = Regex("[$PY_SPACE]+")
        private val PY_LINE_BREAK = Regex("\\r\\n|[\\n\\r\\u000B\\u000C\\u001C-\\u001E\\u0085\\u2028\\u2029]")
        // 词性标记后面跟空白，或者连着另一个标记（vi.vt.、vt.,vi.）
        private const val POS_END = "\\.(?:[$PY_SPACE]|,|(?=[a-z]+\\.))"
        private val POS = Regex("^(a|adj|adv|n|v|vt|vi|prep|conj|pron|int)$POS_END") // 不含 pl.：复数的意思和原词一样
        // 释义里说明“是某词的变形”的写法：( interest的名词复数 )、（service复数形式）、（open过去分词形式）、（第三人称单数）、
        // go的ing形式、单三形式……
        private val FORM_OF = Regex("名词复数|动词复数|复数形式|复数[）)]|的复数|第三人称|三单|的单数|过去式|过去时|过去分词|现在分词|" +
            "比较级|最高级|的[\\p{L}\\p{N}_]*形式|ing形式|单三形式|过去形式|动名词|的变形")
        // “colour 的变形”“（变形）”这种只说明是别的词的变体的行；“变形金刚”“变形的”不算
        private val VARIANT = Regex("的变形|变形[$PY_SPACE]*[)）]|变形形式")
        // 复数词条里说明是哪个词的复数：ache的名词复数、( burger的复数形式 )
        private val NAMED_PLURAL = Regex("([A-Za-z]+)[$PY_SPACE]*的[^)）\\n]{0,4}复数")
        private val PAREN_GROUP = Regex("[(（][^()（）]*[)）]")
        private val POINTER = Regex("=|等于|同“|同\"|<主英>|<英>|<美>|〈美〉|〈英〉|英国英语|美国英语")
        private val ANY_POS = Regex("^(a|adj|adv|n|v|vt|vi|prep|conj|pron|int|interj|num|art|aux|pl)$POS_END")
        private val NOUN = Regex("^(n|pl)$POS_END")
        private val VERB = Regex("^(v|vt|vi)$POS_END")
        // 专名注释：比尔斯（姓氏）、因祸得福（电影名）、火箭（篮球队名）……ECDICT 把它们和普通释义写在一起
        private val NAME = Regex("[（(][^）)]*(人名|姓氏|姓|男子名|女子名|地名|电影名|歌曲名|杂志名|书名|游戏名|公司名|队名|电视剧名|乐队名|品牌|" +
            "郡名|州|城市|海峡|旧约|新约|圣经|剧集|剧名|连续剧|片名)[^）)]*[）)]")
        // 复数另有常用意思的词（glasses 眼镜、spirits 情绪）：ECDICT 里没有考试标签，靠这张表认
        private val OWN_PLURALS = setOf("glasses", "spectacles", "colours", "colors", "spirits", "manners", "customs",
            "damages", "minutes", "works", "papers", "forces", "quarters", "grounds", "troops", "savings", "surroundings",
            "belongings", "earnings", "outskirts", "remains", "contents", "odds", "premises", "proceeds", "letters", "arms",
            "goods", "looks", "sands", "waters", "heavens", "times", "pains", "regards", "terms", "relations")
        private val CJK = Regex("[\\u4E00-\\u9FFF]")
        private val SENSE_SEP = Regex("[,;，；]")
        private val PART_SEP = Regex("[;；]")
        private val PAREN = Regex("[（(]")
        private val DOUBLED = Regex("(.)\\1")
        private val POS_CLASSES = mapOf("n" to "n", "pl" to "n", "v" to "v", "vt" to "v", "vi" to "v", "a" to "a",
            "adj" to "a", "adv" to "a")

        private fun isCjk(c: Char) = c in '一'..'鿿'

        /** 同 Python 的 re.sub(r"(.)\1", r"\1", s)：双写字母只留一个。 */
        private fun undouble(s: String) = DOUBLED.replace(s, "$1")

        /** 相邻两个汉字组成的词：比较两段释义说的是不是一回事（委员会；街道办事处 / 委员会）。 */
        private fun bigrams(text: String): Set<String> {
            val out = HashSet<String>()
            for (i in 0 until text.length - 1) {
                if (isCjk(text[i]) && isCjk(text[i + 1])) out += text.substring(i, i + 2)
            }
            return out
        }

        /** 去掉一行里的专名部分（比尔斯（姓氏）；奥尼恩斯（英国语言学家））。整行都是专名时返回空串。 */
        private fun dropNames(line: String): String {
            val m = ANY_POS.find(line)
            val head = if (m != null) line.substring(0, m.range.last + 1) else ""
            val body = if (m != null) line.substring(m.range.last + 1) else line
            val parts = PART_SEP.split(body).map { pyStrip(it) }.filter { it.isNotEmpty() }
            val isName = { p: String -> NAME.containsMatchIn(p) || "人名" in p }
            val names = parts.filter(isName).map { PAREN.split(it)[0] }.toSet()
            val kept = parts.filter { !(isName(it) || PAREN.split(it)[0] in names) }
            return if (kept.isNotEmpty()) head + kept.joinToString("；") else ""
        }

        /** 有中文释义的行：只写“<主英>=…”、变形说明、只有词性或音标的行不算。 */
        private fun senseLines(r: Row): List<String> = splitLines(r.translation)
            .filter { CJK.containsMatchIn(it) && !POINTER.containsMatchIn(it) && !VARIANT.containsMatchIn(it) }

        private fun countSenses(lines: List<String>): Int = lines.sumOf { SENSE_SEP.split(it).size }

        /** 比较英美两个拼写的词条用：先看有没有带词性的释义，再看不算“[网络]”的义项数，最后看总义项数。 */
        private val RICHNESS: Comparator<Row> = compareBy<Row>(
            { r -> senseLines(r).any { ANY_POS.containsMatchIn(pyStrip(it)) } },
            { r -> countSenses(senseLines(r).filter { "[网络]" !in it }) },
            { r -> countSenses(senseLines(r)) })

        /** 变形词自己的意思（不是“某词的过去式”这种说明，去掉姓氏、电影名之类的专名）。
         *  只有最后一段是变形说明的保留前面几段：n. 眼镜；双筒望远镜；玻璃（glass的复数形式）→ n. 眼镜；双筒望远镜 */
        private fun ownSenses(r: Row, pos: Regex = ANY_POS): List<String> {
            val lines = mutableListOf<String>()
            for (raw in senseLines(r)) {
                var l = pyStrip(raw)
                if (!pos.containsMatchIn(l)) continue
                val parts = PART_SEP.split(l)
                if (FORM_OF.containsMatchIn(parts.last()) && parts.size > 1 &&
                    !FORM_OF.containsMatchIn(parts.dropLast(1).joinToString(""))) {
                    l = parts.dropLast(1).joinToString("；") { pyStrip(it) }
                }
                if (!FORM_OF.containsMatchIn(l)) lines += dropNames(l)
            }
            return lines.filter { it.isNotEmpty() }
        }

        /** 释义行的词性大类：n / v / a / 其他。复数 pl. 算名词，vt. vi. 算动词，adj. adv. 算形容词类。 */
        private fun posClass(line: String): String {
            val tag = ANY_POS.find(pyStrip(line))?.groupValues?.get(1).orEmpty()
            return POS_CLASSES[tag] ?: tag
        }

        /** line 的中文字六成以上也出现在 text 里：变形词照抄了原词的释义（孩子们 / 孩子）。 */
        private fun mostlyIn(line: String, text: String): Boolean {
            val chars = CJK.findAll(line).map { it.value }.toSet()
            val inText = CJK.findAll(text).map { it.value }.toSet()
            return chars.isNotEmpty() && chars.count { it in inText } >= 0.6 * chars.size
        }

        /** 按 src → dst 的拼写差异改 word：disorganized → disorganised 的差异用在 disorganize 上得 disorganise。
         *  差异超过两个字母、或 word 里没有对应的地方时返回 null。 */
        private fun respell(word: String, src: String, dst: String): String? {
            var i = 0
            while (i < minOf(src.length, dst.length) && src[i] == dst[i]) i++
            var a = src.substring(i)
            var b = dst.substring(i)
            while (a.isNotEmpty() && b.isNotEmpty() && a.last() == b.last()) {
                a = a.dropLast(1)
                b = b.dropLast(1)
            }
            if (a.length > 2 || b.length > 2 || !word.startsWith(src.substring(0, i)) || !word.substring(i).startsWith(a)) return null
            return word.substring(0, i) + b + word.substring(i + a.length)
        }

        /** ECDICT 的词形变化字段 "p:ate/d:eaten/0:eat/1:p" → {类型: 词形}。 */
        private fun parseExchange(text: String): Map<String, String> =
            text.split("/").filter { ':' in it }.associate { it.substringBefore(':') to it.substringAfter(':') }

        /** 同 Python 的 str.splitlines()。 */
        private fun splitLines(text: String): List<String> =
            text.split(PY_LINE_BREAK).let { if (it.last().isEmpty()) it.dropLast(1) else it }

        private fun isPySpace(c: Char) = c.code in 0x09..0x0D || c.code in 0x1C..0x20 || c.code == 0x85 || c.code == 0xA0 ||
            c.code == 0x1680 || c.code in 0x2000..0x200A || c.code in 0x2028..0x2029 || c.code == 0x202F || c.code == 0x205F ||
            c.code == 0x3000

        /** 同 Python 的 str.strip()。 */
        private fun pyStrip(s: String) = s.trim(::isPySpace)

        /** ECDICT 的音标用了几个替代字符，换成标准的国际音标。 */
        private fun ipa(p: String) = p.replace("ә", "ə").replace(":", "ː").replace("'", "ˈ").replace("ɒː", "ɔː")
    }
}

/** 英式英语（和电脑版 uk_english.py 一致），数据在 assets/uk_english.json（tools/make_assets.py 从电脑版复制）：
 *  - spelling：美式拼写 → 英式拼写（color → colour），来自 tysto.com 的英美拼写对照表，经 hyperreality/American-British-English-Translator 整理（MIT）
 *  - partial：只对部分义项成立的拼写差异（tire 指轮胎时写 tyre），只加说明，不改标题
 *  - vocab：常见的英美用词差异（elevator → 英式说 lift） */
private class UkEnglish(json: JSONObject) {
    val spelling = json.section("spelling") // 美式拼写 → 英式拼写
    val partial = json.section("partial") // 只有部分义项不同的拼写：tire → 指轮胎时英式写作 tyre
    val vocab = json.section("vocab") // 美式用词 → 英式说法：elevator → 英式说 lift
    val american = spelling.entries.associate { (us, gb) -> gb to us } // 英式拼写 → 美式拼写

    /** 美式拼写的英式写法，没有差异时原样返回（保留首字母大写）。 */
    fun british(word: String): String {
        val gb = spelling[word.lowercase()].nz() ?: return word
        return if (word.firstOrNull()?.isUpperCase() == true) pyCapitalize(gb) else gb
    }

    /** 这个词（原形，小写）的英美差异说明：部分义项的拼写差异、用词差异。 */
    fun notes(word: String): List<String> = listOfNotNull(partial[word].nz(), vocab[word].nz())

    private fun JSONObject.section(name: String): Map<String, String> =
        optJSONObject(name)?.let { o -> o.keys().asSequence().associateWith { o.getString(it) } }.orEmpty()

    companion object {
        /** 读 assets/uk_english.json。没有或读不了时不影响查词：当作没有英美差异数据，只记一条警告。 */
        fun load(assets: AssetManager): UkEnglish = UkEnglish(
            runCatching { JSONObject(assets.open("uk_english.json").bufferedReader().use { it.readText() }) }
                .onFailure { Log.w(App.TAG, "读不了 uk_english.json，英文卡片不做英式处理", it) }
                .getOrElse { JSONObject() })
    }
}

/** 同 Python 的 str.capitalize()：首字母大写，其余小写。 */
private fun pyCapitalize(s: String): String =
    if (s.isEmpty()) s else s.substring(0, 1).uppercase() + s.substring(1).lowercase()

private fun String?.nz() = this?.takeIf { it.isNotEmpty() }

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
