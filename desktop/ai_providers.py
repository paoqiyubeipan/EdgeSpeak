"""AI 服务：Cohere、Mistral、OpenRouter。每个服务都能列模型、选默认模型、流式对话、测试连接。

stream() 产生的事件：("text", 片段)、("thinking", None)、("finish", 结束原因)。
查词用不上推理，请求时都先关掉思考；模型不允许关时自动改用允许思考的请求（思考内容不返回或只显示“正在思考”）。
"""
import json
import logging
import time

import httpx

log = logging.getLogger("EdgeSpeak")

MAX_TOKENS = 4000  # 只防模型失控：回答长短靠提示词里的固定格式控制
TEMPERATURE = 0.3


class ApiError(Exception):
    """服务返回的错误。status_code 和 Cohere SDK 的异常同名，方便统一处理。"""

    def __init__(self, status_code, message):
        super().__init__(f"{status_code}: {message}")
        self.status_code = status_code
        self.message = message


def _rejects_reasoning_setting(e):
    """请求因为“关闭思考”这个参数被拒（模型必须思考，或根本不支持这个参数）。"""
    text = str(getattr(e, "message", "") or e).lower()
    return getattr(e, "status_code", None) in (400, 422) and any(w in text for w in ("think", "reason"))


class Provider:
    name = label = key_url = hint = ""
    key_field = model_field = ""  # 配置里存密钥 / 模型的字段
    env_vars = ()
    default_hint = ""  # 模型留空时用哪个

    def stream(self, key, model, system, prompt):
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        try:
            yield from self._stream(key, model, messages, disable_reasoning=True)
        except Exception as e:
            if not _rejects_reasoning_setting(e):
                raise
            log.info("模型 %s 不能关闭思考，改为允许思考", model)
            yield from self._stream(key, model, messages, disable_reasoning=False)

    def test(self, key, model):
        """（是否成功，说明）。用一次很短的请求验证密钥和模型。"""
        started = time.monotonic()
        try:
            for _ in self.stream(key, model, "只回答「はい」。", "「はい」とだけ答えて"):
                pass
        except Exception as e:
            return False, friendly_error(e, model, self)
        return True, f"连接成功，用时 {time.monotonic() - started:.1f} 秒。"


# ---------------------------------------------------------------- Cohere（官方 SDK）

class Cohere(Provider):
    name, label = "cohere", "Cohere"
    key_url = "https://dashboard.cohere.com/api-keys"
    hint = "试用密钥免费：每月 1,000 次、每分钟 20 次，不能商用。"
    key_field, model_field = "cohere_api_key", "ai_model"
    env_vars = ("CO_API_KEY", "COHERE_API_KEY")
    default_hint = "留空则用 Cohere 的默认聊天模型。可以直接输入模型名。"

    @staticmethod
    def client(key):
        import cohere  # 只在用 Cohere 时加载
        return cohere.ClientV2(api_key=key, timeout=30, max_retries=1)

    def list_models(self, key):
        response = self.client(key).models.list(endpoint="chat", page_size=1000)
        return sorted(m.name for m in response.models or [] if m.name and not m.is_deprecated)

    def default_model(self, key):
        response = self.client(key).models.list(endpoint="chat", default_only=True)
        return response.models[0].name if response.models else None

    def _stream(self, key, model, messages, disable_reasoning):
        extra = {"thinking": {"type": "disabled"}} if disable_reasoning else {}
        stream = self.client(key).chat_stream(model=model, temperature=TEMPERATURE, messages=messages,
                                              max_tokens=MAX_TOKENS, **extra)
        for event in stream:
            if not event:
                continue
            if event.type == "content-delta":
                content = event.delta.message.content
                piece = getattr(content, "text", None)
                if piece:
                    yield "text", piece
                elif getattr(content, "thinking", None):
                    yield "thinking", None
            elif event.type == "message-end" and event.delta:
                yield "finish", event.delta.finish_reason


# ---------------------------------------------------------------- OpenAI 兼容接口（Mistral、OpenRouter）

def _error_message(response):
    """从错误响应里取出说明：{"error": {"message": ...}}、{"message": ...} 或 {"detail": ...}。"""
    try:
        body = response.json()
    except ValueError:
        return response.text[:300] or response.reason_phrase
    if not isinstance(body, dict):
        return str(body)[:300]
    error = body.get("error")
    if isinstance(error, dict):
        error = error.get("message") or error
    message = error or body.get("message") or body.get("detail") or body
    return (message if isinstance(message, str) else json.dumps(message, ensure_ascii=False))[:300]


class OpenAICompatible(Provider):
    base_url = ""
    headers = {}

    def _headers(self, key):
        return {"Authorization": f"Bearer {key}", **self.headers}

    def _get(self, path, key=None):
        response = httpx.get(self.base_url + path, headers=self._headers(key) if key else self.headers, timeout=30)
        if response.status_code >= 400:
            raise ApiError(response.status_code, _error_message(response))
        return response.json()

    def _reasoning(self, disable):
        return {}

    def _stream(self, key, model, messages, disable_reasoning):
        body = {"model": model, "messages": messages, "temperature": TEMPERATURE, "max_tokens": MAX_TOKENS,
                "stream": True, **self._reasoning(disable_reasoning)}
        timeout = httpx.Timeout(60, connect=15)
        with httpx.stream("POST", self.base_url + "/chat/completions", json=body, headers=self._headers(key),
                          timeout=timeout) as response:
            if response.status_code >= 400:
                response.read()
                raise ApiError(response.status_code, _error_message(response))
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue  # 空行和 “: OPENROUTER PROCESSING” 这类保活注释
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("error"):
                    error = chunk["error"]
                    raise ApiError(error.get("code") or 500, error.get("message") or str(error))
                for choice in chunk.get("choices", [])[:1]:
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    if isinstance(content, str):
                        if content:
                            yield "text", content
                    elif isinstance(content, list):  # Mistral 的思考模型：[{type: thinking}, {type: text}]
                        for part in content:
                            if part.get("type") == "text" and part.get("text"):
                                yield "text", part["text"]
                            elif part.get("type") == "thinking":
                                yield "thinking", None
                    if delta.get("reasoning") or delta.get("reasoning_details"):
                        yield "thinking", None
                    if choice.get("finish_reason"):
                        yield "finish", choice["finish_reason"]


class Mistral(OpenAICompatible):
    name, label = "mistral", "Mistral"
    key_url = "https://console.mistral.ai/api-keys"
    hint = "有免费的 Experiment 计划（需要验证手机号，有速率限制）。"
    key_field, model_field = "mistral_api_key", "mistral_model"
    env_vars = ("MISTRAL_API_KEY",)
    base_url = "https://api.mistral.ai/v1"
    default_hint = "留空则用 mistral-small-latest。可以直接输入模型名。"

    def _reasoning(self, disable):
        return {"reasoning_effort": "none"} if disable else {}

    def list_models(self, key):
        models = self._get("/models", key).get("data", [])
        return sorted({m["id"] for m in models
                       if (m.get("capabilities") or {}).get("completion_chat") and not m.get("deprecation")})

    def default_model(self, key):
        return "mistral-small-latest"


class OpenRouter(OpenAICompatible):
    name, label = "openrouter", "OpenRouter"
    key_url = "https://openrouter.ai/keys"
    hint = ("只列免费模型。openrouter/free 会自动挑一个当前可用的免费模型。免费模型按账户限次："
            "每分钟 20 次，每天 50 次（累计充值满 10 美元后每天 1000 次）。")
    key_field, model_field = "openrouter_api_key", "openrouter_model"
    env_vars = ("OPENROUTER_API_KEY",)
    base_url = "https://openrouter.ai/api/v1"
    headers = {"X-Title": "EdgeSpeak"}
    FREE_ROUTER = "openrouter/free"  # 免费模型路由；openrouter/auto 会路由到收费模型，不列
    default_hint = f"留空则用 {FREE_ROUTER}。"

    def _reasoning(self, disable):
        # 必须思考的模型不接受 effort: none，退一步：照常思考但不返回思考内容
        return {"reasoning": {"effort": "none"}} if disable else {"reasoning": {"exclude": True}}

    def list_models(self, key):
        """免费的文字模型（模型列表不需要密钥），免费路由排第一。"""
        free = set()
        for m in self._get("/models").get("data", []):
            # 只认带 :free 的：标价为 0 的预览模型里有音乐生成之类不能聊天的；安全分类模型也不能用来解释
            outputs = (m.get("architecture") or {}).get("output_modalities") or ["text"]
            if m["id"].endswith(":free") and "text" in outputs and not any(w in m["id"] for w in ("safety", "guard")):
                free.add(m["id"])
        free.add(self.FREE_ROUTER)
        return sorted(free, key=lambda i: (i != self.FREE_ROUTER, i))

    def default_model(self, key):
        return self.FREE_ROUTER


PROVIDERS = {p.name: p for p in (Cohere(), Mistral(), OpenRouter())}


def get(name):
    return PROVIDERS.get(name) or PROVIDERS["cohere"]


def friendly_error(e, model, provider):
    status = getattr(e, "status_code", None)
    label = provider.label
    if status == 401:
        return f"{label} API 密钥无效，请右键托盘图标 →「设置」检查。"
    if status == 402:
        return f"{label} 账户余额不足：换一个名字带 :free 的免费模型，或充值后再试。"
    if status == 429:
        if provider.name == "cohere":
            return "请求太频繁，或本月免费额度（1,000 次）已用完。"
        return f"请求太频繁，或 {label} 的免费额度用完了，稍后再试。"
    if status == 404:
        return f"找不到模型 {model}，请在设置里换一个。"
    if status in (400, 422):
        message = getattr(e, "message", None)
        if message is None:
            body = getattr(e, "body", None)
            message = body.get("message") if isinstance(body, dict) else body
        return f"请求被拒绝：{message or e}"
    if status:
        return f"{label} 服务出错（{status}），请稍后再试。"
    return f"连不上 {label}：{e.__class__.__name__}。请检查网络或安全软件是否放行。"
