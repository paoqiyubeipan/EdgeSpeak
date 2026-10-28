package app.edgespeak

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.text.Spannable
import android.text.SpannableString
import android.text.style.BackgroundColorSpan
import android.text.style.ForegroundColorSpan
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView

/** 查词面板：上面是取到的文字（点哪个字就查哪个词），下面是查词卡片、英文卡片和按钮。
 *  点到的不是日语而是英文单词时，日语卡片不显示，英文卡片结合原文句子解释它。
 *  选字菜单（LookupActivity）和悬浮取词（ScreenLookupService）共用。 */
@SuppressLint("ViewConstructor")
class CardPanel(context: Context, private val maxHeight: Int, private val onClose: () -> Unit) : LinearLayout(context) {
    private val app = App.instance
    private val density = resources.displayMetrics.density
    private fun dp(v: Int) = (v * density).toInt()

    private val scroll = ScrollView(context)
    private val content = LinearLayout(context).apply { orientation = VERTICAL }
    private val sentence = TextView(context).apply {
        textSize = 17f
        setTextColor(0xFF374151.toInt())
        setLineSpacing(0f, 1.25f)
        textLocale = java.util.Locale.JAPANESE
        setPadding(dp(16), dp(14), dp(16), dp(6))
    }
    private val status = TextView(context).apply {
        textSize = 14f
        setTextColor(0xFF6B7280.toInt())
        setPadding(dp(16), dp(10), dp(16), dp(10))
    }
    private val japanese = JapaneseCard(context)
    private val english = EnglishCard(context).apply { visibility = GONE }
    private val divider = View(context).apply { setBackgroundColor(0xFFE5E7EB.toInt()); visibility = GONE }

    private var text = ""
    private var word: WordInfo? = null
    private var shown: LookupResult? = null // 当前显示的日语词或英文单词
    private var enRequest: AiRequest? = null

    init {
        orientation = VERTICAL
        background = GradientDrawable().apply {
            setColor(Color.WHITE)
            cornerRadius = 16 * density
        }
        elevation = 12 * density
        clipToOutline = true
        content.addView(sentence)
        content.addView(status)
        content.addView(japanese)
        content.addView(divider, LayoutParams(LayoutParams.MATCH_PARENT, dp(1)).apply { setMargins(dp(16), 0, dp(16), 0) })
        content.addView(english)
        scroll.addView(content)
        addView(scroll, LayoutParams(LayoutParams.MATCH_PARENT, 0, 1f))
        addView(buttons())

        japanese.onSpeak = { word?.let { app.speaker.say(it.surface, "ja") } }
        japanese.onAi = { word?.let { requestAi(it, manual = true) } }
        japanese.onWord = ::showEnglish
        english.onSpeak = { app.speaker.say(english.wordText, "en") }
        english.onAi = { requestEnglishAi() }
        sentence.setOnTouchListener(::onSentenceTouch)
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        val limited = MeasureSpec.makeMeasureSpec(minOf(MeasureSpec.getSize(heightMeasureSpec), maxHeight), MeasureSpec.AT_MOST)
        super.onMeasure(widthMeasureSpec, limited)
    }

    private fun buttons() = LinearLayout(context).apply {
        orientation = HORIZONTAL
        gravity = Gravity.END
        setPadding(dp(8), dp(2), dp(8), dp(6))
        fun button(label: String, action: () -> Unit) = Button(context, null, android.R.attr.borderlessButtonStyle).apply {
            text = label
            setTextColor(0xFF0078D4.toInt())
            setOnClickListener { action() }
        }
        addView(button("朗读全文") { app.speaker.say(this@CardPanel.text) })
        addView(button("设置") {
            openSettings()
        })
        addView(button("关闭") { onClose() })
    }

    /** 显示 text，并查 offset 处的词。offset 为 -1（选字菜单，或不知道点的是哪个字）时查第一个日语词，没有日语时查第一个英文单词。 */
    fun show(text: String, offset: Int) {
        this.text = text
        sentence.text = text
        status.visibility = GONE
        val firstEnglish = if (app.settings.lookupEnglish) firstEnglishWord(text) else -1
        if (!isJapanese(text) && firstEnglish < 0) {
            setStatus("这段文字里没有日语，可以点「朗读全文」。")
            return
        }
        val first = text.indexOfFirst { isKanji(it) || isKana(it) }.takeIf { it >= 0 } ?: firstEnglish
        lookupAt(if (offset < 0) first else offset)
    }

    fun setStatus(message: String) {
        status.text = message
        status.visibility = VISIBLE
    }

    private fun lookupAt(offset: Int) {
        app.lookup(text, offset) { found ->
            when (found) {
                null -> if (shown == null) setStatus("这里没有能查的词，点上面句子里的其他字试试。")
                is WordInfo -> showWord(found)
                is EnglishHit -> showEnglishHit(found)
            }
        }
    }

    private fun showWord(found: WordInfo) {
        status.visibility = GONE
        word = found
        shown = found
        enRequest = null
        highlight(found.spanStart, found.spanEnd)
        english.visibility = GONE
        divider.visibility = GONE
        japanese.word = found
        val initial = app.explainer.initial(jpRequest(found))
        japanese.ai = when {
            initial.state != null -> initial
            !app.explainer.hasKey() -> AiState(null, "在设置里填好 AI 密钥后可以结合上下文解释")
            else -> initial
        }
        if (initial.state == null && app.settings.aiAuto && app.explainer.hasKey()) requestAi(found, manual = false)
        scroll.post { scroll.smoothScrollTo(0, 0) }
    }

    /** 点到的是英文单词：不显示日语卡片，英文卡片结合原文句子解释（自动请求的规则和日语卡片一样）。 */
    private fun showEnglishHit(hit: EnglishHit) {
        status.visibility = GONE
        word = null
        shown = hit
        highlight(hit.spanStart, hit.spanEnd)
        japanese.word = null // 没有词时高度为 0
        divider.visibility = GONE
        val request = enContextRequest(hit)
        enRequest = request
        val initial = app.explainer.initial(request)
        english.show(hit.entry, hit.surface, when {
            initial.state != null -> initial
            !app.explainer.hasKey() -> AiState(null, "在设置里填好 AI 密钥后可以结合上下文解释")
            else -> initial
        }, inContext = true)
        english.visibility = VISIBLE
        if (initial.state == null && app.settings.aiAuto && app.explainer.hasKey()) requestEnglishAi(manual = false)
        scroll.post { scroll.smoothScrollTo(0, 0) }
    }

    private fun highlight(start: Int, end: Int) {
        val s = SpannableString(text)
        s.setSpan(BackgroundColorSpan(0xFFDBEAFE.toInt()), start, end, Spannable.SPAN_EXCLUSIVE_EXCLUSIVE)
        s.setSpan(ForegroundColorSpan(0xFF111827.toInt()), start, end, Spannable.SPAN_EXCLUSIVE_EXCLUSIVE)
        sentence.text = s
    }

    @Suppress("UNUSED_PARAMETER")
    private fun onSentenceTouch(v: View, e: MotionEvent): Boolean {
        if (e.action != MotionEvent.ACTION_UP) return true
        val layout = sentence.layout ?: return true
        val line = layout.getLineForVertical(e.y.toInt() - sentence.totalPaddingTop)
        val offset = layout.getOffsetForHorizontal(line, e.x - sentence.totalPaddingLeft)
        val char = minOf(offset, text.length - 1)
        if (char >= 0 && (isKana(text[char]) || isKanji(text[char]) ||
                (app.settings.lookupEnglish && englishWordAt(text, char) != null))) lookupAt(char)
        return true
    }

    private fun openSettings() {
        context.startActivity(Intent(context, SettingsActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        onClose()
    }

    private fun requestAi(w: WordInfo, manual: Boolean) {
        if (!app.explainer.hasKey()) {
            if (manual) openSettings()
            return
        }
        val request = jpRequest(w)
        val started = app.explainer.explain(request, manual) { state ->
            if (word === w) japanese.ai = state // 同时查了别的词时，旧结果不覆盖
        }
        japanese.ai = if (started) AiState("loading") else AiState(null, "自动解释太频繁，已暂停一会儿（点这里手动解释）")
    }

    private fun showEnglish(enWord: String, sense: Int) {
        val w = word ?: return
        val request = enRequest(enWord, w.senses[sense - 1].glosses.joinToString("; "), w)
        enRequest = request
        app.lookupEnglish(enWord) { entry ->
            if (enRequest !== request) return@lookupEnglish // 这期间换了别的词
            english.show(entry, enWord, if (app.explainer.hasKey()) app.explainer.initial(request) else AiState("off"))
            english.visibility = VISIBLE
            divider.visibility = VISIBLE
            scroll.post { scroll.smoothScrollTo(0, english.top) }
        }
    }

    /** 英文卡片的 AI 解释：从日语卡片点进来的只能手动请求；点到的英文单词（inContext）和日语卡片一样可以自动请求。 */
    private fun requestEnglishAi(manual: Boolean = true) {
        val request = enRequest ?: return
        if (!app.explainer.hasKey()) {
            if (manual) openSettings()
            return
        }
        val started = app.explainer.explain(request, manual) { state -> if (enRequest === request) english.ai = state }
        english.ai = if (started) AiState("loading") else AiState(null, "自动解释太频繁，已暂停一会儿（点这里手动解释）")
    }
}
