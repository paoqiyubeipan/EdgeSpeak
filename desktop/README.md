# EdgeSpeak 划词朗读 + 日语查词

- **划词朗读**：选中任意文字，按 **Ctrl+Alt+Q** 朗读，再按一次停止。用微软 Edge TTS 的神经网络音色；断网或 Edge 不可用时自动改用 Windows 自带语音。
- **日语查词**：按住 **Shift**，把鼠标停在日语上，旁边弹出卡片：假名注音、音高、动词 / 形容词原型、词性、英文释义、中文释义（含网络用语），以及 AI 结合上下文的中文解释。点英文释义里的单词，旁边再弹出这个英文单词的中文释义卡片。
- **英文取词**：按住 Shift 停在英文单词上，弹出英文卡片：音标、中文释义、考试标签、词形变化（running → run），AI 结合这句话解释它在这里的意思并翻译整句。
- **词典自动更新**：词典有新版本时在后台下载，装好后直接生效，不用重启。

## 安装

有两种安装包，装好后功能一样：

| 安装包 | 大小 | 词典 |
|---|---|---|
| `EdgeSpeak-Setup-1.2.0.exe`（完整版） | 约 520 MB | 已带上，装完不联网也能完整使用 |
| `EdgeSpeak-Setup-1.2.0-lite.exe`（精简版） | 约 56 MB | 第一次启动后在后台下载（约 1.4 GB），日英词典一到就能查词，其余陆续到位；朗读不受影响。进度：把鼠标停在托盘图标上，或右键 →「词典」。下载失败（断网、安全软件拦截）时 10 分钟后自动重试 |

运行安装包：
- 装到 `%LOCALAPPDATA%\Programs\EdgeSpeak`，**不需要管理员权限**；开始菜单里有 EdgeSpeak 和卸载快捷方式，可选桌面快捷方式和开机自动启动。
- 安装包没有数字签名，第一次运行时 Windows SmartScreen 会提示“Windows 已保护你的电脑”：点 **更多信息 → 仍要运行**。
- 升级时直接运行新版安装包，会先结束正在运行的 EdgeSpeak。
- 卸载：开始菜单 → 卸载 EdgeSpeak，或 设置 → 应用。卸载会结束程序、删掉开机自启和后台下载的词典更新（`%LOCALAPPDATA%\EdgeSpeak\dict`），**保留**配置和 AI 密钥（`%APPDATA%\EdgeSpeak`），重装后不用重新设置。

## 使用

1. 从开始菜单打开 EdgeSpeak（没用安装包时双击 `dist\EdgeSpeak\EdgeSpeak.exe`，整个文件夹要放在一起），任务栏右下角出现蓝色喇叭图标。
2. 划词朗读：选中文字 → `Ctrl+Alt+Q`。边下载边播放，长段落也约 1 秒出声。
3. 自动判断朗读语言：
   - 有假名（ひらがな/カタカナ）→ 日语
   - 只有汉字 → 默认按日语读（如「東京大学」）；出现中文特有的字（这、们、说、吗、是、我、很……）则按中文读。托盘「只有汉字时」可改成中文优先。
   - 其余 → 英文
   - 按日语读时，繁体 / 旧字体会先转成日语新字体（發注→発注、擔當→担当），否则日语音色会逐字乱读。
4. 右键托盘图标：切换中/英/日音色（选中后会试读）、汉字优先语言、Shift 悬停查词开关（和「英文单词也查」）、语速、朗读引擎、词典更新、开机自动启动、退出。

**火绒等安全软件**第一次会询问 EdgeSpeak.exe 是否允许联网，请选“允许”。不允许的话只能用系统语音。

## 查词（Shift 悬停）

- 按住 Shift 停在词上约 0.1 秒弹出卡片；在句子里移动鼠标，卡片跟着换词。
- 松开 Shift 卡片会留着，可以把鼠标移上去：
  - **点标题**读原文（食べなかった）；
  - **点英文释义里的任意单词**（如 salary），在旁边弹出英文卡片：音标、按词性分行的中文释义、考试标签（四六级、考研…）、词形变化。点变形词（eating、ate）会跳到原词（eat）并注明是哪种变形；点英文卡片的标题读英文发音。
  - 英文卡片底部的 **「AI · 它在这条释义里是什么意思」**：AI 结合你点的那条日语释义，解释这个英文单词在这里的意思，并翻译整条释义（如 subsist on 在「靠工资过活」里的意思）。只在点了之后才请求。
  - 只有能点的地方鼠标才会变成小手。
- 鼠标移远、点别处或按 Esc 关闭卡片（两张一起关）。
- 按 Shift 打字（输入大写字母、Ctrl+Shift 组合键）不会触发。
- 查词方式：从光标处开始找词典里最长的词（「発注担当者」指着「担当」会查到「担当者」），并把变形词尾归到同一个词里还原原型（食べなかった → 食べる、連絡してください → 連絡する）。以助词结尾的匹配要读音对得上，「今日は暑い」不会被当成寒暄语「今日は（こんにちは）」。
- 释义：先列 JMdict 的英文释义（最多 5 条，多的显示“还有几条”）；JMdict 没收的词（很多网络用语、新词）用英文维基词典的。下面绿色的「中文」区是中文维基词典的释义。
- **网络用语 / 俚语**：释义前用红字标出〔网络用语〕〔俚语〕〔新词〕〔口语〕〔古语〕〔尊敬语〕等。JMdict 有这个词但没收网络用法时（如「草」= 笑死），会从维基词典补上。分词词典是 2025 年 12 月版，バズる、ぴえん、エモい、推し 这类词能正确切分并还原原型。
- 音高：橙色横线表示高，竖线是下降处；圆圈数字是音调型（⓪ 平板、① 头高……）。显示的是原型的音高，变形后音高可能移动。

**英文单词**：光标下不是日语、而是英文单词时（英文网页、文档，或日语里夹的「PC」「OK」），弹出英文卡片，查的是 ECDICT 完整版：
- 变形词还原到原词并注明（running 是 run 的现在分词、RUNS → run）；连字符词先查整体（well-known、state-of-the-art），查不到再查光标所在的那一段；John's 这类先查整体再去掉 's。全角英文（ＰＣ）也能查。
- **以英式英语为准**：查美式拼写时标题显示英式写法，下面绿字注明「美式拼写 color」（color → colour、center → centre、organize → organise、traveler → traveller……约 1700 对，由 `build_uk_english.py` 从 tysto 对照表筛出常规差异并去掉古旧写法（waggon、gaol、mediaeval 之类），变形也跟着换成英式：coloured、organising）；直接查英式拼写得到同一张卡片。只在部分义项上不同的（tire 指轮胎时写 tyre、program 指节目时写 programme、meter 指长度单位时写 metre……）不改标题，只加说明。常见的英美用词差异也会注明（elevator → 英式说 lift、apartment → 英式常说 flat、pants → 英式说 trousers）。音标本来就是英式的（ECDICT 以英音为主），默认英文音色是英式的 Sonia。
- 有的变形词本身也是个词（tired 疲倦的、left 左边的、rose 玫瑰），卡片会同时写出「是 tire 的过去式」和它自己的意思，AI 按句子判断。
- 词典里没有的普通单词（新词、专有名词）也会弹出卡片，可以让 AI 解释；网址、路径、邮箱、带数字或下划线的代码（example.com、my_var、mp3）和驼峰式标识符（LookupPopup）不弹。
- AI 区和日语卡片一样会自动请求（同样受 0.6 秒停留和每分钟次数的限制），回答两行：**意思**（这个词在这句话里的意思）和**翻译**（整句中文）。点标题读英文发音。
- 只想查日语时，在托盘菜单里关掉「英文单词也查」。

**AI 解释（结合上下文）**：卡片在同一个词上停留约 0.6 秒后，会自动把这句话、目标词、读音和中英文词典释义发给你选的 AI 服务，几秒内在卡片底部逐步显示（也可以在设置里关掉自动，改为点「AI · 结合上下文解释」按钮）。词典里没有的网络用语，也会按它在句子里的用法解释：
- 释义：这个词在这句话里的中文意思
- 读音：只在 AI 认为这里的读法和卡片上标的不一样时才出现（红字，如「今日の日本」这里读「こんにち」）
- 义项：「对应上面第 N 条英文释义」，并在上面的英文释义里用黄色高亮；都不贴切时写「词典释义都不贴切」
- 拆解：变形拆解，如「食べる + て（连接）+ いける（可能）+ ない（否定）」。这一行由本地分词生成，不靠 AI，所以准确且不占额度
- 翻译：整句中文翻译

请求时会关掉模型的“思考”（推理）：查词用不上，关掉后一般 3–5 秒出结果。有的推理模型（如 command-a-plus）关掉思考后偶尔仍会把思考过程写进回答，程序会只取最后的答案，期间卡片显示“AI 正在思考…”，但会慢一些（8–12 秒）；想更快可以在设置里换一个非推理模型。如果模型没给出回答，卡片会写明原因，而不是留空。

需要先设置：**左键单击托盘图标**（或右键 →「设置…」）打开设置窗口，选一个**服务**，填入它的 API 密钥，点「刷新列表」选模型（留空用默认模型），「测试连接」确认后保存。三个服务的密钥和模型各存各的，切换服务不用重新填。

| 服务 | 密钥 | 免费用法 | 模型留空时 |
|---|---|---|---|
| Cohere | [Cohere 控制台](https://dashboard.cohere.com/api-keys) | 试用密钥：每月 1,000 次、每分钟 20 次，不能商用 | Cohere 的默认聊天模型 |
| Mistral | [Mistral 控制台](https://console.mistral.ai/api-keys) | 免费的 Experiment 计划（需验证手机号，有速率限制） | `mistral-small-latest` |
| OpenRouter | [OpenRouter](https://openrouter.ai/keys) | 只列免费模型（名字带 `:free`）和免费路由 `openrouter/free`；按账户限次：每分钟 20 次、每天 50 次（累计充值满 10 美元后每天 1000 次） | `openrouter/free`（自动挑一个当前可用的免费模型） |

- 按住 Shift 在句子里滑过时不会每个词都请求：卡片要在一个词上停够 0.6 秒才发。同一句里同一个词的结果会缓存，不重复消耗额度。
- 自动请求一分钟内最多 15 次，超过就暂停一会儿（卡片上会提示，仍可手动点）。
- **备用模型**：设置窗口下方可以按顺序列几个「服务 + 模型」。主模型限速、额度用完、余额不足或出错时，自动换下一个（没填密钥的跳过）；出过错的模型会暂时排到后面（限速约 1 分钟，余额不足、密钥无效约 1 小时），之后自动恢复优先用主模型。
- 额度怎么算：Cohere 的每分钟限制按模型分开，但每月 1,000 次是整个试用密钥共用的；OpenRouter 免费模型的次数按账户算，换模型或多开密钥都不会增加。所以备用模型的作用是躲开限速和故障，想多拿额度要跨服务（如 Cohere 用完换 OpenRouter）。
- 自动解释开着时额度用得快，可以在设置里关掉。
- 三个服务都会先关掉模型的“思考”（推理）；模型不允许关时自动改为允许思考，期间卡片显示“AI 正在思考…”。
- 密钥用 Windows DPAPI 加密后存在配置文件里，只有当前 Windows 用户能解开；换电脑或换用户需要重新填写。也可以用环境变量提供：`CO_API_KEY`、`MISTRAL_API_KEY`、`OPENROUTER_API_KEY`。

**怎么取到光标下的字**：
1. 先用 Windows 辅助接口（UI Automation）直接读文字，约 20–40 毫秒。浏览器、Word、记事本、Claude 桌面版、VS Code 的界面文字都走这条路。
2. 读不到时（VS Code 代码编辑区、canvas、网页里的图片、PDF、游戏……）自动改用 **Windows 自带的日语 OCR**：截取光标所在的一条屏幕识别，约 0.1–0.3 秒。认出来是英文时，再用系统里的英文 OCR（英文、中文系统一般自带）重认一次，单词和空格更准。
   需要系统里装了日语 OCR。没装时，启动后会弹通知，托盘菜单里出现 **「安装日语 OCR（需要管理员确认）」**：点了会弹出 Windows 的管理员确认，确认后从 Windows Update 下载安装（需联网，一两分钟），装好立即可用。
   也可以手动装：设置 → 时间和语言 → 语言和区域 → 添加日本語，或在管理员 PowerShell 里运行 `Add-WindowsCapability -Online -Name "Language.OCR~~~ja-JP~0.0.1.0"`。
   OCR 偶尔会认错字；日志里每次查词都会标明用的是 `uia` 还是 `ocr`。
在 Chromium 内核的程序里第一次查词时，程序要先开启辅助功能，可能要停一下才弹出。

## 词典和自动更新

| 词典 | 用途 | 大小 | 上游更新频率 |
|---|---|---|---|
| UniDic 現代書き言葉（2025-12） | 分词、读音、原型、部分音高 | 约 1.3 GB | 一两年一次 |
| JMdict + Kanjium | 日→英释义、音高 | 约 65 MB | 每周 |
| 维基词典（英文版 + 中文版的日语词条） | 网络用语、新词的英文释义；中文释义 | 约 25 MB | 约每月 |
| ECDICT 完整版（约 340 万词条） | 英→中（英文取词、日语卡片里点英文单词） | 约 330 MB | 很少更新 |

- 程序自带一份（`dist\EdgeSpeak\dict`）。启动 2 分钟后检查一次，之后每天一次；有新版本就在后台下载、生成，放到 `%LOCALAPPDATA%\EdgeSpeak\dict`，装好后弹通知，下一次查词就用新的，不用重启。旧版本会自动删除。
- 托盘菜单 →「词典」：**立即检查更新**、**每天自动更新**（开关）。
- 分词词典更新时要下载约 700 MB；网络或安全软件不放行时只记日志，继续用现有词典。

## 配置

`%APPDATA%\EdgeSpeak\config.json`，改完后从托盘退出再重新打开：

| 项 | 说明 |
|---|---|
| `hotkey` | 快捷键，如 `ctrl+alt+q`、`alt+f1`、`ctrl+shift+e` |
| `zh_voice` / `en_voice` / `ja_voice` | 任意 Edge 音色名，如 `zh-CN-YunyangNeural`、`ja-JP-KeitaNeural`；英文默认英式的 `en-GB-SoniaNeural` |
| `kanji_lang` | 只有汉字时按哪种语言读：`ja` 或 `zh` |
| `hover_lookup` | 是否启用 Shift 悬停查词：`true` / `false` |
| `hover_english` | 悬停在英文单词上时是否也查：`true` / `false` |
| `ai_provider` | AI 解释用哪个服务：`cohere`、`mistral` 或 `openrouter` |
| `ai_model` / `mistral_model` / `openrouter_model` | 各服务用的模型名，留空用默认（建议在设置窗口里选） |
| `ai_fallbacks` | 备用模型列表，如 `[{"provider": "openrouter", "model": "openrouter/free"}]`，在设置窗口里编辑 |
| `ai_auto` | 卡片停留片刻后是否自动请求 AI 解释：`true` / `false` |
| `cohere_api_key` / `mistral_api_key` / `openrouter_api_key` | 加密后的密钥，请在设置窗口里填写，不要手改 |
| `dict_auto_update` | 是否每天在后台检查词典更新：`true` / `false` |
| `rate` | 语速百分比，-50 ~ 50 |
| `engine` | `auto`（Edge 优先）或 `sapi`（只用系统语音） |

快捷键被别的程序占用时会弹通知，换一个即可。出问题时看 `%APPDATA%\EdgeSpeak\log.txt`。

## 已知限制

- 划词朗读靠模拟 Ctrl+C 取词：会出现在 Win+V 剪贴板历史里；终端里没选中文字时按快捷键会发送 Ctrl+C（中断命令）。
- 程序文件夹约 2 GB，其中词典约 1.7 GB（见上表）。
- 中文释义来自中文维基词典，不是每个词都有；没有时靠 AI 解释。
- 音高只在 Kanjium 或 UniDic 收录时显示，且是原型的音高。

## 从源码运行 / 重新打包

```bat
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python build_dict.py
.venv\Scripts\python edge_speak.py
build.cmd
```

`build_dict.py` 会下载全部最新数据（约 1.4 GB），生成到 `dict\`；`build_dict.py jmdict wiktionary` 只重新生成其中几份。`build.cmd` 打包后把 `dict\` 复制到 `dist\EdgeSpeak\dict`。

制作安装包：装好 Inno Setup 6（`winget install JRSoftware.InnoSetup`）后运行 `build_installer.cmd 1.0.0`，生成 `dist\EdgeSpeak-Setup-1.0.0.exe`；`build_installer.cmd 1.0.0 lite` 生成不带词典的 `dist\EdgeSpeak-Setup-1.0.0-lite.exe`（脚本在 `installer\EdgeSpeak.iss`，中文界面翻译来自 [kira-96/Inno-Setup-Chinese-Simplified-Translation](https://github.com/kira-96/Inno-Setup-Chinese-Simplified-Translation)）。自测命令：`EdgeSpeak.exe --say "要读的文字"`。

## 数据来源与许可

- [JMdict](https://www.edrdg.org/jmdict/j_jmdict.html)（EDRDG，CC BY-SA 4.0），经 [jmdict-simplified](https://github.com/scriptin/jmdict-simplified) 转换
- 音高：[Kanjium](https://github.com/mifunetoshiro/kanjium)（CC BY-SA 4.0）
- [维基词典](https://www.wiktionary.org/)英文版、中文版（CC BY-SA 4.0），经 [kaikki.org / wiktextract](https://kaikki.org/) 提取
- 英汉词典：[ECDICT](https://github.com/skywind3000/ECDICT) 完整版（MIT）
- 英美拼写对照：[tysto.com 的对照表](http://www.tysto.com/uk-us-spelling-list.html)，经 [hyperreality/American-British-English-Translator](https://github.com/hyperreality/American-British-English-Translator) 整理（MIT），见 `uk_english.json`
- 分词：MeCab + [UniDic](https://clrd.ninjal.ac.jp/unidic/) 現代書き言葉（国立国语研究所，BSD 许可）；找不到时退回随程序带的 unidic-lite
- 繁体 → 日文新字体：[OpenCC](https://github.com/BYVoid/OpenCC)（Apache 2.0）
- OCR：Windows 自带的 Windows.Media.Ocr，经 [PyWinRT](https://github.com/pywinrt/pywinrt) 调用（MIT）
