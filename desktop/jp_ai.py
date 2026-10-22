"""结合上下文解释日语词：把句子、目标词和词典义项发给 AI（Cohere / Mistral / OpenRouter），流式返回中文解释。"""
import base64
import logging
import os
import re
import threading
import time
from collections import OrderedDict, deque

import win32crypt

import ai_providers
from jp_dict import register_labels, sentence_around, to_hiragana

log = logging.getLogger("EdgeSpeak")

FIELDS = ("释义", "读音", "义项", "拆解", "翻译", "意思", "整条")
MAX_SENSES = 12
CACHE_SIZE = 200
UPDATE_INTERVAL = 0.12  # 流式输出时多久刷新一次卡片
AUTO_PER_MINUTE = 15  # 自动请求每分钟最多几次（Cohere 试用密钥、OpenRouter 免费模型都是每分钟 20 次，留出手动点的余量）

SYSTEM_PROMPT = """你是日语老师，帮中文母语的学习者理解句子里的一个日语词。
用户会给出：句子（目标词用【】标出）、分词器给的读音、原型、词典里的英文义项（带编号）和中文释义。
句子里可能有网络用语、俚语或新词，按它在这句话里的实际用法解释。
只按下面四行的格式用简体中文回答，不要寒暄，不要用 Markdown：
释义：这个词在这句话里的具体意思，一两句话
读音：【】里的部分在这句话里的读音，只写平假名
义项：最贴切的英文义项编号，只写数字；都不贴切就写“无”
翻译：整句的中文翻译"""

EN_SYSTEM_PROMPT = """你是英语老师，学习者是中文母语者，正在用日英词典查日语词。
用户会给出：日语词、它的一条英文释义、释义里看不懂的一个英文单词。
只按下面两行的格式用简体中文回答，不要寒暄，不要用 Markdown：
意思：这个英文单词在这条释义里的意思，一两句话
整条：整条英文释义的中文翻译"""


# ---------------------------------------------------------------- 密钥（用 Windows DPAPI 加密后存进配置，只有当前用户能解开）

def protect(secret):
    return base64.b64encode(win32crypt.CryptProtectData(secret.encode(), "EdgeSpeak", None, None, None, 0)).decode()


def unprotect(blob):
    return win32crypt.CryptUnprotectData(base64.b64decode(blob), None, None, None, 0)[1].decode()


def provider_of(cfg):
    return ai_providers.get(cfg.get("ai_provider", "cohere"))


def api_key(cfg, provider=None):
    """当前（或指定）AI 服务的密钥：先看配置，没有再看环境变量。"""
    provider = provider or provider_of(cfg)
    blob = cfg.get(provider.key_field)
    if blob:
        try:
            return unprotect(blob)
        except Exception:
            log.warning("配置里的 %s 密钥无法解密（可能是从别的电脑或用户复制来的），请重新填写", provider.label)
    return next((os.environ[v] for v in provider.env_vars if os.environ.get(v)), "")


# ---------------------------------------------------------------- 请求

def _labelled(text, tags):
    labels = register_labels(tags)
    return f"（{'、'.join(labels)}）{text}" if labels else text


def build_prompt(word):
    sentence, k = sentence_around(word.context, *word.span)
    marked = f"{sentence[:k]}【{word.surface}】{sentence[k + len(word.surface):]}"
    reading = to_hiragana("".join(r or t for t, r in word.ruby))
    senses = "\n".join(f"{n}. {_labelled('; '.join(glosses), misc)}"
                       for n, (_, glosses, misc) in enumerate(word.senses[:MAX_SENSES], 1))
    zh = "\n".join(f"- {_labelled(text, tags)}" for text, tags in word.zh)
    return (f"句子：{marked}\n目标词：{word.surface}（分词器读作 {reading}）\n原型：{word.base}（{word.base_reading}）\n"
            f"英文义项：\n{senses or '（英文词典里没有这个词）'}\n中文释义：\n{zh or '（无）'}")


def build_en_prompt(en_word, gloss, word):
    return f"日语词：{word.base}（{word.base_reading}）\n英文释义：{gloss}\n看不懂的英文单词：{en_word}"


def jp_request(word):
    """结合上下文解释日语词的请求：(系统提示, 用户提示)。"""
    return SYSTEM_PROMPT, build_prompt(word)


def en_request(en_word, gloss, word):
    """解释日语词英文释义里某个英文单词的请求。"""
    return EN_SYSTEM_PROMPT, build_en_prompt(en_word, gloss, word)


FIRST_FIELDS = ("释义", "意思")  # 两种回答格式各自的第一行


def strip_thinking(text):
    """去掉正文里的 <think>…</think>；思考还没结束（只有开头标签）时，后面的都不算正文。"""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    k = text.find("<think>")
    return text if k < 0 else text[:k]


def answer_part(text):
    """有的推理模型关掉思考后会把思考过程写进正文（以 <EOS_TOKEN> 开头，里面还会打草稿），
    答案在最后：从最后一次出现的第一行标签开始截取。正常回答原样返回。"""
    starts = [m.start() for m in re.finditer(rf"({'|'.join(FIRST_FIELDS)})\s*[：:]", text)]
    return text[starts[-1]:] if starts else text


def looks_like_answer(text):
    """正文是不是直接从答案开始（不是混进来的思考过程）。"""
    return re.match(rf"\s*({'|'.join(FIRST_FIELDS)})\s*[：:]", text) is not None


def parse(text):
    """把“释义：……”这样的几行解析成字典；还在流式输出时也能用（只解析已经出现的部分）。"""
    fields, current = {}, None
    for line in answer_part(text).splitlines():
        m = re.match(rf"\s*({'|'.join(FIELDS)})\s*[：:]\s*(.*)", line)
        if m:
            current = m.group(1)
            fields[current] = m.group(2).strip()
        elif current and line.strip():
            fields[current] += " " + line.strip()
    return fields


def key_model(chain, name, model):
    """冷却表里用配置里的写法（默认模型记作空串），和 model_chain() 的条目对得上。"""
    return model if (name, model) in chain else ""


def describe_failures(failures):
    """所有模型都没成功时卡片上显示的说明：主模型的错误为主，有备用模型时补一句。"""
    tried = [(p, m, e) for p, m, e in failures if e is not None]
    if not tried:
        provider = failures[0][0]
        return f"还没有设置 {provider.label} API 密钥：右键托盘图标 →「设置」。"
    provider, model, e = tried[0]
    if isinstance(e, EmptyAnswer):
        message = f"模型没有给出回答（{e}），可以在设置里换一个模型试试。"
    else:
        message = ai_providers.friendly_error(e, model or "（默认模型）", provider)
    return message + ("（备用模型也都没成功）" if len(tried) > 1 else "")


def chosen_sense(fields):
    m = re.search(r"\d+", fields.get("义项", ""))
    return int(m.group()) if m else None


def model_chain(cfg):
    """按顺序试的（服务名，模型）：先是主模型，再是设置里的备用模型。模型为空串表示该服务的默认模型。"""
    primary = provider_of(cfg)
    chain = [(primary.name, cfg.get(primary.model_field) or "")]
    for item in cfg.get("ai_fallbacks") or []:
        entry = (item.get("provider"), item.get("model") or "")
        if entry[0] in ai_providers.PROVIDERS and entry not in chain:
            chain.append(entry)
    return chain


def has_key(cfg):
    """主模型或任何一个备用模型的服务填了密钥。"""
    return any(api_key(cfg, ai_providers.get(name)) for name, _ in model_chain(cfg))


class EmptyAnswer(Exception):
    pass


# 出错后多久内先不找这个模型（还有别的可试时）：限速一般一分钟就恢复，余额不足、密钥无效要等用户处理
COOLDOWN = {429: 60, 402: 3600, 401: 3600, 403: 3600, 404: 3600}
COOLDOWN_OTHER = 30


class Explainer:
    """在后台线程里请求 AI，结果按（模型顺序，提示）缓存。request 是 jp_request() / en_request() 的返回值。
    主模型限速、额度用完或出错时，按顺序换备用模型；最近出过错的模型暂时排到后面。
    on_update(状态, 文字) 会在后台线程里被调用，状态依次为 loading →（thinking）→ streaming… → done，出错时为 error。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self.cache = OrderedDict()
        self.lock = threading.Lock()
        self.running = set()
        self.default_models = {}  # 服务名 → 模型留空时实际用的模型
        self.cooling = {}  # （服务名，模型）→ 冷却到什么时候
        self.recent = deque()  # 最近一分钟里发出的请求时间

    def _key(self, request):
        return (tuple(model_chain(self.cfg)),) + tuple(request)

    def cached(self, request):
        with self.lock:
            return self.cache.get(self._key(request))

    def is_running(self, request):
        with self.lock:
            return self._key(request) in self.running

    def explain(self, request, on_update, manual=True):
        """发起请求；自动请求（manual=False）在一分钟内超过 AUTO_PER_MINUTE 次时不发，返回 False。"""
        key = self._key(request)
        now = time.monotonic()
        with self.lock:
            if key in self.running:
                return True
            while self.recent and now - self.recent[0] > 60:
                self.recent.popleft()
            if not manual and len(self.recent) >= AUTO_PER_MINUTE:
                return False
            self.recent.append(now)
            self.running.add(key)
        threading.Thread(target=self._run, args=(key, on_update), daemon=True).start()
        return True

    def _ordered(self, chain):
        """不在冷却中的按原顺序排前面，冷却中的排后面（都在冷却时也照样试，总比直接报错好）。"""
        now = time.monotonic()
        ready = [entry for entry in chain if self.cooling.get(entry, 0) <= now]
        return ready + [entry for entry in chain if entry not in ready]

    def _model(self, provider, api, model):
        if model:
            return model
        if not self.default_models.get(provider.name):
            self.default_models[provider.name] = provider.default_model(api)
        if not self.default_models[provider.name]:
            raise ai_providers.ApiError(404, "没有可用的默认模型")
        return self.default_models[provider.name]

    def _run(self, key, on_update):
        chain, system, prompt = key[0], key[1], key[2]
        failures = []  # [(服务, 模型, 异常)]；异常为 None 表示没填密钥
        try:
            for name, model in self._ordered(chain):
                provider = ai_providers.get(name)
                api = api_key(self.cfg, provider)
                if not api:
                    failures.append((provider, model, None))
                    continue
                on_update("loading", "")
                try:
                    model = self._model(provider, api, model)
                    started = time.monotonic()
                    # 查词不需要推理：先关掉思考，更快也更省；否则推理型模型会把输出上限全花在思考上，正文一个字都没有
                    text, finish = self._collect(provider.stream(api, model, system, prompt), on_update)
                    log.info("AI 解释完成（%s %s，%.1fs，%d 字，%s）", provider.label, model,
                             time.monotonic() - started, len(text), finish)
                    text = answer_part(strip_thinking(text)).strip()
                    if not text:
                        raise EmptyAnswer(finish or "未知原因")
                except Exception as e:
                    log.warning("AI 解释失败（%s %s）：%r", provider.label, model or "默认模型", e)
                    status = getattr(e, "status_code", None)
                    self.cooling[(name, key_model(chain, name, model))] = \
                        time.monotonic() + COOLDOWN.get(status, COOLDOWN_OTHER)
                    failures.append((provider, model, e))
                    continue
                with self.lock:
                    self.cache[key] = text
                    if len(self.cache) > CACHE_SIZE:
                        self.cache.popitem(last=False)
                on_update("done", text)
                return
            on_update("error", describe_failures(failures))
        except Exception as e:
            log.exception("AI 解释出错")
            on_update("error", f"AI 解释出错：{e.__class__.__name__}")
        finally:
            with self.lock:
                self.running.discard(key)

    @staticmethod
    def _collect(events, on_update):
        """（正文，结束原因）。思考内容不显示，思考期间卡片显示“正在思考”。"""
        text, finish, last, thinking_shown = "", None, 0.0, False
        for kind, value in events:
            if kind == "text":
                text += value
                visible = strip_thinking(text)
                if not looks_like_answer(visible):
                    # 思考过程混在正文里：先显示“正在思考”，等完整结束后再从末尾取答案
                    if not thinking_shown and len(text.strip()) > 12:
                        thinking_shown = True
                        on_update("thinking", "")
                elif time.monotonic() - last > UPDATE_INTERVAL:
                    last = time.monotonic()
                    on_update("streaming", visible)
            elif kind == "thinking" and not thinking_shown:
                thinking_shown = True
                on_update("thinking", "")
            elif kind == "finish":
                finish = value
        return text, finish
