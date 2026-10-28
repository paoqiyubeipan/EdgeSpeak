package app.edgespeak

import android.content.Context
import android.speech.tts.TextToSpeech
import android.util.Log
import java.util.Locale

/** 朗读：用系统自带的朗读引擎（Google 文字转语音支持日语、中文、英语）。
 *  语言判断和电脑版一样：有假名 → 日语；只有汉字 → 日语（出现中文特有的字则中文）；其余 → 英语。 */
class Speaker(context: Context) {
    private var ready = false
    private var pending: Pair<String, String?>? = null
    private val tts: TextToSpeech = TextToSpeech(context.applicationContext) { status ->
        ready = status == TextToSpeech.SUCCESS
        if (!ready) Log.w("EdgeSpeak", "朗读引擎初始化失败：$status")
        pending?.let { (text, lang) -> pending = null; say(text, lang) }
    }

    /** lang：ja / zh / en，null 表示自动判断。 */
    fun say(text: String, lang: String? = null) {
        val clean = text.trim()
        if (clean.isEmpty()) return
        if (!ready) {
            pending = clean to lang
            return
        }
        val locale = when (lang ?: detectLanguage(clean)) {
            "ja" -> Locale.JAPANESE
            "zh" -> Locale.SIMPLIFIED_CHINESE
            else -> english()
        }
        if (tts.setLanguage(locale) < TextToSpeech.LANG_AVAILABLE) Log.w("EdgeSpeak", "朗读引擎没有 $locale 的语音")
        tts.speak(clean, TextToSpeech.QUEUE_FLUSH, null, "edgespeak")
    }

    fun stop() = tts.stop()

    fun hasVoice(lang: String): Boolean = ready && tts.isLanguageAvailable(
        if (lang == "ja") Locale.JAPANESE else if (lang == "zh") Locale.SIMPLIFIED_CHINESE else english()) >= TextToSpeech.LANG_AVAILABLE

    /** 英文以英式为准：引擎没有英式语音时用美式。 */
    private fun english(): Locale =
        if (tts.isLanguageAvailable(Locale.UK) >= TextToSpeech.LANG_AVAILABLE) Locale.UK else Locale.US

    companion object {
        // 中文特有、日语里不用的字：出现就按中文读
        private const val CHINESE_ONLY = "这们说吗是我很么没还为对时会个也就过来着让给些那怎哪里"

        fun detectLanguage(text: String): String = when {
            text.any(::isKana) -> "ja"
            text.any(::isKanji) -> if (text.any { it in CHINESE_ONLY }) "zh" else "ja"
            else -> "en"
        }
    }
}
