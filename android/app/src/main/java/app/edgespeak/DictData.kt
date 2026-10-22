package app.edgespeak

import android.content.Context
import org.json.JSONObject
import java.io.File

/** 词典文件随 APK 打包在 assets/dict 里；SQLite 和 Sudachi 都要真实文件路径，第一次用时解压到应用目录。 */
object DictData {
    private const val ASSET_DIR = "dict"
    private val lock = Any()

    fun dir(context: Context) = File(context.filesDir, "dict")

    private fun manifest(context: Context): Map<String, Long> {
        val json = JSONObject(context.assets.open("$ASSET_DIR/manifest.json").bufferedReader().readText())
        val files = json.getJSONObject("files")
        return files.keys().asSequence().associateWith { files.getLong(it) }
    }

    /** 已解压且和这个版本的 APK 一致（换了新词典的 APK 会重新解压）。 */
    fun ready(context: Context): Boolean {
        val dir = dir(context)
        return manifest(context).all { (name, size) -> File(dir, name).length() == size }
    }

    /** 解压缺的或大小不对的文件。progress(已完成百分比)。可以从多个线程调用，只会解压一次。 */
    fun prepare(context: Context, progress: (Int) -> Unit = {}) = synchronized(lock) {
        val dir = dir(context).apply { mkdirs() }
        val files = manifest(context)
        val total = files.values.sum().coerceAtLeast(1)
        var done = 0L
        for ((name, size) in files) {
            val target = File(dir, name)
            if (target.length() == size) {
                done += size
                continue
            }
            val tmp = File(dir, "$name.tmp")
            context.assets.open("$ASSET_DIR/$name").use { input ->
                tmp.outputStream().use { output ->
                    val buffer = ByteArray(1 shl 20)
                    var lastPercent = -1
                    while (true) {
                        val n = input.read(buffer)
                        if (n < 0) break
                        output.write(buffer, 0, n)
                        done += n
                        val percent = (done * 100 / total).toInt()
                        if (percent != lastPercent) {
                            lastPercent = percent
                            progress(percent)
                        }
                    }
                }
            }
            if (!tmp.renameTo(target)) {
                target.delete()
                check(tmp.renameTo(target)) { "无法写入 $name" }
            }
        }
        progress(100)
    }
}
