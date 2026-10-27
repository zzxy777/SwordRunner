from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class LLMProvider:
    name: str
    base_url: str
    model: str
    api_key: str
    timeout_s: float
    temperature: float = 0.1


class LLMClient:
    def __init__(self, providers: list[LLMProvider]):
        self.providers = providers

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "LLMClient":
        providers: list[LLMProvider] = []
        for key in ("primary", "fallback"):
            raw = (cfg.get("llm") or {}).get(key) or {}
            api_key = str(raw.get("api_key") or "").strip()
            env_name = str(raw.get("api_key_env") or "").strip()
            if not api_key and env_name:
                api_key = os.environ.get(env_name, "").strip()
            providers.append(
                LLMProvider(
                    name=str(raw.get("name") or key),
                    base_url=str(raw.get("base_url") or "").rstrip("/"),
                    model=str(raw.get("model") or ""),
                    api_key=api_key,
                    timeout_s=float(raw.get("timeout_s") or 60),
                    temperature=float(raw.get("temperature") if raw.get("temperature") is not None else 0.1),
                )
            )
        return cls(providers)

    def chat(self, system: str, user: str, *, temperature: float | None = None) -> str:
        errors: list[str] = []
        for provider in self.providers:
            if not provider.base_url or not provider.model:
                errors.append(f"{provider.name}: missing base_url/model")
                continue
            if not provider.api_key:
                errors.append(f"{provider.name}: missing api key")
                continue
            try:
                return self._chat_provider(provider, system, user, temperature=temperature)
            except Exception as exc:
                errors.append(f"{provider.name}: {exc}")
        raise RuntimeError("all LLM providers failed: " + " | ".join(errors))

    def _chat_provider(self, provider: LLMProvider, system: str, user: str, *, temperature: float | None = None) -> str:
        payload = {
            "model": provider.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": provider.temperature if temperature is None else temperature,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            provider.base_url + "/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {provider.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=provider.timeout_s) as response:
                data = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")[:800]
            raise RuntimeError(f"http {exc.code}: {text}") from exc

        try:
            return str(data["choices"][0]["message"]["content"]).strip()
        except Exception as exc:
            raise RuntimeError(f"unexpected response shape: {json.dumps(data, ensure_ascii=False)[:800]}") from exc


def extract_json_array(text: str) -> list[Any]:
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("[")
        end = raw.rfind("]")
        if start < 0 or end <= start:
            raise
        data = json.loads(raw[start : end + 1])
    if not isinstance(data, list):
        raise RuntimeError("LLM output is not a JSON array")
    return data

