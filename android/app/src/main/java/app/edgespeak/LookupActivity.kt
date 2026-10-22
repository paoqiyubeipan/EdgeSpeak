package app.edgespeak

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.view.WindowInsets
import android.widget.FrameLayout

/** 选中文字 → 菜单里的「EdgeSpeak 查词」：在原来的 App 上面弹出查词面板，点面板外面关闭。 */
class LookupActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val text = intent.getCharSequenceExtra(Intent.EXTRA_PROCESS_TEXT)?.toString()?.trim().orEmpty()
        val density = resources.displayMetrics.density
        val margin = (8 * density).toInt()
        val panel = CardPanel(this, (resources.displayMetrics.heightPixels * 0.85).toInt()) { finish() }
        panel.isClickable = true // 点面板本身不关闭
        val root = FrameLayout(this).apply {
            setOnClickListener { finish() }
            addView(panel, FrameLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT, Gravity.BOTTOM).apply {
                setMargins(margin, margin, margin, margin)
            })
            // 面板放在屏幕底部，避开导航栏
            setOnApplyWindowInsetsListener { v, insets ->
                val bars = insets.getInsets(WindowInsets.Type.systemBars())
                v.setPadding(0, bars.top, 0, bars.bottom)
                insets
            }
        }
        setContentView(root)

        val app = App.instance
        if (text.isEmpty()) {
            panel.setStatus("没有收到选中的文字。")
            return
        }
        if (DictData.ready(this)) {
            panel.show(text, 0)
        } else {
            panel.setStatus("第一次使用，正在解压词典…")
            app.prepare({ percent -> panel.setStatus("第一次使用，正在解压词典… $percent%") }) { error ->
                if (error != null) panel.setStatus("打开词典失败：${error.message}") else panel.show(text, 0)
            }
        }
    }
}
