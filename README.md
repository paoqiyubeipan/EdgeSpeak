# EdgeSpeak

日语查词 + 朗读，给中文母语的日语学习者用。

- **desktop/**：Windows 版。选中文字按 Ctrl+Alt+Q 朗读（微软 Edge 神经网络语音）；按住 Shift 悬停在日语上弹出查词卡片：假名注音、音高、原型和变形拆解、英文和中文释义（标出网络用语、俚语等），点英文单词查中文，AI 结合上下文解释（Cohere / Mistral / OpenRouter，可设备用模型）。停在英文单词上弹出英文卡片（ECDICT 英汉释义、词形还原、AI 结合句子解释）。取不到文字时用 Windows OCR。词典每天自动更新。详见 [desktop/README.md](desktop/README.md)。
- **android/**：安卓版，同一套查词逻辑。选中文字后在菜单里点「EdgeSpeak 查词 / 朗读」，或用悬浮按钮点屏幕取词（读界面文字，读不到时用 ML Kit 日语 OCR）。英文单词也能查。详见 [android/README.md](android/README.md)。

安装包和 APK 在 [Releases](../../releases) 页面。

## 词典数据

仓库里只有代码，词典数据由脚本从上游下载生成（单个文件超过 GitHub 的大小上限）：

- 电脑版：`desktop\build_dict.py`，生成到 `desktop\dict\`
- 安卓版：`android\tools\make_assets.py`，从电脑版的 `dict` 和 Sudachi 词典整理到 `android\app\src\main\assets\dict\`

## 许可

代码使用 [BSD 3-Clause](LICENSE) 许可。

词典数据和第三方组件各有许可，分发带词典的安装包时需要一并注明：

| 组件 | 许可 |
|---|---|
| [JMdict](https://www.edrdg.org/jmdict/j_jmdict.html)（EDRDG），经 [jmdict-simplified](https://github.com/scriptin/jmdict-simplified) 转换 | CC BY-SA 4.0 |
| [Kanjium](https://github.com/mifunetoshiro/kanjium) 音高数据 | CC BY-SA 4.0 |
| [维基词典](https://www.wiktionary.org/)英文版、中文版，经 [kaikki.org](https://kaikki.org/) 提取 | CC BY-SA 4.0 |
| [ECDICT](https://github.com/skywind3000/ECDICT) | MIT |
| [UniDic](https://clrd.ninjal.ac.jp/unidic/)（国立国语研究所） | BSD |
| [Sudachi](https://github.com/WorksApplications/Sudachi)、[SudachiDict](https://github.com/WorksApplications/SudachiDict) | Apache 2.0 |
| [OpenCC](https://github.com/BYVoid/OpenCC) | Apache 2.0 |
| [ML Kit 文字识别](https://developers.google.com/ml-kit/vision/text-recognition/v2) | Google ML Kit 服务条款 |
| `desktop/installer/ChineseSimplified.isl`，来自 [kira-96/Inno-Setup-Chinese-Simplified-Translation](https://github.com/kira-96/Inno-Setup-Chinese-Simplified-Translation) | MIT，Copyright (c) 2019 - 2020 kirakira |

朗读使用微软 Edge 的在线语音服务（经 [edge-tts](https://github.com/rany2/edge-tts)），属于非官方接口，可能随时变化。
