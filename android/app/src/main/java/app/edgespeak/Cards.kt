package app.edgespeak

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.graphics.Typeface
import android.view.MotionEvent
import android.view.View
import java.util.Locale
import kotlin.math.abs

/** 查词卡片和英文卡片（和电脑版 jp_card.py 的排版一致）。先排版算出高度，再一次性画。 */

private const val BG = 0xFFFFFFFF.toInt()
private const val BORDER = 0xFFE2E8F0.toInt()
private const val TEXT = 0xFF111827.toInt()
private const val MUTED = 0xFF6B7280.toInt()
private const val FAINT = 0xFF9CA3AF.toInt()
private const val ACCENT = 0xFF0078D4.toInt()
private const val PITCH = 0xFFEA580C.toInt()
private const val CHIP_BG = 0xFFEEF2FF.toInt()
private const val CHIP_TEXT = 0xFF3730A3.toInt()
private const val AI_BG = 0xFFF5F3FF.toInt()
private const val AI_ACCENT = 0xFF6D28D9.toInt()
private const val HIGHLIGHT = 0xFFFEF3C7.toInt()
private const val ERROR = 0xFFB91C1C.toInt()
private const val REGISTER = 0xFFBE123C.toInt() // 网络用语、俚语等标记
private const val ZH_BG = 0xFFF0FDF4.toInt()
private const val ZH_ACCENT = 0xFF15803D.toInt()

private const val MAX_SENSES = 5 // 英文释义最多显示几条（AI 选中的那条在后面也会补上）
private const val MAX_ZH_LINES = 6

private val POS_ZH = mapOf(
    "n" to "名词", "pn" to "代词", "v1" to "一段动词", "v1-s" to "一段动词", "vk" to "カ变动词", "vs" to "可加する",
    "vs-i" to "サ变动词", "vs-s" to "サ变动词", "vz" to "サ变动词", "vt" to "他动词", "vi" to "自动词",
    "adj-i" to "い形容词", "adj-ix" to "い形容词", "adj-na" to "な形容词", "adj-no" to "可加の", "adj-t" to "タル形容词",
    "adj-pn" to "连体词", "adj-f" to "连体词", "adv" to "副词", "adv-to" to "副词", "aux" to "助动词", "aux-v" to "助动词",
    "aux-adj" to "助形容词", "conj" to "连词", "cop" to "系词", "ctr" to "量词", "exp" to "惯用语", "int" to "感叹词",
    "num" to "数词", "pref" to "前缀", "suf" to "后缀", "n-suf" to "名词后缀", "n-pref" to "名词前缀", "prt" to "助词",
    // 维基词典的词性
    "noun" to "名词", "verb" to "动词", "adj" to "形容词", "adj_noun" to "な形容词", "intj" to "感叹词",
    "name" to "专有名词", "phrase" to "短语", "proverb" to "谚语", "suffix" to "后缀", "prefix" to "前缀", "pron" to "代词",
    "particle" to "助词", "postp" to "助词", "counter" to "量词", "classifier" to "量词", "det" to "连体词",
)

private fun posLabels(tags: List<String>) = tags.mapNotNull { if (it.startsWith("v5")) "五段动词" else POS_ZH[it] }.distinct()

private val NO_LINE_START = "。、，．,.！？!?）)」』】〕”’ー々ゃゅょっャュョッ：；:;".toSet()
private val NO_LINE_END = "（(「『【〔“‘".toSet()
private val WORD = Regex("[A-Za-z][A-Za-z'-]*[A-Za-z]|[A-Za-z]")

/** 按宽度折行（英文按空格断，太长的词硬断）。 */
private fun wrapWords(p: Paint, text: String, width: Float): List<String> {
    val lines = mutableListOf<String>()
    var line = ""
    for (raw in text.split(" ")) {
        var word = raw
        val candidate = "$line $word".trim()
        if (p.measureText(candidate) <= width) {
            line = candidate
            continue
        }
        if (line.isNotEmpty()) lines += line
        while (word.length > 1 && p.measureText(word) > width) {
            var cut = word.length
            while (cut > 1 && p.measureText(word, 0, cut) > width) cut--
            lines += word.substring(0, cut)
            word = word.substring(cut)
        }
        line = word
    }
    if (line.isNotEmpty()) lines += line
    return lines
}

/** 按字折行（中日文没有空格可断）。句号、右括号等不放在行首，左括号不放在行尾。 */
private fun wrapChars(p: Paint, text: String, width: Float): List<String> {
    val lines = mutableListOf<String>()
    var line = StringBuilder()
    for (c in text) {
        if (p.measureText(line.toString() + c) > width && line.isNotEmpty() && c !in NO_LINE_START) {
            var carry = ""
            while (line.isNotEmpty() && line.last() in NO_LINE_END) {
                carry = line.last() + carry
                line.setLength(line.length - 1)
            }
            if (line.isNotEmpty()) lines += line.toString()
            line = StringBuilder((carry + c).trimStart())
        } else line.append(c)
    }
    if (line.isNotEmpty()) lines += line.toString()
    return lines
}

/** 两种卡片共用的：画布操作列表、点击区域、药丸标签、喇叭图标、AI 区。 */
abstract class CardBase(context: Context) : View(context) {
    protected val density = resources.displayMetrics.density
    protected val scaled = resources.displayMetrics.scaledDensity
    protected fun dp(v: Float) = v * density
    protected fun sp(v: Float) = v * scaled
    protected val pad get() = dp(16f)

    protected val ops = mutableListOf<(Canvas) -> Unit>()
    protected var contentHeight = 0f
    var headerBottom = 0f
        protected set
    protected var aiRegion: ClosedFloatingPointRange<Float>? = null
    var ai: AiState = AiState("off")
        set(value) {
            field = value
            requestLayout()
            invalidate()
        }

    var onSpeak: () -> Unit = {}
    var onAi: () -> Unit = {}

    protected fun paint(sizeSp: Float, color: Int = TEXT, bold: Boolean = false, locale: Locale = Locale.SIMPLIFIED_CHINESE) =
        Paint(Paint.ANTI_ALIAS_FLAG).apply {
            textSize = sp(sizeSp)
            this.color = color
            typeface = if (bold) Typeface.DEFAULT_BOLD else Typeface.DEFAULT
            textLocale = locale // 同一个汉字中日字形不同，按语言选字体
        }

    protected val zhBody = paint(14f)
    private val pillPaint = paint(12f)
    private val fill = Paint(Paint.ANTI_ALIAS_FLAG)

    protected abstract fun build(width: Float)

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val width = MeasureSpec.getSize(widthMeasureSpec)
        ops.clear()
        aiRegion = null
        build(width.toFloat())
        setMeasuredDimension(width, contentHeight.toInt())
    }

    override fun onDraw(canvas: Canvas) {
        fill.color = BG
        canvas.drawRect(0f, 0f, width.toFloat(), height.toFloat(), fill)
        ops.forEach { it(canvas) }
    }

    /** 文字的基线位置：给的 y 是这一行的顶部。 */
    protected fun baseline(p: Paint, top: Float) = top - p.fontMetrics.ascent

    protected fun pill(c: Canvas, x: Float, y: Float, text: String, bg: Int, fg: Int): Float {
        val w = pillPaint.measureText(text) + dp(14f)
        fill.color = bg
        c.drawRoundRect(RectF(x, y, x + w, y + dp(20f)), dp(10f), dp(10f), fill)
        pillPaint.color = fg
        c.drawText(text, x + dp(7f), y + dp(10f) - (pillPaint.fontMetrics.ascent + pillPaint.fontMetrics.descent) / 2, pillPaint)
        return x + w
    }

    protected fun pillWidth(text: String) = pillPaint.measureText(text) + dp(14f)

    protected fun speakerIcon(c: Canvas, x: Float, y: Float) {
        fill.color = ACCENT
        fill.style = Paint.Style.FILL
        c.drawRect(x, y + dp(5f), x + dp(4f), y + dp(11f), fill)
        c.drawPath(Path().apply {
            moveTo(x + dp(4f), y + dp(5f)); lineTo(x + dp(9f), y + dp(1f)); lineTo(x + dp(9f), y + dp(15f))
            lineTo(x + dp(4f), y + dp(11f)); close()
        }, fill)
        fill.style = Paint.Style.STROKE
        fill.strokeWidth = dp(1.5f)
        for (r in listOf(5f, 9f)) {
            c.drawArc(RectF(x + dp(9f - r), y + dp(8f - r), x + dp(9f + r), y + dp(8f + r)), -50f, 100f, false, fill)
        }
        fill.style = Paint.Style.FILL
    }

    protected fun roundBox(c: Canvas, top: Float, bottom: Float, color: Int) {
        fill.color = color
        c.drawRoundRect(RectF(pad, top, width - pad, bottom), dp(10f), dp(10f), fill)
    }

    protected fun hline(c: Canvas, y: Float) {
        fill.color = BORDER
        c.drawRect(pad, y, width - pad, y + maxOf(1f, dp(1f)), fill)
    }

    protected open fun aiRow(name: String, value: String, state: String): Pair<String, Int>? = value to TEXT

    /** 排版 AI 区，返回下一块的 y。names 是要显示的字段，button 是还没请求时按钮上的字。 */
    protected fun layoutAi(y: Float, width: Float, fields: Map<String, String>, names: List<String>, button: String): Float {
        val state = ai.state
        val text = ai.text
        if (state == "off") return y
        val inner = width - pad * 2 - dp(24f)
        val top = y
        if (state == null || state == "error") {
            // 还没请求 / 出错：一个可点的按钮（出错时点了重试）。还没请求时 text 可以替换按钮文字
            val lines = if (state == "error") wrapChars(zhBody, text, inner) else emptyList()
            val lineH = dp(20f)
            val height = dp(36f) + lineH * lines.size
            val label = if (state == "error") "点击重试" else text.ifEmpty { button }
            val labelPaint = paint(14f, AI_ACCENT, bold = true)
            val errorPaint = paint(14f, ERROR)
            ops += { c ->
                roundBox(c, top, top + height, AI_BG)
                val cx = pill(c, pad + dp(10f), top + dp(8f), "AI", AI_ACCENT, BG)
                c.drawText(label, cx + dp(8f), baseline(labelPaint, top + dp(8f)), labelPaint)
                var ly = top + dp(32f)
                for (l in lines) {
                    c.drawText(l, pad + dp(12f), baseline(errorPaint, ly), errorPaint)
                    ly += lineH
                }
            }
            aiRegion = top..(top + height)
            return top + height + dp(12f)
        }
        // 加载中 / 生成中 / 完成
        data class Row(val label: String?, val text: String, val color: Int)
        val rows = mutableListOf<Row>()
        if (fields.isNotEmpty()) {
            for (name in names) {
                val raw = fields[name]?.takeIf { it.isNotEmpty() } ?: continue
                val (value, color) = aiRow(name, raw, state) ?: continue
                val plain = name == "义项" // 写成一句话，从左边开始，不带标签
                val w = if (plain) inner else inner - dp(44f)
                wrapChars(zhBody, value, w).forEachIndexed { k, line ->
                    rows += Row(if (plain) "" else if (k == 0) name else null, line, if (plain) AI_ACCENT else color)
                }
            }
        } else if (text.isNotBlank()) {
            wrapChars(zhBody, text.trim().replace("\n", " "), inner).forEach { rows += Row(null, it, TEXT) }
        }
        if (state != "done") rows += Row(null, if (state == "thinking") "AI 正在思考…" else "AI 正在分析…", FAINT)
        val lineH = dp(22f)
        val height = dp(14f) + lineH * rows.size
        val paints = rows.associate { it.color to paint(14f, it.color) }
        ops += { c ->
            roundBox(c, top, top + height, AI_BG)
            var ly = top + dp(7f)
            for (row in rows) {
                var x = pad + dp(12f)
                if (!row.label.isNullOrEmpty()) pill(c, x, ly, row.label, AI_ACCENT, BG)
                if (fields.isNotEmpty() && row.label != "") x += dp(44f)
                c.drawText(row.text, x, baseline(paints.getValue(row.color), ly + dp(1f)), paints.getValue(row.color))
                ly += lineH
            }
        }
        return top + height + dp(12f)
    }

    private var downX = 0f
    private var downY = 0f

    @SuppressLint("ClickableViewAccessibility")
    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.action) {
            MotionEvent.ACTION_DOWN -> {
                downX = event.x
                downY = event.y
                return true
            }
            MotionEvent.ACTION_UP -> if (abs(event.x - downX) < dp(10f) && abs(event.y - downY) < dp(10f)) {
                onTap(event.x, event.y)
                return true
            }
        }
        return super.onTouchEvent(event)
    }

    protected open fun onTap(x: Float, y: Float) {
        val region = aiRegion
        when {
            region != null && y in region -> onAi()
            y < headerBottom -> onSpeak()
        }
    }
}

/** 日语查词卡片。点标题读原文；点 AI 区请求解释；点英文单词查它（onWord：单词，所在义项编号）。 */
class JapaneseCard(context: Context) : CardBase(context) {
    var word: WordInfo? = null
        set(value) {
            field = value
            requestLayout()
            invalidate()
        }
    var onWord: (String, Int) -> Unit = { _, _ -> }

    private data class Link(val rect: RectF, val word: String, val sense: Int)
    private val links = mutableListOf<Link>()

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        links.clear()
        super.onMeasure(widthMeasureSpec, heightMeasureSpec)
    }

    override fun build(width: Float) {
        val w = word ?: run { contentHeight = 0f; return }
        var y = pad

        // 标题：带假名注音的原文。注音可以伸出汉字两侧；相邻两段注音会重叠时才把后面的字往右挪
        val big = paint(28f, bold = true, locale = Locale.JAPANESE)
        val small = paint(12f, MUTED, locale = Locale.JAPANESE)
        val rubyH = if (w.ruby.any { it.reading != null }) dp(16f) else 0f
        var x = pad
        var rubyRight = -1e9f
        data class Seg(val x: Float, val text: String, val reading: String?, val tw: Float, val rw: Float)
        val segs = mutableListOf<Seg>()
        for (r in w.ruby) {
            val tw = big.measureText(r.text)
            val rw = r.reading?.let { small.measureText(it) } ?: 0f
            if (r.reading != null) {
                x += maxOf(0f, rubyRight + dp(2f) - (x + (tw - rw) / 2))
                x = maxOf(x, pad + maxOf(0f, (rw - tw) / 2))
                rubyRight = x + (tw + rw) / 2
            }
            segs += Seg(x, r.text, r.reading, tw, rw)
            x += tw
        }
        val titleY = y
        y += rubyH + dp(38f)
        headerBottom = y + dp(6f)
        ops += { c ->
            for (s in segs) {
                if (s.reading != null) c.drawText(s.reading, s.x + (s.tw - s.rw) / 2, baseline(small, titleY), small)
                c.drawText(s.text, s.x, baseline(big, titleY + rubyH), big)
            }
            speakerIcon(c, width - pad - dp(18f), titleY + rubyH + dp(10f))
        }
        y += dp(8f)
        val sep = y
        ops += { c -> hline(c, sep) }

        // 原型 / 读音 + 音高
        y += dp(12f)
        val label = if (w.surface != w.base) "原型" else "读音"
        val baseY = y
        val morae = splitMora(w.baseReading)
        val basePaint = paint(20f, bold = true, locale = Locale.JAPANESE)
        val moraPaint = paint(18f, locale = Locale.JAPANESE)
        val badgePaint = paint(11f, PITCH, locale = Locale.US)
        val line = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = PITCH; strokeWidth = maxOf(1f, dp(2f)); style = Paint.Style.STROKE }
        ops += { c ->
            var cx = pill(c, pad, baseY + dp(4f), label, ACCENT, BG) + dp(10f)
            if (w.base != w.baseReading) {
                c.drawText(w.base, cx, baseline(basePaint, baseY), basePaint)
                cx += basePaint.measureText(w.base) + dp(12f)
            }
            // 假名上方画音高线：高的拍上面有横线，下降处画一条竖线
            val accent = w.accents.firstOrNull()
            val highs = if (accent != null) pitchPattern(morae.size, accent) else List(morae.size) { false }
            val top = baseY + dp(2f)
            morae.forEachIndexed { i, mora ->
                val mw = moraPaint.measureText(mora)
                c.drawText(mora, cx, baseline(moraPaint, baseY + dp(5f)), moraPaint)
                if (accent != null && highs[i]) {
                    c.drawLine(cx, top, cx + mw, top, line)
                    val next = if (i + 1 < highs.size) highs[i + 1] else accent == 0
                    if (!next) c.drawLine(cx + mw, top, cx + mw, top + dp(9f), line) // 下降核
                }
                cx += mw
            }
            for (a in w.accents) { // 圆圈数字：音调型
                val r = dp(9f)
                val bx = cx + dp(6f)
                val by = baseY + dp(6f)
                c.drawCircle(bx + r, by + r, r, line.apply { strokeWidth = maxOf(1f, dp(1.5f)) })
                c.drawText(a.toString(), bx + r - badgePaint.measureText(a.toString()) / 2,
                    by + r - (badgePaint.fontMetrics.ascent + badgePaint.fontMetrics.descent) / 2, badgePaint)
                line.strokeWidth = maxOf(1f, dp(2f))
                cx = bx + 2 * r
            }
        }
        y += dp(36f)

        // 词性
        val labels = posLabels(w.pos)
        if (labels.isNotEmpty()) {
            val chipsY = y
            ops += { c ->
                var cx = pad
                for (lab in labels) {
                    if (cx + pillWidth(lab) > width - pad) break
                    cx = pill(c, cx, chipsY, lab, CHIP_BG, CHIP_TEXT) + dp(6f)
                }
            }
            y += dp(30f)
        }

        // 释义（AI 选中的义项高亮），释义前标出网络用语等
        val fields = if (ai.state == "streaming" || ai.state == "done") parseAnswer(ai.text) else emptyMap()
        val chosen = if (ai.state == "done") chosenSense(fields) else null
        val gloss = paint(15f, locale = Locale.US)
        val num = paint(15f, FAINT, locale = Locale.US)
        val regPaint = paint(12.5f, REGISTER)
        val hintPaint = paint(13.5f, FAINT)
        data class GLine(val number: Int?, val sense: Int?, val text: String, val reg: String)
        val lines = mutableListOf<GLine>()
        val shown = (1..minOf(w.senses.size, MAX_SENSES)).toMutableList()
        if (chosen != null && chosen > MAX_SENSES && chosen <= w.senses.size) shown += chosen
        val textWidth = width - pad * 2 - dp(24f)
        for (n in shown) {
            val sense = w.senses[n - 1]
            val reg = registerLabels(sense.misc).joinToString("·").let { if (it.isEmpty()) "" else "〔$it〕" }
            wrapWords(gloss, sense.glosses.joinToString("; "), textWidth - regPaint.measureText(reg))
                .forEachIndexed { k, l -> lines += GLine(if (k == 0) n else null, n, l, if (k == 0) reg else "") }
        }
        val more = w.senses.size - shown.size
        if (more > 0) lines += GLine(null, null, "…… 还有 $more 条英文释义", "")
        if (w.senses.isEmpty()) lines += GLine(null, null, if (w.zh.isNotEmpty()) "（中文释义见下方）" else "（词典里没有这个词）", "")
        val glossTop = y + dp(2f)
        val lineH = dp(23f)
        lines.forEachIndexed { k, l ->
            if (l.sense == null) return@forEachIndexed
            val x0 = pad + dp(24f) + regPaint.measureText(l.reg)
            val ly = glossTop + k * lineH
            for (m in WORD.findAll(l.text)) {
                val left = x0 + gloss.measureText(l.text, 0, m.range.first)
                links += Link(RectF(left, ly, left + gloss.measureText(m.value), ly + lineH), m.value, l.sense)
            }
        }
        ops += { c ->
            var ly = glossTop
            val highlight = Paint().apply { color = HIGHLIGHT }
            for (l in lines) {
                if (chosen != null && l.sense == chosen) c.drawRect(pad - dp(6f), ly - dp(1f), width - pad + dp(6f), ly + lineH - dp(1f), highlight)
                if (l.number != null) c.drawText("${l.number}.", pad, baseline(num, ly), num)
                var x0 = pad + dp(24f)
                if (l.reg.isNotEmpty()) {
                    c.drawText(l.reg, x0, baseline(regPaint, ly + dp(2f)), regPaint)
                    x0 += regPaint.measureText(l.reg)
                }
                if (l.sense == null) c.drawText(l.text, if (w.senses.isNotEmpty()) x0 else pad, baseline(hintPaint, ly + dp(1f)), hintPaint)
                else c.drawText(l.text, x0, baseline(gloss, ly), gloss)
                ly += lineH
            }
        }
        y = glossTop + lineH * lines.size + dp(8f)

        // 中文释义（中文维基词典）
        if (w.zh.isNotEmpty()) y = layoutZh(y, width, w)

        // AI 区。“拆解”由本地分词生成（确定且准确），不让 AI 猜
        val aiFields = if (fields.isEmpty()) fields else fields + ("拆解" to breakdown(w))
        y = layoutAi(y, width, aiFields, listOf("释义", "读音", "义项", "拆解", "翻译"), "结合上下文解释（中文）")

        val hint = paint(11.5f, FAINT)
        val hintY = y
        ops += { c -> c.drawText("点标题朗读 · 点英文单词查它的释义", pad, baseline(hint, hintY), hint) }
        contentHeight = y + dp(18f) + pad
    }

    private fun layoutZh(y: Float, width: Float, w: WordInfo): Float {
        val regPaint = paint(13f, REGISTER)
        val textWidth = width - pad * 2 - dp(24f) - dp(54f)
        data class ZLine(val first: Boolean, val reg: String, val text: String)
        var rows = mutableListOf<ZLine>()
        for (z in w.zh) {
            val reg = registerLabels(z.tags).joinToString("·").let { if (it.isEmpty()) "" else "〔$it〕" }
            wrapChars(zhBody, reg + z.text, textWidth).forEachIndexed { k, l ->
                rows += ZLine(k == 0, if (k == 0) reg else "", if (k == 0) l.removePrefix(reg) else l)
            }
        }
        if (rows.size > MAX_ZH_LINES) rows = (rows.take(MAX_ZH_LINES - 1) + ZLine(true, "", "……")).toMutableList()
        val top = y
        val lineH = dp(22f)
        val height = dp(12f) + lineH * rows.size
        val bullet = paint(14f, FAINT)
        ops += { c ->
            roundBox(c, top, top + height, ZH_BG)
            pill(c, pad + dp(10f), top + dp(6f), "中文", ZH_ACCENT, BG)
            var ly = top + dp(6f)
            for (r in rows) {
                var x = pad + dp(12f) + dp(54f)
                if (r.first && r.text != "……") c.drawText("·", x - dp(10f), baseline(bullet, ly), bullet)
                if (r.reg.isNotEmpty()) {
                    c.drawText(r.reg, x, baseline(regPaint, ly + dp(1f)), regPaint)
                    x += regPaint.measureText(r.reg)
                }
                c.drawText(r.text, x, baseline(zhBody, ly), zhBody)
                ly += lineH
            }
        }
        return top + height + dp(12f)
    }

    override fun aiRow(name: String, value: String, state: String): Pair<String, Int>? {
        val w = word ?: return null
        return when (name) {
            "读音" -> {
                // 只在 AI 认为这里的读法和卡片上标的不一样时提醒（如「今日」读 こんにち）
                if (state != "done") return null
                val local = w.surfaceReading
                val aiReading = toHiragana(value.trim().replace(Regex("[\\s（(].*$"), ""))
                if (aiReading.isEmpty() || aiReading == local || !aiReading.matches(Regex("[\\u3041-\\u309fー]+"))) null
                else "这里读「$aiReading」（上面标的是「$local」）" to ERROR
            }
            "义项" -> {
                // 写成一句话：对应上面第几条英文释义（那一条同时被高亮）
                if (w.senses.isEmpty()) return null
                val n = chosenSense(mapOf("义项" to value))
                when {
                    n != null && n <= w.senses.size -> "对应上面第 $n 条英文释义" to AI_ACCENT
                    "无" in value || n != null || state == "done" -> "词典释义都不贴切" to AI_ACCENT
                    else -> null // 还在输出中，等数字出来
                }
            }
            else -> value to TEXT
        }
    }

    override fun onTap(x: Float, y: Float) {
        val link = links.firstOrNull { it.rect.contains(x, y) }
        if (link != null && (aiRegion?.let { y !in it } != false)) onWord(link.word, link.sense) else super.onTap(x, y)
    }
}

/** 点日语卡片里的英文单词后显示的英文卡片。entry 为 null 表示查不到。点标题读英文；AI 区解释它在那条释义里的意思。 */
class EnglishCard(context: Context) : CardBase(context) {
    var entry: EnglishWord? = null
    var clicked: String = ""

    fun show(entry: EnglishWord?, clicked: String, ai: AiState) {
        this.entry = entry
        this.clicked = clicked
        this.ai = ai
    }

    val wordText get() = entry?.word ?: clicked

    override fun build(width: Float) {
        val e = entry
        var y = pad
        val title = paint(26f, bold = true, locale = Locale.US)
        val phonetic = paint(15f, MUTED, locale = Locale.US)
        val titleY = y
        ops += { c ->
            c.drawText(wordText, pad, baseline(title, titleY), title)
            if (e != null && e.phonetic.isNotEmpty()) {
                c.drawText("/${e.phonetic}/", pad + title.measureText(wordText) + dp(10f), baseline(phonetic, titleY + dp(9f)), phonetic)
            }
            speakerIcon(c, width - pad - dp(18f), titleY + dp(10f))
        }
        y += dp(40f)
        headerBottom = y
        if (e != null && e.via.isNotEmpty()) {
            val via = paint(12.5f, MUTED)
            val viaY = y
            ops += { c -> c.drawText(e.via, pad, baseline(via, viaY), via) }
            y += dp(20f)
        }
        y += dp(6f)
        val sep = y
        ops += { c -> hline(c, sep) }
        y += dp(10f)

        // 中文释义：词性 + 意思，每个词性一行（长的折行）
        val body = paint(15f)
        val posPaint = paint(13.5f, FAINT)
        val rows = mutableListOf<Pair<String?, String>>()
        for (line in e?.lines ?: emptyList()) {
            val m = Regex("""^([a-z]+\.|\[[^\]]+\])\s*(.*)$""").find(line)
            val (pos, meaning) = if (m != null) m.groupValues[1] to m.groupValues[2] else "" to line
            wrapChars(body, meaning, width - pad * 2 - dp(48f)).forEachIndexed { k, part -> rows += (if (k == 0) pos else null) to part }
        }
        if (rows.isEmpty()) rows += null to "离线词典里没有这个词。"
        val rowsY = y
        val lineH = dp(24f)
        ops += { c ->
            var ly = rowsY
            for ((pos, text) in rows) {
                if (!pos.isNullOrEmpty()) c.drawText(pos, pad, baseline(posPaint, ly + dp(1f)), posPaint)
                c.drawText(text, if (e != null) pad + dp(48f) else pad, baseline(body, ly), body)
                ly += lineH
            }
        }
        y += lineH * rows.size + dp(6f)

        if (e != null && e.tags.isNotEmpty()) {
            val tagsY = y
            ops += { c -> var cx = pad; for (t in e.tags) cx = pill(c, cx, tagsY, t, CHIP_BG, CHIP_TEXT) + dp(6f) }
            y += dp(28f)
        }
        if (e != null && e.forms.isNotEmpty()) {
            val small = paint(12.5f, MUTED)
            val formLines = mutableListOf("") // 按整项折行，不把“三单 eats”拆到两行
            for ((label, form) in e.forms) {
                val item = "$label $form"
                val candidate = if (formLines.last().isEmpty()) item else "${formLines.last()} · $item"
                if (formLines.last().isNotEmpty() && small.measureText(candidate) > width - pad * 2) formLines += item
                else formLines[formLines.size - 1] = candidate
            }
            val formsY = y
            ops += { c -> formLines.forEachIndexed { k, l -> c.drawText(l, pad, baseline(small, formsY + k * dp(19f)), small) } }
            y += dp(19f) * formLines.size + dp(6f)
        }

        // AI：这个英文单词在那条日语释义里的意思（点了才请求）
        val fields = if (ai.state == "streaming" || ai.state == "done") parseAnswer(ai.text) else emptyMap()
        y = layoutAi(y + dp(4f), width, fields, listOf("意思", "整条"), "它在这条释义里是什么意思")

        val hint = paint(11.5f, FAINT)
        val hintY = y
        ops += { c -> c.drawText("点单词朗读", pad, baseline(hint, hintY), hint) }
        contentHeight = y + dp(18f) + pad
    }
}
