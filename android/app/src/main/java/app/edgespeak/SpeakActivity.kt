package app.edgespeak

import android.app.Activity
import android.content.Intent
import android.os.Bundle

/** 选中文字 → 菜单里的「EdgeSpeak 朗读」：直接读出来，不显示界面（和电脑版的 Ctrl+Alt+Q 一样）。 */
class SpeakActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        intent.getCharSequenceExtra(Intent.EXTRA_PROCESS_TEXT)?.toString()?.let { App.instance.speaker.say(it) }
        finish() // 无界面主题要求在 onResume 之前结束
    }
}
