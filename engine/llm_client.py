"""OpenRouter / OpenAI-compatible client for the narrative layer.

Responsibilities:

* async HTTP (``httpx.AsyncClient``) with a 30s timeout;
* retries with exponential backoff on 429/5xx;
* a response cache keyed by a hash of the request;
* structured logging to ``logs/llm_YYYYMMDD.log``;
* a ``litellm`` fallback router when the primary call fails;
* graceful degradation: any failure returns ``None`` so callers fall back to
  deterministic templates (the engine must stay playable offline).

This client is used by ``intent_parser`` and ``narrator`` **only**. It never
sees the RNG seed, the full state or the canon log.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from engine.config import Settings, get_settings


class LLMUnavailable(RuntimeError):
    """Raised only when a caller explicitly requires an online response."""


class LLMClient:
    """Thin async chat-completions client with cache, retries and fallbacks."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._http = http_client
        self._cache: dict[str, str] = {}

    # -- state ---------------------------------------------------------------

    @property
    def offline(self) -> bool:
        return self.settings.effective_offline

    @property
    def cache_size(self) -> int:
        return len(self._cache)

    # -- transport -----------------------------------------------------------

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=self.settings.llm_timeout,
                headers={
                    "Authorization": f"Bearer {self.settings.effective_api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._http

    async def _post(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Send one chat-completions request. Overridden in tests."""
        http = await self._get_http()
        url = self.settings.effective_base_url.rstrip("/") + "/chat/completions"
        response = await http.post(url, json=payload)
        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text[:500]}
        return response.status_code, data

    async def list_models(self) -> list[str]:
        """Fetch the model ids offered by the configured endpoint (0 tokens)."""
        if self.offline:
            return []
        try:
            http = await self._get_http()
            url = self.settings.effective_base_url.rstrip("/") + "/models"
            response = await http.get(url)
            data = response.json()
            entries = data.get("data") if isinstance(data, dict) else None
            if not isinstance(entries, list):
                return []
            models: list[str] = []
            for entry in entries:
                if isinstance(entry, dict) and entry.get("id"):
                    models.append(str(entry["id"]))
            return models
        except Exception as exc:
            self._log("error", f"list_models failed: {exc}")
            return []

    # -- completion ----------------------------------------------------------

    def _cache_key(self, model: str, messages: list[dict[str, str]], response_format: Any) -> str:
        blob = json.dumps(
            {"model": model, "messages": messages, "response_format": response_format},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.8,
        max_tokens: int | None = None,
        cache: bool = True,
    ) -> str | None:
        """Return the assistant message, or ``None`` when unavailable."""
        if self.offline:
            return None

        model = model or self.settings.effective_model
        key = self._cache_key(model, messages, response_format)
        if cache and key in self._cache:
            self._log("cache", f"model={model}")
            return self._cache[key]

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        attempts = max(1, int(self.settings.llm_retries))
        delay = 1.0
        provider_unreachable = True
        for attempt in range(1, attempts + 1):
            try:
                status, data = await self._post(payload)
            except Exception as exc:  # network error
                self._log("error", f"attempt {attempt} transport error: {exc}")
                status, data = 0, {}

            if status == 200:
                content = _extract_content(data)
                if content:
                    if cache:
                        self._cache[key] = content
                    self._log("ok", f"model={model} chars={len(content)}")
                    return content
                self._log("warn", "empty completion")
            elif status == 429 or 500 <= status < 600:
                self._log("warn", f"http {status}, backing off")
            else:
                # 400/401/403/404/422: a definitive answer (bad model, bad key,
                # bad request). Retrying or routing elsewhere will not help.
                provider_unreachable = False
                self._log("error", f"http {status}: {str(data)[:200]}")
                break

            if attempt < attempts:
                await asyncio.sleep(delay)
                delay *= 2

        if not provider_unreachable:
            return None
        content = await self._litellm_fallback(model, messages, temperature, response_format, max_tokens)
        if content:
            if cache:
                self._cache[key] = content
            return content
        return None

    async def _litellm_fallback(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        response_format: dict[str, Any] | None,
        max_tokens: int | None = None,
    ) -> str | None:
        try:
            import litellm  # type: ignore

            litellm.suppress_debug_info = True
        except Exception:
            return None
        try:
            response = await litellm.acompletion(
                model=model,
                messages=messages,
                temperature=temperature,
                response_format=response_format,
                max_tokens=max_tokens,
                api_key=self.settings.effective_api_key,
                base_url=self.settings.effective_base_url,
            )
            return response["choices"][0]["message"]["content"]
        except Exception as exc:  # pragma: no cover - depends on litellm
            self._log("error", f"litellm fallback failed: {exc}")
            return None

    # -- lifecycle -----------------------------------------------------------

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # -- logging -------------------------------------------------------------

    def _log(self, level: str, message: str) -> None:
        try:
            directory: Path = self.settings.logs_path
            directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d")
            line = f"{datetime.now().isoformat(timespec='seconds')} [{level}] {message}\n"
            with (directory / f"llm_{stamp}.log").open("a", encoding="utf-8") as handle:
                handle.write(line)
        except Exception:
            pass  # logging must never break the game


def _extract_content(data: dict[str, Any]) -> str | None:
    try:
        choices = data.get("choices") or []
        if not choices:
            return None
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    except Exception:
        return None
    return None
