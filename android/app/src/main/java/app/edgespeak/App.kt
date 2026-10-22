package app.edgespeak

import android.app.Application
import android.os.Handler
import android.os.Looper
import android.util.Log
import java.util.concurrent.Executors

/** 全局共用的设置、词典、AI 和朗读。词典只在 lookupThread 里打开和使用（分词器不是线程安全的）。 */
class App : Application() {
    lateinit var settings: Settings
        private set
    val explainer by lazy { Explainer(settings) }
    val speaker by lazy { Speaker(this) }

    private val lookupThread = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())
    private var japanese: JapaneseDictionary? = null
    private var english: EnglishDictionary? = null

    override fun onCreate() {
        super.onCreate()
        instance = this
        settings = Settings(this)
    }

    /** 第一次用时解压词典（约 320 MB，几秒到十几秒）。progress 和 done 都在主线程里调用。 */
    fun prepare(progress: (Int) -> Unit, done: (Throwable?) -> Unit) {
        progressListener = progress
        lookupThread.execute {
            val error = runCatching { open() }.exceptionOrNull()
            if (error != null) Log.e(TAG, "打开词典失败", error)
            main.post { done(error) }
        }
    }

    @Volatile private var progressListener: ((Int) -> Unit)? = null

    private fun open() {
        if (japanese != null) return
        if (!DictData.ready(this)) {
            DictData.prepare(this) { percent -> progressListener?.let { l -> main.post { l(percent) } } }
            progressListener = null
        }
        val dir = DictData.dir(this)
        val started = System.currentTimeMillis()
        japanese = JapaneseDictionary(dir)
        english = EnglishDictionary(dir)
        Log.i(TAG, "词典就绪（${System.currentTimeMillis() - started}ms）")
    }

    /** 查 text[offset] 处的日语词，结果在主线程里交给 callback（不是日语时为 null）。 */
    fun lookup(text: String, offset: Int, callback: (WordInfo?) -> Unit) = lookupThread.execute {
        val word = runCatching {
            open()
            val started = System.currentTimeMillis()
            japanese!!.lookup(text, offset).also {
                Log.i(TAG, "查词（${System.currentTimeMillis() - started}ms）")
            }
        }.onFailure { Log.e(TAG, "查词失败", it) }.getOrNull()
        main.post { callback(word) }
    }

    fun lookupEnglish(word: String, callback: (EnglishWord?) -> Unit) = lookupThread.execute {
        val entry = runCatching { open(); english!!.lookup(word) }.onFailure { Log.e(TAG, "查英文失败", it) }.getOrNull()
        main.post { callback(entry) }
    }

    companion object {
        const val TAG = "EdgeSpeak"
        lateinit var instance: App
            private set
    }
}
