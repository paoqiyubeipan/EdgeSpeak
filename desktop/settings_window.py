"""设置窗口：AI 服务（Cohere / Mistral / OpenRouter）、各自的 API 密钥和模型，以及备用模型。
tkinter 在自己的线程里运行，同一时间只开一个。"""
import ctypes
import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk

import ai_providers
import jp_ai

_window = {"hwnd": None}
_lock = threading.Lock()


def open_settings(cfg, save_config, icon=None):
    """打开设置窗口；已经开着就把它提到前面。可以从任意线程调用。icon 是 PIL 图片。"""
    with _lock:
        if _window["hwnd"]:
            if _window["hwnd"] != -1:
                ctypes.windll.user32.SetForegroundWindow(_window["hwnd"])
            return
        _window["hwnd"] = -1  # 正在创建
    threading.Thread(target=_run, args=(cfg, save_config, icon), daemon=True).start()


def _run(cfg, save_config, icon):
    try:
        SettingsWindow(cfg, save_config, icon).run()
    finally:
        with _lock:
            _window["hwnd"] = None


class SettingsWindow:
    def __init__(self, cfg, save_config, icon=None):
        self.cfg, self.save_config = cfg, save_config
        self.results = queue.Queue()  # 后台线程把结果放这里，由 Tk 线程取出更新界面
        providers = list(ai_providers.PROVIDERS.values())
        # 每个服务各自的密钥、模型和模型列表；切换服务时换着显示，保存时一起存
        self.keys = {p.name: jp_ai.api_key(cfg, p) for p in providers}
        self.models = {p.name: cfg.get(p.model_field, "") for p in providers}
        self.model_lists = {p.name: [] for p in providers}
        self.provider = jp_ai.provider_of(cfg)
        self.fallbacks = [(item["provider"], item.get("model", "")) for item in cfg.get("ai_fallbacks") or []
                          if item.get("provider") in ai_providers.PROVIDERS]

        root = self.root = tk.Tk()
        root.title("EdgeSpeak 设置")
        if icon is not None:
            from PIL import ImageTk
            self._icon = ImageTk.PhotoImage(icon)  # 保持引用，否则图标会被回收
            root.iconphoto(True, self._icon)
        root.resizable(False, False)
        root.option_add("*Font", ("Microsoft YaHei UI", 10))
        style = ttk.Style(root)
        style.configure(".", font=("Microsoft YaHei UI", 10))
        style.configure("Hint.TLabel", foreground="#6b7280")
        style.configure("Link.TLabel", foreground="#0078d4")

        scale = root.winfo_fpixels("1i") / 96  # 折行宽度按像素算，高分屏上要跟着放大
        frame = ttk.Frame(root, padding=18)
        frame.grid(sticky="nsew")
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="AI 解释（结合上下文的中文释义）", font=("Microsoft YaHei UI", 11, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

        ttk.Label(frame, text="服务").grid(row=1, column=0, sticky="w", padx=(0, 10))
        self.labels = {p.label: p.name for p in providers}
        self.provider_var = tk.StringVar(value=self.provider.label)
        provider_box = ttk.Combobox(frame, textvariable=self.provider_var, values=list(self.labels), state="readonly",
                                    width=20)
        provider_box.grid(row=1, column=1, sticky="w")
        provider_box.bind("<<ComboboxSelected>>", lambda e: self._switch(self.labels[self.provider_var.get()]))
        self.hint = ttk.Label(frame, text="", style="Hint.TLabel", wraplength=round(440 * scale))
        self.hint.grid(row=2, column=1, columnspan=2, sticky="w", pady=(4, 12))

        ttk.Label(frame, text="API 密钥").grid(row=3, column=0, sticky="w", padx=(0, 10))
        self.key = tk.StringVar()
        self.key_entry = ttk.Entry(frame, textvariable=self.key, show="•", width=44)
        self.key_entry.grid(row=3, column=1, sticky="ew")
        self.reveal = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="显示", variable=self.reveal, command=self._toggle_reveal).grid(
            row=3, column=2, padx=(8, 0))
        self.link = ttk.Label(frame, text="", style="Link.TLabel", cursor="hand2")
        self.link.grid(row=4, column=1, sticky="w", pady=(4, 10))
        self.link.bind("<Button-1>", lambda e: webbrowser.open(self.provider.key_url))

        ttk.Label(frame, text="模型").grid(row=5, column=0, sticky="w", padx=(0, 10))
        self.model = tk.StringVar()
        self.model_box = ttk.Combobox(frame, textvariable=self.model, width=42)
        self.model_box.grid(row=5, column=1, sticky="ew")
        ttk.Button(frame, text="刷新列表", command=self._refresh_models).grid(row=5, column=2, padx=(8, 0))
        self.model_hint = ttk.Label(frame, text="", style="Hint.TLabel")
        self.model_hint.grid(row=6, column=1, columnspan=2, sticky="w", pady=(4, 10))

        self.auto = tk.BooleanVar(value=cfg.get("ai_auto", True))
        ttk.Checkbutton(frame, text="卡片在一个词上停留片刻后自动解释（每次算 1 次请求）",
                        variable=self.auto).grid(row=7, column=1, columnspan=2, sticky="w", pady=(0, 10))

        test_row = ttk.Frame(frame)
        test_row.grid(row=8, column=1, columnspan=2, sticky="w")
        ttk.Button(test_row, text="测试连接", command=self._test).pack(side="left")
        self.status = ttk.Label(test_row, text="", style="Hint.TLabel", wraplength=round(360 * scale))
        self.status.pack(side="left", padx=(10, 0))

        # 备用模型：主模型限速、额度用完或出错时按顺序换下一个
        ttk.Separator(frame).grid(row=9, column=0, columnspan=3, sticky="ew", pady=(16, 10))
        ttk.Label(frame, text="备用模型", font=("Microsoft YaHei UI", 11, "bold")).grid(
            row=10, column=0, columnspan=3, sticky="w")
        ttk.Label(frame, text="主模型限速、额度用完或出错时，按顺序换下一个。各服务的密钥在上面切换到该服务后填写，"
                              "没填密钥的会被跳过。", style="Hint.TLabel", wraplength=round(520 * scale)).grid(
            row=11, column=0, columnspan=3, sticky="w", pady=(2, 8))
        add_row = ttk.Frame(frame)
        add_row.grid(row=12, column=0, columnspan=3, sticky="ew")
        self.fb_provider_var = tk.StringVar(value=self.provider.label)
        fb_provider_box = ttk.Combobox(add_row, textvariable=self.fb_provider_var, values=list(self.labels),
                                       state="readonly", width=11)
        fb_provider_box.pack(side="left")
        fb_provider_box.bind("<<ComboboxSelected>>", lambda e: (self.fb_model.set(""), self._fill_fb_models()))
        self.fb_model = tk.StringVar()
        self.fb_model_box = ttk.Combobox(add_row, textvariable=self.fb_model, width=34,
                                         postcommand=self._fill_fb_models)
        self.fb_model_box.pack(side="left", padx=(6, 0))
        ttk.Button(add_row, text="添加", command=self._add_fallback).pack(side="left", padx=(6, 0))

        list_row = ttk.Frame(frame)
        list_row.grid(row=13, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        self.fb_list = tk.Listbox(list_row, height=4, activestyle="none", exportselection=False)
        self.fb_list.pack(side="left", fill="x", expand=True)
        list_buttons = ttk.Frame(list_row)
        list_buttons.pack(side="left", padx=(6, 0), anchor="n")
        ttk.Button(list_buttons, text="上移", command=lambda: self._move_fallback(-1)).pack(fill="x")
        ttk.Button(list_buttons, text="下移", command=lambda: self._move_fallback(1)).pack(fill="x", pady=(4, 0))
        ttk.Button(list_buttons, text="删除", command=self._remove_fallback).pack(fill="x", pady=(4, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=14, column=0, columnspan=3, sticky="e", pady=(18, 0))
        ttk.Button(buttons, text="取消", command=root.destroy).pack(side="right")
        ttk.Button(buttons, text="保存", command=self._save).pack(side="right", padx=(0, 8))
        root.bind("<Escape>", lambda e: root.destroy())

        self._show(self.provider)
        self._show_fallbacks()
        self.key.trace_add("write", lambda *args: self._show_fallbacks())  # 填了密钥后“会跳过”的提示要更新
        root.update_idletasks()
        _window["hwnd"] = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        root.attributes("-topmost", True)
        root.after(300, lambda: root.attributes("-topmost", False))
        root.focus_force()
        self.key_entry.focus_set()
        root.after(100, self._poll)

    def run(self):
        self.root.mainloop()

    def _show(self, provider):
        """把某个服务的说明、密钥和模型填进界面。"""
        self.provider = provider
        self.hint.configure(text=provider.hint)
        self.link.configure(text=f"还没有密钥？到 {provider.label} 网站获取 ↗")
        self.key.set(self.keys[provider.name])
        self.model.set(self.models[provider.name])
        self.model_box.configure(values=self.model_lists[provider.name])
        self.model_hint.configure(text=provider.default_hint)
        self.status.configure(text="")

    def _switch(self, name):
        # 先记下当前服务里改过的内容，再换成新服务的
        self.keys[self.provider.name] = self.key.get().strip()
        self.models[self.provider.name] = self.model.get().strip()
        self._show(ai_providers.get(name))
        self._show_fallbacks()  # 密钥情况可能变了

    # ---- 备用模型

    def _fb_provider(self):
        return ai_providers.get(self.labels[self.fb_provider_var.get()])

    def _key_for(self, provider):
        if provider is self.provider:
            return self.key.get().strip()
        return self.keys[provider.name]

    def _fill_fb_models(self):
        """打开下拉框时填模型列表；还没取过就在后台取（OpenRouter 不需要密钥，其他服务要先填密钥）。"""
        provider = self._fb_provider()
        models = self.model_lists[provider.name]
        self.fb_model_box.configure(values=models)
        key = self._key_for(provider)
        if not models and (key or provider.name == "openrouter"):
            self.status.configure(text=f"正在获取 {provider.label} 的模型列表…")
            self._background("fb_models", lambda: provider.list_models(key), provider)

    def _show_fallbacks(self):
        selected = self._selected_fallback()
        self.fb_list.delete(0, "end")
        for name, model in self.fallbacks:
            provider = ai_providers.get(name)
            missing = "" if self._key_for(provider) else "（没填密钥，会跳过）"
            self.fb_list.insert("end", f"{provider.label} · {model or '默认模型'}{missing}")
        if selected is not None and selected < len(self.fallbacks):
            self.fb_list.selection_set(selected)

    def _add_fallback(self):
        entry = (self._fb_provider().name, self.fb_model.get().strip())
        if entry in self.fallbacks:
            self.status.configure(text="这个模型已经在备用列表里了。")
            return
        self.fallbacks.append(entry)
        self._show_fallbacks()
        self.fb_list.selection_set("end")

    def _selected_fallback(self):
        selection = self.fb_list.curselection()
        return selection[0] if selection else None

    def _move_fallback(self, step):
        k = self._selected_fallback()
        if k is None or not 0 <= k + step < len(self.fallbacks):
            return
        self.fallbacks[k], self.fallbacks[k + step] = self.fallbacks[k + step], self.fallbacks[k]
        self._show_fallbacks()
        self.fb_list.selection_set(k + step)

    def _remove_fallback(self):
        k = self._selected_fallback()
        if k is not None:
            del self.fallbacks[k]
            self._show_fallbacks()

    def _toggle_reveal(self):
        self.key_entry.configure(show="" if self.reveal.get() else "•")

    def _background(self, label, func, provider=None):
        """在后台线程里跑耗时的网络请求，完成后把 (label, 服务, 结果) 交给 Tk 线程。"""
        provider = provider or self.provider

        def work():
            try:
                self.results.put((label, provider, func()))
            except Exception as e:
                self.results.put((label, provider, e))
        threading.Thread(target=work, daemon=True).start()

    def _refresh_models(self):
        key, provider = self.key.get().strip(), self.provider
        if not key and provider.name != "openrouter":  # OpenRouter 的模型列表不需要密钥
            self.status.configure(text="请先填 API 密钥。")
            return
        self.status.configure(text="正在获取模型列表…")
        self._background("models", lambda: provider.list_models(key))

    def _test(self):
        key, model, provider = self.key.get().strip(), self.model.get().strip(), self.provider
        if not key:
            self.status.configure(text="请先填 API 密钥。")
            return
        self.status.configure(text="正在测试…")

        def run():
            chosen = model or provider.default_model(key)
            if not chosen:
                return False, "没有可用的默认模型，请先选一个。"
            ok, message = provider.test(key, chosen)
            return ok, message if model else f"{message}（默认模型：{chosen}）"
        self._background("test", run)

    def _poll(self):
        while not self.results.empty():
            label, provider, result = self.results.get_nowait()
            if label in ("models", "fb_models") and not isinstance(result, Exception):
                self.model_lists[provider.name] = result
            if label == "fb_models":
                if isinstance(result, Exception):
                    self.status.configure(text=ai_providers.friendly_error(result, "（默认模型）", provider))
                else:
                    self.status.configure(text=f"{provider.label}：找到 {len(result)} 个模型。")
                    if provider is self._fb_provider():
                        self.fb_model_box.configure(values=result)
                continue
            if provider is not self.provider:
                continue  # 已经切到别的服务了
            if isinstance(result, Exception):
                self.status.configure(text=ai_providers.friendly_error(result, self.model.get() or "（默认模型）",
                                                                       provider))
            elif label == "models":
                self.model_box.configure(values=result)
                kind = "免费模型" if provider.name == "openrouter" else "可用的聊天模型"
                self.status.configure(text=f"找到 {len(result)} 个{kind}，点下拉框选择。")
                if result and not self.model.get():
                    self.model_box.event_generate("<Down>")
            elif label == "test":
                ok, message = result
                self.status.configure(text=("✓ " if ok else "✗ ") + message)
        self.root.after(100, self._poll)

    def _save(self):
        self.keys[self.provider.name] = self.key.get().strip()
        self.models[self.provider.name] = self.model.get().strip()
        for provider in ai_providers.PROVIDERS.values():
            key = self.keys[provider.name]
            # 密钥来自环境变量时不写进配置，免得环境变量改了配置里还是旧的
            from_env = key and not self.cfg.get(provider.key_field) and key == jp_ai.api_key({}, provider)
            self.cfg[provider.key_field] = jp_ai.protect(key) if key and not from_env else ""
            self.cfg[provider.model_field] = self.models[provider.name]
        self.cfg["ai_provider"] = self.provider.name
        self.cfg["ai_fallbacks"] = [{"provider": name, "model": model} for name, model in self.fallbacks]
        self.cfg["ai_auto"] = self.auto.get()
        self.save_config(self.cfg)
        self.root.destroy()
