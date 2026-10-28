package app.edgespeak

import android.app.Activity
import android.content.ComponentName
import android.content.Intent
import android.graphics.Typeface
import android.net.Uri
import android.os.Bundle
import android.provider.Settings.ACTION_ACCESSIBILITY_SETTINGS
import android.provider.Settings.Secure
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.view.WindowInsets
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.AutoCompleteTextView
import android.widget.Button
import android.widget.CheckBox
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Spinner
import android.widget.Switch
import android.widget.TextView
import kotlin.concurrent.thread

/** 设置页（也是启动页）：用法说明（和英文取词开关）、悬浮取词、AI 服务和密钥、备用模型、朗读语音、词典。 */
class SettingsActivity : Activity() {
    private val app get() = App.instance
    private val settings get() = app.settings
    private val density get() = resources.displayMetrics.density
    private fun dp(v: Int) = (v * density).toInt()

    private lateinit var accessibilityStatus: TextView
    private lateinit var voiceStatus: TextView
    private lateinit var dictStatus: TextView
    private lateinit var providerSpinner: Spinner
    private lateinit var keyEdit: EditText
    private lateinit var keyLink: TextView
    private lateinit var providerHint: TextView
    private lateinit var modelEdit: AutoCompleteTextView
    private lateinit var modelHint: TextView
    private lateinit var aiStatus: TextView
    private lateinit var fallbackList: LinearLayout

    // 每个服务各自的密钥和模型：切换服务时换着显示，离开页面时一起保存
    private val keys = mutableMapOf<String, String>()
    private val models = mutableMapOf<String, String>()
    private lateinit var current: Provider
    private val fallbacks = mutableListOf<Pair<String, String>>()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        for (p in PROVIDERS) {
            keys[p.name] = settings.apiKey(p.name)
            models[p.name] = settings.model(p.name)
        }
        current = provider(settings.aiProvider)
        fallbacks += settings.fallbacks

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(20), dp(16), dp(20), dp(32))
        }
        val scroll = ScrollView(this).apply {
            addView(root)
            setOnApplyWindowInsetsListener { v, insets ->
                val bars = insets.getInsets(WindowInsets.Type.systemBars())
                v.setPadding(0, bars.top, 0, bars.bottom)
                insets
            }
        }
        setContentView(scroll)

        root.addView(text("EdgeSpeak", 24f, bold = true))
        root.addView(hint("日语查词（英文单词也能查）+ 朗读。词典都在手机里，不联网也能查；只有 AI 解释需要联网。"))

        heading(root, "怎么用")
        root.addView(text("① 选字菜单：在任何 App 里长按选中文字，弹出的菜单里点「EdgeSpeak 查词」或「EdgeSpeak 朗读」" +
            "（有的 App 要先点菜单里的 ⋮ 才能看到）。\n② 悬浮取词：打开下面的辅助功能后，屏幕边上出现蓝色悬浮按钮；" +
            "点它，再点屏幕上的日语。游戏、图片、漫画里的字也能识别。\n③ 卡片上：点标题朗读，点英文单词查它的中文意思，" +
            "点上面句子里的其他字换词查。\n④ 选中或点到的是英文单词时，显示英汉词典的释义，AI 结合句子解释它的意思。", 15f))
        root.addView(Switch(this).apply {
            text = "英文单词也查"
            textSize = 15f
            isChecked = settings.lookupEnglish
            setOnCheckedChangeListener { _, checked -> settings.lookupEnglish = checked }
        })

        heading(root, "悬浮取词")
        accessibilityStatus = hint("")
        root.addView(accessibilityStatus)
        root.addView(button("打开辅助功能设置") { startActivity(Intent(ACTION_ACCESSIBILITY_SETTINGS)) })
        root.addView(hint("在列表里找到「EdgeSpeak 悬浮取词」并打开。它只用来读取你点到的文字；读不到时截屏识别。" +
            "文字只在手机里处理，只有开了 AI 解释时才会把那一句发给你选的 AI 服务。"))
        root.addView(Switch(this).apply {
            text = "显示悬浮按钮"
            textSize = 15f
            isChecked = settings.bubble
            setOnCheckedChangeListener { _, checked ->
                settings.bubble = checked
                ScreenLookupService.instance?.refreshBubble()
            }
        })

        heading(root, "AI 解释（结合上下文的中文释义）")
        providerSpinner = Spinner(this).apply {
            adapter = ArrayAdapter(this@SettingsActivity, android.R.layout.simple_spinner_dropdown_item, PROVIDERS.map { it.label })
            setSelection(PROVIDERS.indexOf(current))
            onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
                override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) = switchTo(PROVIDERS[position])
                override fun onNothingSelected(parent: AdapterView<*>?) {}
            }
        }
        root.addView(row(text("服务", 15f), providerSpinner))
        providerHint = hint("")
        root.addView(providerHint)
        keyEdit = EditText(this).apply {
            hint = "API 密钥"
            isSingleLine = true // 要在设密码类型之前：设单行会把遮挡密码的效果重置掉
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
        }
        root.addView(keyEdit)
        root.addView(CheckBox(this).apply {
            text = "显示密钥"
            setOnCheckedChangeListener { _, show ->
                keyEdit.inputType = InputType.TYPE_CLASS_TEXT or
                    if (show) InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD else InputType.TYPE_TEXT_VARIATION_PASSWORD
                keyEdit.setSelection(keyEdit.text.length)
            }
        })
        keyLink = text("", 14f).apply {
            setTextColor(0xFF0078D4.toInt())
            setOnClickListener { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(current.keyUrl))) }
        }
        root.addView(keyLink)
        modelEdit = AutoCompleteTextView(this).apply {
            hint = "模型（留空用默认）"
            isSingleLine = true
            threshold = 0
            setOnClickListener { if (adapter != null) showDropDown() }
        }
        root.addView(row(modelEdit, button("刷新列表") { refreshModels() }), LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        modelHint = hint("")
        root.addView(modelHint)
        root.addView(CheckBox(this).apply {
            text = "打开卡片时自动解释（每次算 1 次请求）"
            isChecked = settings.aiAuto
            setOnCheckedChangeListener { _, checked -> settings.aiAuto = checked }
        })
        aiStatus = hint("")
        root.addView(row(button("测试连接") { testConnection() }, aiStatus))

        heading(root, "备用模型")
        root.addView(hint("主模型限速、额度用完或出错时，按顺序换下一个。各服务的密钥在上面切换到该服务后填写，没填密钥的会被跳过。"))
        val fbProvider = Spinner(this).apply {
            adapter = ArrayAdapter(this@SettingsActivity, android.R.layout.simple_spinner_dropdown_item, PROVIDERS.map { it.label })
        }
        val fbModel = EditText(this).apply { hint = "模型（留空用默认）"; isSingleLine = true }
        root.addView(row(fbProvider, fbModel))
        root.addView(button("添加到备用列表") {
            val entry = PROVIDERS[fbProvider.selectedItemPosition].name to fbModel.text.toString().trim()
            if (entry !in fallbacks) fallbacks += entry
            fbModel.setText("")
            showFallbacks()
        })
        fallbackList = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(fallbackList)

        heading(root, "朗读")
        voiceStatus = hint("")
        root.addView(voiceStatus)
        root.addView(button("打开系统朗读设置") {
            runCatching { startActivity(Intent("com.android.settings.TTS_SETTINGS")) }
        })

        heading(root, "词典")
        dictStatus = hint("")
        root.addView(dictStatus)
        root.addView(hint("JMdict（EDRDG，CC BY-SA 4.0）、Kanjium 音高（CC BY-SA 4.0）、维基词典（CC BY-SA 4.0）、" +
            "ECDICT（MIT）、Sudachi 分词词典（Apache 2.0）、ML Kit 文字识别（Google）。"))

        show(current)
        showFallbacks()
    }

    override fun onResume() {
        super.onResume()
        val enabled = Secure.getString(contentResolver, Secure.ENABLED_ACCESSIBILITY_SERVICES).orEmpty()
            .contains(ComponentName(this, ScreenLookupService::class.java).flattenToString())
        accessibilityStatus.text = if (enabled) "✓ 已开启。屏幕边上的蓝色按钮可以拖到别的位置。" else "还没开启。"
        voiceStatus.text = "正在检查朗读语音…"
        app.speaker.say(" ") // 让朗读引擎先初始化
        voiceStatus.postDelayed({
            voiceStatus.text = if (app.speaker.hasVoice("ja")) "✓ 系统朗读引擎有日语语音。"
            else "系统朗读引擎里没有日语语音：打开系统朗读设置，引擎选「Google 语音服务」并下载日语语音。"
        }, 1500)
        if (DictData.ready(this)) dictStatus.text = "✓ 词典已就绪。"
        else {
            dictStatus.text = "第一次使用要先解压词典（约 370 MB）…"
            app.prepare({ dictStatus.text = "正在解压词典… $it%" }) { error ->
                dictStatus.text = if (error == null) "✓ 词典已就绪。" else "解压词典失败：${error.message}（手机空间不够？）"
            }
        }
    }

    override fun onPause() {
        super.onPause()
        keys[current.name] = keyEdit.text.toString().trim()
        models[current.name] = modelEdit.text.toString().trim()
        for (p in PROVIDERS) {
            settings.setApiKey(p.name, keys[p.name].orEmpty())
            settings.setModel(p.name, models[p.name].orEmpty())
        }
        settings.aiProvider = current.name
        settings.fallbacks = fallbacks.toList()
    }

    private fun switchTo(p: Provider) {
        if (p === current) return
        keys[current.name] = keyEdit.text.toString().trim()
        models[current.name] = modelEdit.text.toString().trim()
        show(p)
        showFallbacks()
    }

    private fun show(p: Provider) {
        current = p
        providerHint.text = p.hint
        keyEdit.setText(keys[p.name])
        keyLink.text = "还没有密钥？到 ${p.label} 网站获取 ↗"
        modelEdit.setText(models[p.name])
        modelEdit.setAdapter(null)
        modelHint.text = p.defaultHint
        aiStatus.text = ""
    }

    private fun showFallbacks() {
        fallbackList.removeAllViews()
        if (fallbacks.isEmpty()) fallbackList.addView(hint("（还没有备用模型）"))
        fallbacks.forEachIndexed { k, (name, model) ->
            val p = provider(name)
            val key = if (p === current) keyEdit.text.toString().trim() else keys[name].orEmpty()
            val label = text("${k + 1}. ${p.label} · ${model.ifEmpty { "默认模型" }}${if (key.isEmpty()) "（没填密钥，会跳过）" else ""}", 14f)
            fun small(label: String, action: () -> Unit) = Button(this, null, android.R.attr.borderlessButtonStyle).apply {
                text = label
                minWidth = 0
                minimumWidth = 0
                setPadding(dp(8), 0, dp(8), 0)
                setOnClickListener { action(); showFallbacks() }
            }
            fallbackList.addView(row(label,
                small("上移") { if (k > 0) fallbacks.add(k - 1, fallbacks.removeAt(k)) },
                small("下移") { if (k < fallbacks.size - 1) fallbacks.add(k + 1, fallbacks.removeAt(k)) },
                small("删除") { fallbacks.removeAt(k) }))
        }
    }

    private fun refreshModels() {
        val p = current
        val key = keyEdit.text.toString().trim()
        if (key.isEmpty() && p !== OpenRouter) { // OpenRouter 的模型列表不需要密钥
            aiStatus.text = "请先填 API 密钥。"
            return
        }
        aiStatus.text = "正在获取模型列表…"
        thread {
            val result = runCatching { p.listModels(key) }
            runOnUiThread {
                if (p !== current) return@runOnUiThread
                result.onSuccess { list ->
                    modelEdit.setAdapter(ArrayAdapter(this, android.R.layout.simple_dropdown_item_1line, list))
                    aiStatus.text = "找到 ${list.size} 个${if (p === OpenRouter) "免费模型" else "可用的聊天模型"}，点模型框选择。"
                    modelEdit.showDropDown()
                }.onFailure { aiStatus.text = friendlyError(it as Exception, "（默认模型）", p) }
            }
        }
    }

    private fun testConnection() {
        val p = current
        val key = keyEdit.text.toString().trim()
        val model = modelEdit.text.toString().trim()
        if (key.isEmpty()) {
            aiStatus.text = "请先填 API 密钥。"
            return
        }
        aiStatus.text = "正在测试…"
        thread {
            val (ok, message) = try {
                val chosen = model.ifEmpty { p.defaultModel(key) ?: "" }
                if (chosen.isEmpty()) false to "没有可用的默认模型，请先选一个。"
                else p.test(key, chosen).let { (ok, m) -> ok to if (model.isEmpty()) "$m（默认模型：$chosen）" else m }
            } catch (e: Exception) {
                false to friendlyError(e, model, p)
            }
            runOnUiThread { aiStatus.text = (if (ok) "✓ " else "✗ ") + message }
        }
    }

    // ---- 简单的界面部件

    private fun text(s: String, size: Float, bold: Boolean = false) = TextView(this).apply {
        text = s
        textSize = size
        setTextColor(0xFF111827.toInt())
        setLineSpacing(0f, 1.2f)
        if (bold) typeface = Typeface.DEFAULT_BOLD
    }

    private fun hint(s: String) = text(s, 13.5f).apply {
        setTextColor(0xFF6B7280.toInt())
        setPadding(0, dp(2), 0, dp(6))
    }

    private fun heading(root: LinearLayout, s: String) {
        root.addView(text(s, 17f, bold = true).apply { setPadding(0, dp(22), 0, dp(6)) })
    }

    private fun button(label: String, action: () -> Unit) = Button(this).apply {
        text = label
        isAllCaps = false
        setOnClickListener { action() }
    }

    private fun row(vararg views: View) = LinearLayout(this).apply {
        orientation = LinearLayout.HORIZONTAL
        gravity = Gravity.CENTER_VERTICAL
        views.forEachIndexed { k, v ->
            val grow = v is EditText || v is Spinner || (v is TextView && v !is Button && k == views.size - 1) ||
                (k == 0 && v is TextView && v !is Button && views.size > 2)
            addView(v, LinearLayout.LayoutParams(if (grow) 0 else WRAP_CONTENT, WRAP_CONTENT, if (grow) 1f else 0f)
                .apply { marginEnd = dp(8) })
        }
    }
}
