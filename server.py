# -*- coding: utf-8 -*-
"""PaperLens Flask backend."""
import json
import os
import queue
import sys
import threading
import traceback

from flask import Flask, Response, jsonify, request, send_from_directory

import core


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE = app_dir()
SETTINGS_FILE = os.path.join(BASE, "settings.json")
OUT_DIR = os.path.join(BASE, "reports")
os.makedirs(os.path.join(OUT_DIR, "pdfs"), exist_ok=True)


def resource_dir():
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "static")
    return os.path.join(BASE, "static")


app = Flask(__name__, static_folder=resource_dir(), static_url_path="/static")

DEFAULT_SETTINGS = {
    "key_anthropic": "",
    "key_openai": "",
    "key_deepseek": "",
    "key_qwen": "",
    "key_kimi": "",
    "base_url_anthropic": "",
    "base_url_openai": "",
    "base_url_deepseek": "",
    "base_url_qwen": "",
    "base_url_kimi": "",
    "custom_models_text": "",
    "tier_cheap": "anthropic:claude-haiku-4-5-20251001",
    "tier_main": "anthropic:claude-sonnet-4-6",
    "tier_premium": "anthropic:claude-opus-4-8",
    "semantic_scholar_key": "",
    "arxiv_mirror": "",
    "smtp_host": "smtp.qq.com",
    "smtp_port": "465",
    "smtp_user": "",
    "smtp_password": "",
}

MODEL_CATALOG = {
    "anthropic": ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o3-mini"],
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
    "qwen": ["qwen-max", "qwen-plus", "qwen-turbo"],
    "kimi": ["moonshot-v1-128k", "moonshot-v1-32k", "moonshot-v1-8k"],
}

PROVIDER_LABEL = {
    "anthropic": "Claude",
    "openai": "GPT",
    "deepseek": "DeepSeek",
    "qwen": "通义千问",
    "kimi": "Kimi",
}

KEY_FIELD = {
    "anthropic": "key_anthropic",
    "openai": "key_openai",
    "deepseek": "key_deepseek",
    "qwen": "key_qwen",
    "kimi": "key_kimi",
}

BASE_URL_FIELD = {
    "anthropic": "base_url_anthropic",
    "openai": "base_url_openai",
    "deepseek": "base_url_deepseek",
    "qwen": "base_url_qwen",
    "kimi": "base_url_kimi",
}


def build_model_catalog(settings):
    catalog = {provider: list(models) for provider, models in MODEL_CATALOG.items()}
    for line in str(settings.get("custom_models_text", "")).splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        provider, sep, model = item.partition(":")
        if not sep:
            continue
        provider = provider.strip().lower()
        model = model.strip()
        if provider not in catalog or not model:
            continue
        if model not in catalog[provider]:
            catalog[provider].append(model)
    return catalog


def tier_to_spec(settings, tier_value):
    provider, _, model = tier_value.partition(":")
    api_key = settings.get(KEY_FIELD.get(provider, ""), "")
    base_url = settings.get(BASE_URL_FIELD.get(provider, ""), "").strip()
    return {"provider": provider, "model": model, "api_key": api_key, "base_url": base_url}


def load_settings():
    settings = dict(DEFAULT_SETTINGS)
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                settings.update(json.load(f))
        except Exception:
            pass
    return settings


def save_settings(data):
    settings = load_settings()
    settings.update(data)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    return settings


_progress_q = queue.Queue()
_result_holder = {}


@app.route("/")
def index():
    return send_from_directory(resource_dir(), "index.html")


@app.route("/api/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        save_settings(request.json or {})
        return jsonify({"ok": True})

    settings = load_settings()
    masked = dict(settings)
    for key in (
        "key_anthropic",
        "key_openai",
        "key_deepseek",
        "key_qwen",
        "key_kimi",
        "semantic_scholar_key",
        "smtp_password",
    ):
        value = settings.get(key, "")
        masked[key + "_set"] = bool(value)
        masked[key] = (value[:5] + "•••" + value[-3:]) if len(value) > 10 else ("已设置" if value else "")
    masked["catalog"] = build_model_catalog(settings)
    masked["provider_label"] = PROVIDER_LABEL
    return jsonify(masked)


@app.route("/api/run", methods=["POST"])
def run():
    data = request.json or {}
    settings = load_settings()

    sp_cheap = tier_to_spec(settings, settings["tier_cheap"])
    sp_main = tier_to_spec(settings, settings["tier_main"])
    sp_premium = tier_to_spec(settings, settings["tier_premium"])

    for tier_name, spec in (("检索档", sp_cheap), ("精读档", sp_main), ("综述档", sp_premium)):
        if not spec["api_key"]:
            label = PROVIDER_LABEL.get(spec["provider"], spec["provider"])
            return jsonify({
                "ok": False,
                "error": f"{tier_name}正在使用 {label}（{spec['model']}），但还没有填写 {label} API Key。请打开设置补充。",
            }), 400

    params = {
        "query": data.get("query", "").strip(),
        "num_papers": int(data.get("num_papers", 15)),
        "years_back": int(data.get("years_back", 3)),
    }
    if not params["query"]:
        return jsonify({"ok": False, "error": "请先填写研究需求。"}), 400

    cfg = {
        "spec_cheap": sp_cheap,
        "spec_main": sp_main,
        "spec_premium": sp_premium,
        "semantic_scholar_key": settings.get("semantic_scholar_key", ""),
        "arxiv_mirror": settings.get("arxiv_mirror", ""),
        "out_dir": OUT_DIR,
    }

    while not _progress_q.empty():
        _progress_q.get_nowait()
    _result_holder.clear()

    def worker():
        def progress(pct, stage, desc):
            _progress_q.put({"pct": pct, "stage": stage, "desc": desc})

        try:
            result = core.run_pipeline(cfg, params, progress)
            _result_holder["result"] = result
            _progress_q.put({"pct": 100, "stage": "done", "desc": "完成", "final": True})
        except Exception as exc:
            traceback.print_exc()
            _progress_q.put({"stage": "error", "desc": str(exc), "final": True})

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/progress")
def progress_stream():
    def gen():
        while True:
            event = _progress_q.get()
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event.get("final"):
                if event.get("stage") != "error" and "result" in _result_holder:
                    result_event = {"stage": "result", "result": _result_holder["result"]}
                    yield f"data: {json.dumps(result_event, ensure_ascii=False)}\n\n"
                break

    return Response(gen(), mimetype="text/event-stream")


if __name__ == "__main__":
    app.run(port=7860, threaded=True)
