package app.edgespeak

import android.accessibilityservice.AccessibilityService
import android.annotation.SuppressLint
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.Rect
import android.graphics.RectF
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import android.view.Display
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import android.widget.FrameLayout
import android.widget.TextView
import android.widget.Toast
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.japanese.JapaneseTextRecognizerOptions
import kotlin.math.abs
import kotlin.math.hypot

/** 悬浮取词：一个可拖动的悬浮按钮。点它进入取词模式，再点屏幕上的日语（或英文单词），旁边弹出查词面板。
 *  取字先用辅助功能读界面文字（能精确到点中的那个字）；读不到时（游戏、图片、漫画）截屏用 ML Kit 日语 OCR
 *  （日语模型也认拉丁字母，英文单词之间按位置补空格）。
 *  所有悬浮窗都是辅助功能悬浮窗，不需要“显示在其他应用上层”权限。 */
class ScreenLookupService : AccessibilityService() {
    private val app get() = App.instance
    private val main = Handler(Looper.getMainLooper())
    private lateinit var wm: WindowManager
    private val density get() = resources.displayMetrics.density
    private fun dp(v: Int) = (v * density).toInt()

    private var bubble: View? = null
    private var picker: View? = null
    private var panelHost: FrameLayout? = null
    private val recognizer by lazy { TextRecognition.getClient(JapaneseTextRecognizerOptions.Builder().build()) }

    /** 取到的文字和点中的字在其中的位置。 */
    private data class Hit(val text: String, val offset: Int)

    override fun onServiceConnected() {
        instance = this
        wm = getSystemService(WINDOW_SERVICE) as WindowManager
        refreshBubble()
        app.prepare({}) {} // 提前解压、打开词典，第一次取词时不用等
    }

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        removeAll()
        instance = null
        return super.onUnbind(intent)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}
    override fun onInterrupt() {}

    private fun removeAll() {
        listOf(bubble, picker, panelHost).forEach { v -> v?.let { runCatching { wm.removeView(it) } } }
        bubble = null
        picker = null
        panelHost = null
    }

    private fun overlayParams(width: Int, height: Int, flags: Int) = WindowManager.LayoutParams(
        width, height, WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY, flags, PixelFormat.TRANSLUCENT,
    ).apply { gravity = Gravity.TOP or Gravity.START }

    /** 按设置显示或隐藏悬浮按钮（设置页里切换开关时也会调用）。 */
    fun refreshBubble() {
        if (app.settings.bubble && bubble == null) showBubble()
        if (!app.settings.bubble) bubble?.let { wm.removeView(it); bubble = null }
    }

    @SuppressLint("ClickableViewAccessibility")
    private fun showBubble() {
        val size = dp(52)
        val metrics = resources.displayMetrics
        val params = overlayParams(size, size, WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
            WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS).apply {
            x = app.settings.bubbleX.takeIf { it >= 0 } ?: (metrics.widthPixels - size - dp(8))
            y = app.settings.bubbleY.takeIf { it >= 0 } ?: (metrics.heightPixels * 2 / 5)
        }
        val view = BubbleView(this)
        var startX = 0f
        var startY = 0f
        var originX = 0
        var originY = 0
        var moved = false
        view.setOnTouchListener { _, e ->
            when (e.action) {
                MotionEvent.ACTION_DOWN -> {
                    startX = e.rawX; startY = e.rawY; originX = params.x; originY = params.y; moved = false
                }
                MotionEvent.ACTION_MOVE -> {
                    if (abs(e.rawX - startX) + abs(e.rawY - startY) > dp(6)) moved = true
                    if (moved) {
                        params.x = (originX + e.rawX - startX).toInt()
                        params.y = (originY + e.rawY - startY).toInt()
                        wm.updateViewLayout(view, params)
                    }
                }
                MotionEvent.ACTION_UP -> if (moved) {
                    app.settings.bubbleX = params.x
                    app.settings.bubbleY = params.y
                } else if (picker != null) stopPicking() else startPicking()
            }
            true
        }
        wm.addView(view, params)
        bubble = view
    }

    /** 取词模式：盖一层几乎透明的全屏窗口，接住下一次点击。 */
    @SuppressLint("ClickableViewAccessibility")
    private fun startPicking() {
        closePanel()
        val hint = TextView(this).apply {
            text = if (app.settings.lookupEnglish) "点要查的日语或英文单词（再点悬浮按钮取消）" else "点要查的日语（再点悬浮按钮取消）"
            setTextColor(Color.WHITE)
            textSize = 14f
            setBackgroundColor(0xCC0078D4.toInt())
            setPadding(dp(14), dp(8), dp(14), dp(8))
        }
        val layer = FrameLayout(this).apply {
            setBackgroundColor(0x220078D4)
            addView(hint, FrameLayout.LayoutParams(FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.TOP or Gravity.CENTER_HORIZONTAL).apply { topMargin = dp(48) })
            setOnTouchListener { _, e ->
                if (e.action == MotionEvent.ACTION_UP) {
                    val x = e.rawX.toInt()
                    val y = e.rawY.toInt()
                    stopPicking()
                    main.postDelayed({ lookupAt(x, y) }, 120) // 等取词层真正消失，截图里才不会有它
                }
                true
            }
        }
        wm.addView(layer, overlayParams(WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.MATCH_PARENT,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN))
        picker = layer
        raiseBubble() // 留在最上层，可以再点它取消
    }

    /** 新加的悬浮窗会盖住悬浮按钮：把按钮重新放到最上面。 */
    private fun raiseBubble() {
        bubble?.let { wm.removeView(it); wm.addView(it, it.layoutParams) }
    }

    private fun stopPicking() {
        picker?.let { wm.removeView(it) }
        picker = null
    }

    private fun lookupAt(x: Int, y: Int) {
        val hit = runCatching { textFromNodes(x, y) }.onFailure { Log.w(App.TAG, "读取界面文字失败", it) }.getOrNull()
        if (hit != null && hit.offset >= 0) {
            Log.i(App.TAG, "取词（界面文字）@${hit.offset}")
            showPanel(hit, y)
            return
        }
        ocrAt(x, y) { ocr ->
            when {
                // 读到了界面文字但不知道点的是哪个字：用 OCR 认出点中的那几个字，到原文里找位置（原文没有认错的字）
                hit != null && ocr != null -> showPanel(locate(hit.text, ocr) ?: ocr, y)
                ocr != null -> showPanel(ocr, y)
                hit != null -> showPanel(Hit(hit.text, -1), y) // 从头查，可以在面板里点别的字
                else -> Toast.makeText(this, "这里没有找到文字", Toast.LENGTH_SHORT).show()
            }
        }
    }

    /** 在原文里找 OCR 点中位置附近的一小段字，找到唯一一处就换算成原文里的位置。 */
    private fun locate(text: String, ocr: Hit): Hit? {
        for (radius in listOf(6, 4, 3, 2, 1)) {
            val start = (ocr.offset - radius).coerceAtLeast(0)
            val end = (ocr.offset + radius + 1).coerceAtMost(ocr.text.length)
            if (end - start < 2) continue
            val piece = ocr.text.substring(start, end)
            val found = text.indexOf(piece)
            if (found >= 0 && text.indexOf(piece, found + 1) < 0) {
                Log.i(App.TAG, "OCR 定位到原文第 $found 个字")
                return Hit(text, found + ocr.offset - start)
            }
        }
        return null
    }

    /** 从辅助功能节点树里找点中的文字。offset 为 -1 表示找到了文字但不知道点的是哪个字。 */
    private fun textFromNodes(x: Int, y: Int): Hit? {
        val english = app.settings.lookupEnglish
        var best: AccessibilityNodeInfo? = null
        var bestArea = Long.MAX_VALUE
        val bounds = Rect()
        fun visit(node: AccessibilityNodeInfo, depth: Int) {
            if (depth > 80) return
            node.getBoundsInScreen(bounds)
            if (!bounds.contains(x, y)) return
            val text = node.text
            if (!text.isNullOrBlank() && (isJapanese(text.toString()) || english && hasLatin(text))) {
                val area = bounds.width().toLong() * bounds.height()
                if (area <= bestArea) {
                    best = node
                    bestArea = area
                }
            }
            for (k in 0 until node.childCount) node.getChild(k)?.let { visit(it, depth + 1) }
        }
        for (window in windows.sortedByDescending { it.layer }) {
            if (window.type == AccessibilityWindowInfo.TYPE_ACCESSIBILITY_OVERLAY) continue // 自己的悬浮窗
            window.root?.let { visit(it, 0) }
            if (best != null) break
        }
        val node = best ?: return null
        val text = node.text.toString()
        return clip(text, charAt(node, text, x, y))
    }

    /** 点中的是第几个字：问应用要每个字的位置（浏览器、大部分文本控件都支持）。拿不到时返回 -1。 */
    @Suppress("DEPRECATION")
    private fun charAt(node: AccessibilityNodeInfo, text: String, x: Int, y: Int): Int {
        // 系统控件用 android.view…，Chrome 等用 AndroidX 的 android.core.view… 写法，参数名也跟着不同
        val key = node.availableExtraData.firstOrNull { it.endsWith("DATA_TEXT_CHARACTER_LOCATION_KEY") } ?: return -1
        val prefix = key.removeSuffix("KEY")
        val length = minOf(text.length, 3000)
        val args = Bundle().apply {
            putInt(prefix + "ARG_START_INDEX", 0)
            putInt(prefix + "ARG_LENGTH", length)
        }
        if (!node.refreshWithExtraData(key, args)) return -1
        val rects = node.extras.getParcelableArray(key) ?: return -1
        // 有的应用（Chrome）每个字都返回整段的范围，等于没给
        if (rects.size > 1 && rects.filterNotNull().distinct().size == 1) return -1
        var nearest = -1
        var distance = Float.MAX_VALUE
        rects.forEachIndexed { i, r ->
            val rect = r as? RectF ?: return@forEachIndexed
            if (rect.contains(x.toFloat(), y.toFloat())) return i
            val d = hypot(rect.centerX() - x, rect.centerY() - y)
            if (d < distance) {
                distance = d
                nearest = i
            }
        }
        return if (distance < 40 * density) nearest else -1
    }

    /** 太长的文字只留点中位置前后一段（面板里显示的和发给 AI 的上下文都是这一段）。 */
    private fun clip(text: String, offset: Int): Hit {
        if (text.length <= 400) return Hit(text, offset)
        val at = offset.coerceAtLeast(0)
        var start = maxOf(0, at - 180)
        var end = minOf(text.length, at + 180)
        text.lastIndexOfAny(charArrayOf('。', '\n', '！', '？'), at - 1).takeIf { it in start until at }?.let { start = it + 1 }
        text.indexOfAny(charArrayOf('。', '\n', '！', '？'), at).takeIf { it in at until end }?.let { end = it + 1 }
        return Hit(text.substring(start, end), if (offset < 0) -1 else offset - start)
    }

    /** 截屏，识别点击位置附近的日语。 */
    private fun ocrAt(x: Int, y: Int, callback: (Hit?) -> Unit) {
        takeScreenshot(Display.DEFAULT_DISPLAY, mainExecutor, object : TakeScreenshotCallback {
            override fun onSuccess(result: ScreenshotResult) {
                val buffer = result.hardwareBuffer
                val full = Bitmap.wrapHardwareBuffer(buffer, result.colorSpace)?.copy(Bitmap.Config.ARGB_8888, false)
                buffer.close()
                if (full == null) return callback(null)
                // 只识别点击位置上下一段，快一些
                val top = (y - dp(160)).coerceAtLeast(0)
                val bottom = (y + dp(160)).coerceAtMost(full.height)
                val band = Bitmap.createBitmap(full, 0, top, full.width, bottom - top)
                recognizer.process(InputImage.fromBitmap(band, 0))
                    .addOnSuccessListener { text -> callback(ocrHit(text, x, y - top)) }
                    .addOnFailureListener { Log.w(App.TAG, "OCR 失败", it); callback(null) }
            }

            override fun onFailure(errorCode: Int) {
                Log.w(App.TAG, "截屏失败：$errorCode")
                callback(null)
            }
        })
    }

    /** 在识别结果里找点击位置所在的那一行，算出点中的是第几个字。同一块里的其他行一起作为上下文。 */
    private fun ocrHit(result: com.google.mlkit.vision.text.Text, x: Int, y: Int): Hit? {
        for (block in result.textBlocks) {
            val lines = block.lines // 每次取都会生成新对象，取一次按下标用
            for ((li, line) in lines.withIndex()) {
            val box = line.boundingBox ?: continue
            if (!box.contains(x, y) && !(y in box.top..box.bottom && x in box.left - dp(16)..box.right + dp(16))) continue
            // 日语元素之间不加空格，英文单词之间补上
            val pieces = mutableListOf<Pair<String, Rect?>>()
            var previous = ""
            for (element in line.elements) {
                if (needsSpace(previous, element.text)) pieces += " " to null
                for (symbol in element.symbols) pieces += symbol.text to symbol.boundingBox
                previous = element.text
            }
            val lineText = pieces.joinToString("") { it.first }
            var index = 0
            var offset = -1
            var nearest = Float.MAX_VALUE
            for ((t, r) in pieces) {
                if (r != null) {
                    val d = if (r.contains(x, y)) 0f else abs(r.exactCenterX() - x)
                    if (d < nearest) {
                        nearest = d
                        offset = index
                    }
                }
                index += t.length
            }
            val before = lines.subList(0, li).fold("") { acc, l -> joinLines(acc, ocrLineText(l)) }
            val after = lines.subList(li + 1, lines.size).fold("") { acc, l -> joinLines(acc, ocrLineText(l)) }
            val head = if (needsSpace(before, lineText)) "$before " else before
            Log.i(App.TAG, "取词（OCR）@$offset")
            return clip(joinLines(head + lineText, after), if (offset < 0) -1 else head.length + offset)
            }
        }
        return null
    }

    /** OCR 的一行：元素按顺序拼起来，英文单词之间补空格。 */
    private fun ocrLineText(line: com.google.mlkit.vision.text.Text.Line): String {
        val text = StringBuilder()
        for (element in line.elements) {
            val piece = element.symbols.joinToString("") { it.text }
            if (needsSpace(text.toString(), piece)) text.append(' ')
            text.append(piece)
        }
        return text.toString()
    }

    /** 两行接起来：英文折行处是单词间的空格，日语折行处直接相连。 */
    private fun joinLines(a: String, b: String) = if (needsSpace(a, b)) "$a $b" else a + b

    /** 前一段以英文（字母、数字、标点）结尾、后一段以英文字母或数字开头时，中间要有空格。 */
    private fun needsSpace(before: String, after: String): Boolean {
        val a = before.lastOrNull() ?: return false
        val b = after.firstOrNull() ?: return false
        return a.code in 0x21..0x7E && (b in 'a'..'z' || b in 'A'..'Z' || b in '0'..'9')
    }

    /** 有没有英文字母（全角的也算）：节点里只有英文时，英文取词关着就不要它。 */
    private fun hasLatin(text: CharSequence) = text.any { it in 'a'..'z' || it in 'A'..'Z' || it in 'ａ'..'ｚ' || it in 'Ａ'..'Ｚ' }

    @SuppressLint("ClickableViewAccessibility")
    private fun showPanel(hit: Hit, tapY: Int) {
        closePanel()
        val metrics = resources.displayMetrics
        val host = FrameLayout(this)
        val panel = CardPanel(this, (metrics.heightPixels * 0.55).toInt()) { closePanel() }
        // 点在屏幕上半部分时面板放在下面，反之放在上面，不挡住点的那个词
        val gravity = if (tapY < metrics.heightPixels / 2) Gravity.BOTTOM else Gravity.TOP
        host.addView(panel, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.WRAP_CONTENT, gravity)
            .apply { setMargins(dp(8), dp(40), dp(8), dp(40)) })
        host.setOnTouchListener { _, e ->
            if (e.action == MotionEvent.ACTION_OUTSIDE) closePanel()
            false
        }
        val params = overlayParams(WindowManager.LayoutParams.MATCH_PARENT, WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH).apply { this.gravity = gravity }
        wm.addView(host, params)
        panelHost = host
        raiseBubble()
        if (DictData.ready(this)) panel.show(hit.text, hit.offset)
        else {
            panel.setStatus("第一次使用，正在解压词典…")
            app.prepare({ panel.setStatus("第一次使用，正在解压词典… $it%") }) { panel.show(hit.text, hit.offset) }
        }
    }

    private fun closePanel() {
        panelHost?.let { runCatching { wm.removeView(it) } }
        panelHost = null
    }

    /** 悬浮按钮：蓝色圆形 + 白色喇叭，和应用图标一样。 */
    private class BubbleView(context: android.content.Context) : View(context) {
        private val fill = Paint(Paint.ANTI_ALIAS_FLAG)
        override fun onDraw(canvas: Canvas) {
            val r = width / 2f
            fill.color = 0xEE0078D4.toInt()
            fill.style = Paint.Style.FILL
            canvas.drawCircle(r, r, r, fill)
            val s = width / 108f
            fill.color = Color.WHITE
            canvas.drawPath(android.graphics.Path().apply {
                moveTo(30 * s, 44 * s); lineTo(42 * s, 44 * s); lineTo(58 * s, 30 * s); lineTo(58 * s, 78 * s)
                lineTo(42 * s, 64 * s); lineTo(30 * s, 64 * s); close()
            }, fill)
            fill.style = Paint.Style.STROKE
            fill.strokeWidth = 5 * s
            fill.strokeCap = Paint.Cap.ROUND
            canvas.drawArc(RectF(52 * s, 40 * s, 80 * s, 68 * s), -60f, 120f, false, fill)
        }
    }

    companion object {
        /** 正在运行的服务（设置页用来判断辅助功能有没有开、切换悬浮按钮）。 */
        var instance: ScreenLookupService? = null
            private set
    }
}
