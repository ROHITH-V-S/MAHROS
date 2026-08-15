"""Provider-agnostic LLM client. Free options only.

Providers, in the order you should try them:

  * ``ollama``  -- fully local, free forever, no key, no rate limit, works
                   offline. Install from ollama.com, then ``ollama pull llama3.2:3b``.
                   This is the right default for a student project: reproducible
                   and you can run 10,000 explanations without a bill.
  * ``groq``    -- generous free tier, very fast. Needs GROQ_API_KEY.
  * ``gemini``  -- free tier. Needs GEMINI_API_KEY.
  * ``none``    -- deterministic template explainer. Always available, and what
                   CI and the headline experiments run on.

Uses urllib from the stdlib so there is no extra dependency to install.
Every call is cached on the prompt hash: identical negotiations do not burn
quota twice, and a cached run is reproducible.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field


@dataclass
class LLMResponse:
    text: str
    provider: str
    cached: bool = False
    ok: bool = True
    error: str = ""
    latency_ms: float = 0.0


@dataclass
class LLMConfig:
    provider: str = "none"               # none | ollama | groq | gemini
    model: str = ""
    temperature: float = 0.0             # determinism matters for reproducibility
    max_tokens: int = 400
    timeout_s: float = 30.0
    ollama_host: str = "http://localhost:11434"

    @classmethod
    def from_env(cls) -> "LLMConfig":
        provider = os.getenv("MAHROS_LLM_PROVIDER", "none").lower()
        defaults = {
            "ollama": "llama3.2:3b",
            "groq": "llama-3.3-70b-versatile",
            "gemini": "gemini-2.0-flash",
            "none": "",
        }
        return cls(
            provider=provider,
            model=os.getenv("MAHROS_LLM_MODEL", defaults.get(provider, "")),
            temperature=float(os.getenv("MAHROS_LLM_TEMPERATURE", "0.0")),
        )


class LLMClient:
    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig.from_env()
        self._cache: dict[str, str] = {}
        self.calls = 0
        self.cache_hits = 0
        self.failures = 0

    @property
    def available(self) -> bool:
        return self.config.provider != "none"

    def complete(self, system: str, user: str) -> LLMResponse:
        if not self.available:
            return LLMResponse("", "none", ok=False, error="no provider configured")

        key = hashlib.sha256(
            f"{self.config.provider}|{self.config.model}|{system}|{user}".encode()
        ).hexdigest()
        if key in self._cache:
            self.cache_hits += 1
            return LLMResponse(self._cache[key], self.config.provider, cached=True)

        import time
        t0 = time.perf_counter()
        try:
            text = self._dispatch(system, user)
            self._cache[key] = text
            self.calls += 1
            return LLMResponse(
                text, self.config.provider, latency_ms=(time.perf_counter() - t0) * 1000
            )
        except Exception as exc:
            self.failures += 1
            return LLMResponse("", self.config.provider, ok=False, error=str(exc))

    # -- providers --------------------------------------------------------- #
    def _dispatch(self, system: str, user: str) -> str:
        p = self.config.provider
        if p == "ollama":
            return self._ollama(system, user)
        if p == "groq":
            return self._groq(system, user)
        if p == "gemini":
            return self._gemini(system, user)
        raise ValueError(f"unknown provider {p!r}")

    def _post(self, url: str, payload: dict, headers: dict | None = None) -> dict:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.config.timeout_s) as resp:
            return json.loads(resp.read().decode())

    def _ollama(self, system: str, user: str) -> str:
        data = self._post(
            f"{self.config.ollama_host}/api/chat",
            {
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "options": {
                    "temperature": self.config.temperature,
                    "num_predict": self.config.max_tokens,
                    "seed": 42,
                },
            },
        )
        return data["message"]["content"]

    def _groq(self, system: str, user: str) -> str:
        key = os.getenv("GROQ_API_KEY")
        if not key:
            raise RuntimeError("GROQ_API_KEY not set")
        data = self._post(
            "https://api.groq.com/openai/v1/chat/completions",
            {
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
            },
            {"Authorization": f"Bearer {key}"},
        )
        return data["choices"][0]["message"]["content"]

    def _gemini(self, system: str, user: str) -> str:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY not set")
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.config.model}:generateContent?key={key}"
        )
        data = self._post(
            url,
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {
                    "temperature": self.config.temperature,
                    "maxOutputTokens": self.config.max_tokens,
                },
            },
        )
        return data["candidates"][0]["content"]["parts"][0]["text"]

    def stats(self) -> dict:
        return {
            "provider": self.config.provider,
            "model": self.config.model,
            "live_calls": self.calls,
            "cache_hits": self.cache_hits,
            "failures": self.failures,
        }
