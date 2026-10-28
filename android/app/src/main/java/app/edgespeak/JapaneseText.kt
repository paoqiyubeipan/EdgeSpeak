package app.edgespeak

/** 假名、注音、音高、变形拆解等和词典无关的文字处理（和电脑版 jp_dict.py 一致）。 */

private val SMALL_KANA = "ゃゅょぁぃぅぇぉゎャュョァィゥェォヮ".toSet()

fun toHiragana(text: String): String = buildString {
    for (c in text) append(if (c in 'ァ'..'ヶ') c - 0x60 else c)
}

fun isKana(c: Char) = c in '぀'..'ヿ'

fun isKanji(c: Char) = c in '㐀'..'鿿' || c in '豈'..'﫿' || c == '々'

fun hasKanji(text: String) = text.any(::isKanji)

fun isJapanese(text: String) = text.any { isKanji(it) || isKana(it) }

/** かな → 拍。小写的ゃゅょ等并入前一拍，っ、ー、ん各算一拍。 */
fun splitMora(reading: String): List<String> {
    val morae = mutableListOf<String>()
    for (c in reading) {
        if (c in SMALL_KANA && morae.isNotEmpty()) morae[morae.size - 1] += c else morae += c.toString()
    }
    return morae
}

/** 音调型（核的位置）→ 每拍高低。0=平板，1=头高，n=第 n 拍后下降。 */
fun pitchPattern(n: Int, accent: Int): List<Boolean> = List(n) { i ->
    when (accent) {
        0 -> i > 0
        1 -> i == 0
        else -> i in 1 until accent
    }
}

/** 一段文字和它上面标的读音；读音为 null 表示不用标（假名）。 */
data class Ruby(val text: String, val reading: String?)

/** 把读音对齐到每段汉字上：取り扱っ / とりあつかっ → [(取, と), (り, null), (扱, あつか), (っ, null)]。 */
fun rubySegments(surface: String, reading: String): List<Ruby> {
    if (reading.isEmpty() || !hasKanji(surface)) return listOf(Ruby(surface, null))
    val runs = mutableListOf<Pair<String, Boolean>>() // （文字，是否汉字）
    for (c in surface) {
        val kanji = !isKana(c)
        if (runs.isNotEmpty() && runs.last().second == kanji) runs[runs.size - 1] = runs.last().first + c to kanji
        else runs += c.toString() to kanji
    }
    val pattern = runs.joinToString("") { (text, kanji) -> if (kanji) "(.+?)" else Regex.escape(toHiragana(text)) }
    val match = Regex(pattern).matchEntire(reading) ?: return listOf(Ruby(surface, reading))
    val groups = match.groupValues.drop(1).iterator()
    return runs.map { (text, kanji) -> Ruby(text, if (kanji) groups.next() else null) }
}

private const val SENTENCE_END = "。！？!?\n"
private const val MAX_SENTENCE = 200

/** （start..end 所在的那一句，目标词在这句里的起点）。句子太长时以目标词为中心截取。 */
fun sentenceAround(text: String, start: Int, end: Int): Pair<String, Int> {
    val s = (SENTENCE_END.maxOf { text.lastIndexOf(it, start - 1) }) + 1
    val ends = SENTENCE_END.map { text.indexOf(it, end) }.filter { it >= 0 }
    val e = if (ends.isEmpty()) text.length else ends.min() + 1
    return clipSentence(text, s, e, start, end)
}

/** text[s..e) 这一句太长时以目标词（start..end）为中心截取，去掉两头空白。sentenceAround、englishSentenceAround 共用。 */
fun clipSentence(text: String, sentenceStart: Int, sentenceEnd: Int, start: Int, end: Int): Pair<String, Int> {
    var s = sentenceStart
    var e = sentenceEnd
    if (e - s > MAX_SENTENCE) {
        val half = (MAX_SENTENCE - (end - start)) / 2
        s = maxOf(s, start - half)
        e = minOf(e, end + half)
    }
    val segment = text.substring(s, e)
    val lead = segment.length - segment.trimStart().length
    return segment.trim() to (start - s - lead)
}

// 语体 / 用法标记：JMdict 的 misc 代码和维基词典的 tags 都转成中文，显示在释义前面
private val REGISTER_LABELS = mapOf(
    "net-sl" to "网络用语", "Internet" to "网络用语", "sl" to "俚语", "slang" to "俚语", "neologism" to "新词",
    "col" to "口语", "colloquial" to "口语", "informal" to "非正式", "arch" to "古语", "archaic" to "古语",
    "obs" to "旧词", "obsolete" to "旧词", "dated" to "旧", "hon" to "尊敬语", "honorific" to "尊敬语",
    "hum" to "谦让语", "humble" to "谦让语", "pol" to "礼貌语", "polite" to "礼貌语", "vulg" to "粗俗",
    "vulgar" to "粗俗", "derog" to "贬义", "derogatory" to "贬义", "abbr" to "缩略", "abbreviation" to "缩略",
    "joc" to "玩笑", "humorous" to "玩笑", "fam" to "亲昵", "rare" to "罕用", "poet" to "诗歌", "male" to "男性用语",
    "fem" to "女性用语", "chn" to "儿语", "form" to "书面语", "formal" to "书面语", "figuratively" to "比喻",
    "euph" to "委婉", "euphemistic" to "委婉", "on-mim" to "拟声拟态", "onomatopoeia" to "拟声", "id" to "惯用语",
    "idiomatic" to "惯用语", "yoji" to "四字熟语", "proverb" to "谚语", "sens" to "敏感", "Kansai" to "关西方言",
    "ksb" to "关西方言", "dialectal" to "方言",
)
val SLANG_TAGS = setOf("Internet", "slang", "neologism", "net-sl", "sl")
val NET_TAGS = setOf("Internet", "neologism", "net-sl")

fun registerLabels(tags: List<String>): List<String> = tags.mapNotNull { REGISTER_LABELS[it] }.distinct()

// 变形拆解：词尾成分的作用。助动词按词元查（读んだ 的 だ 是过去的 た），其余按原型查
private val PART_ROLES = mapOf(
    "ない" to "否定", "ぬ" to "否定", "ん" to "否定", "まい" to "否定意志", "た" to "过去 / 完成", "だ" to "断定",
    "ます" to "礼貌", "です" to "礼貌", "たい" to "愿望", "たがる" to "愿望，第三人称",
    "れる" to "被动 / 可能 / 尊敬", "られる" to "被动 / 可能 / 尊敬", "せる" to "使役", "させる" to "使役",
    "う" to "意志 / 推量", "よう" to "意志 / 推量", "らしい" to "推测", "そう" to "样态", "べし" to "应当",
    "て" to "连接", "で" to "连接", "ば" to "假定", "ちゃ" to "连接，口语", "じゃ" to "连接，口语",
    "いる" to "持续 / 状态，ている", "ある" to "状态，てある", "しまう" to "完成 / 遗憾，てしまう",
    "おく" to "预先，ておく", "いく" to "趋向，ていく", "くる" to "趋向，てくる", "みる" to "尝试，てみる",
    "くださる" to "请求，てください", "もらう" to "受益，てもらう", "くれる" to "受益，てくれる",
    "あげる" to "给予，てあげる", "いける" to "可能，能…下去", "なる" to "变化", "ほしい" to "希望，てほしい",
)

/** “食べる + ない（否定）+ た（过去 / 完成）”。原型后面没有变形词尾时返回空串。 */
fun breakdown(word: WordInfo): String {
    val tail = word.tail.toMutableList()
    if (tail.isNotEmpty() && tail[0].base == "する" && word.base.endsWith("する")) tail.removeAt(0) // 連絡 + する 已经合成原型
    if (tail.isEmpty()) return ""
    val items = mutableListOf(word.base)
    for (part in tail) {
        val role = PART_ROLES[if (part.pos1 == "助動詞") part.lemma else part.base] ?: PART_ROLES[part.base]
        items += if (role != null) "${part.base}（$role）" else part.base
    }
    return items.joinToString(" + ")
}
