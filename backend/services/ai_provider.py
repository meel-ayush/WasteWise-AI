import os
import time
import json
import re
import threading
import requests as http_requests
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY", "")


GEMINI_MODELS = [
    "gemini-2.5-flash-lite",
    "gemini-flash-latest",
    "gemini-3-flash-preview",
    "gemini-flash-lite-latest",
]

GROQ_MODEL = "llama-3.3-70b-versatile"
MISTRAL_MODEL = "mistral-small-latest"

_rl_lock = threading.Lock()
_rate_limited_until: dict = {}


def _is_rl(model_id: str) -> bool:
    with _rl_lock:
        return time.monotonic() < _rate_limited_until.get(model_id, 0)


def _mark_rl(model_id: str, seconds: int = 70) -> None:
    with _rl_lock:
        _rate_limited_until[model_id] = time.monotonic() + seconds


def _retry_secs(err_str: str) -> int:
    m = re.search(r"retry.*?(\d+).*?second", err_str, re.I)
    return int(m.group(1)) + 5 if m else 70


def _call_gemini(prompt: str, json_mode: bool = False) -> Optional[str]:
    if not GEMINI_API_KEY:
        return None
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"[Gemini] SDK init: {e }")
        return None

    for model_name in GEMINI_MODELS:
        if _is_rl(model_name):
            continue
        try:
            cfg = (
                types.GenerateContentConfig(response_mime_type="application/json")
                if json_mode
                else None
            )
            kwargs = {"model": model_name, "contents": prompt}
            if cfg:
                kwargs["config"] = cfg
            resp = client.models.generate_content(**kwargs)
            text = resp.text
            if text:
                return text.strip()
        except Exception as e:
            err = str(e)
            if "429" in err or "quota" in err.lower():
                _mark_rl(model_name, _retry_secs(err))
            elif "403" in err or "forbidden" in err.lower():
                _mark_rl(model_name, 3600)
            else:
                print(f"[Gemini] {model_name }: {err [:80 ]}")
    return None


def _call_gemini_with_image(
    prompt: str, image_bytes: bytes, mime_type: str = "image/jpeg"
) -> Optional[str]:
    """Send image + text to Gemini Vision."""
    if not GEMINI_API_KEY:
        return None
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception:
        return None

    for model_name in GEMINI_MODELS:
        if _is_rl(model_name):
            continue
        try:
            import base64

            image_data = base64.standard_b64encode(image_bytes).decode("utf-8")
            contents = [
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                prompt,
            ]
            resp = client.models.generate_content(model=model_name, contents=contents)
            if resp.text:
                return resp.text.strip()
        except Exception as e:
            err = str(e)
            if "429" in err or "quota" in err.lower():
                _mark_rl(model_name, _retry_secs(err))
            elif "403" in err or "forbidden" in err.lower():
                _mark_rl(model_name, 3600)
    return None


def _call_groq(prompt: str, json_mode: bool = False) -> Optional[str]:
    if not GROQ_API_KEY or _is_rl("groq"):
        return None
    try:
        from groq import Groq

        client = Groq(api_key=GROQ_API_KEY)
        kwargs = {
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2048,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content
        return text.strip() if text else None
    except Exception as e:
        err = str(e)
        if "429" in err or "rate" in err.lower():
            _mark_rl("groq", 30)
        elif "403" in err or "connection" in err.lower() or "proxy" in err.lower():
            _mark_rl("groq", 3600)
        else:
            print(f"[Groq] {err [:80 ]}")
        return None


def _call_mistral_http(prompt: str, json_mode: bool = False) -> Optional[str]:
    """Use HTTP directly — avoids pydantic version conflict from the SDK."""
    if not MISTRAL_API_KEY or _is_rl("mistral"):
        return None
    try:
        payload = {
            "model": MISTRAL_MODEL,
            "messages": [{"role": "user", "content": prompt}],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        resp = http_requests.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {MISTRAL_API_KEY }",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=15,
        )
        if resp.status_code == 200:
            text = resp.json()["choices"][0]["message"]["content"]
            return text.strip() if text else None
        elif resp.status_code == 429:
            _mark_rl("mistral", 30)
        else:
            _mark_rl("mistral", 3600)
    except Exception as e:
        err = str(e)
        if "proxy" in err.lower() or "connection" in err.lower():
            _mark_rl("mistral", 3600)
        else:
            print(f"[Mistral] {err [:80 ]}")
    return None


def call_ai(prompt: str, json_mode: bool = False) -> Optional[str]:
    result = _call_gemini(prompt, json_mode)
    if result:
        return result
    result = _call_groq(prompt, json_mode)
    if result:
        return result
    result = _call_mistral_http(prompt, json_mode)
    if result:
        return result
    return None


def call_ai_json(prompt: str) -> Optional[dict]:
    raw = call_ai(prompt, json_mode=True)
    if not raw:
        return None
    text = raw.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fallback: extract first complete JSON object using regex
    match = re.search(r'\{.*?\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as e:
            print(f"[AI] JSON parse error: {e} | raw[:100]: {text[:100]}")
    return None



def call_ai_with_image(
    prompt: str, image_bytes: bytes, mime_type: str = "image/jpeg"
) -> Optional[str]:
    """Vision call — only Gemini supports image input."""
    return _call_gemini_with_image(prompt, image_bytes, mime_type)


def ai_available() -> bool:
    return bool(GEMINI_API_KEY or GROQ_API_KEY or MISTRAL_API_KEY)
