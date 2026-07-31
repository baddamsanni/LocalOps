"""LLM router: plain-text respond() + streaming respond_stream()."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from config import LLMConfig
from platform_info import HostPlatform, build_platform_prompt, detect_host_platform

SYSTEM_PROMPT = """
You are a helpful assistant chatting with a developer via Telegram.
You can have normal conversations, AND you can run shell commands on
their laptop when they need you to check or change something on
their system.

Decide for yourself, based on what they're asking, whether you need
to run a command:

- If the user is asking a general question, wants to chat, or wants
  an explanation — just answer normally in plain text.

- If the user wants you to check their system, run something, or
  take an action on their machine — respond with EXACTLY this format
  and nothing else, no explanation before or after:
  CMD: <the exact shell command>

Examples:
User: "what's my disk space?"
You: CMD: df -h

User: "explain what a memory leak is"
You: A memory leak happens when a program keeps allocating memory
     without releasing it back to the system...

User: "run the tests"
You: CMD: mvn test

User: "hello"
You: Hey! What can I help you with?

Only ever use the CMD: format when a command is actually needed.
Never use it for questions, explanations, or conversation.

When the user asks to navigate/go to/cd into a folder, ALWAYS respond
with a plain `CMD: cd <path>` — nothing else. NEVER combine it with
mkdir, NEVER create the folder if it doesn't exist, NEVER use compound
commands like `mkdir -p ... && cd ...`. If the folder turns out not to
exist, that will be reported as an error — that is the correct and
expected behavior. Do not try to work around it by creating the folder.
""".strip()


class LLMProvider(ABC):
    @abstractmethod
    async def respond(
        self,
        user_message: str,
        context: list[dict[str, str]],
        *,
        system_prompt: str | None = None,
        working_directory: str | None = None,
    ) -> str:
        """Complete plain-text reply (may be CMD: …)."""

    @abstractmethod
    async def respond_stream(
        self,
        user_message: str,
        context: list[dict[str, str]],
        *,
        system_prompt: str | None = None,
        working_directory: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Yield text chunks as they arrive."""
        if False:  # pragma: no cover
            yield ""


def _cwd_prompt_block(working_directory: str) -> str:
    return (
        f"The user's current working directory is: {working_directory}\n"
        f"If they ask to navigate somewhere, resolve paths relative to this\n"
        f"location. If they ask you to do something in their current location\n"
        f"without specifying a path, assume they mean {working_directory}."
    )


def _build_messages(
    user_message: str,
    context: list[dict[str, str]],
    working_directory: str,
    *,
    system_prompt: str | None = None,
    host_platform: HostPlatform | None = None,
) -> list[dict[str, str]]:
    if system_prompt is not None:
        # Explicit override (e.g. explain step) — do not add CMD platform guide
        base = system_prompt
    else:
        host = host_platform or detect_host_platform()
        base = f"{build_platform_prompt(host)}\n\n{SYSTEM_PROMPT}"
    system = f"{base}\n\n{_cwd_prompt_block(working_directory)}"
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
    ]
    for item in context[-10:]:
        role = item.get("role", "user")
        content = item.get("content", "")
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})
    return messages


def _claude_text(response: Any) -> str:
    text = ""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            text += block.text
        elif isinstance(block, dict) and block.get("type") == "text":
            text += block.get("text", "")
    return text


class ClaudeProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        working_directory: str,
        client: Any | None = None,
        host_platform: HostPlatform | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.working_directory = working_directory
        self.host_platform = host_platform or detect_host_platform()
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        import anthropic

        self._client = anthropic.AsyncAnthropic(api_key=self.api_key)
        return self._client

    def _chat_parts(
        self,
        user_message: str,
        context: list[dict[str, str]],
        *,
        system_prompt: str | None = None,
        working_directory: str | None = None,
    ) -> tuple[str, list[dict[str, str]]]:
        cwd = (
            working_directory
            if working_directory is not None
            else self.working_directory
        )
        messages = _build_messages(
            user_message,
            context,
            cwd,
            system_prompt=system_prompt,
            host_platform=self.host_platform,
        )
        system = messages[0]["content"]
        chat = [
            {"role": m["role"], "content": m["content"]}
            for m in messages[1:]
            if m["role"] in {"user", "assistant"}
        ]
        return system, chat

    async def respond(
        self,
        user_message: str,
        context: list[dict[str, str]],
        *,
        system_prompt: str | None = None,
        working_directory: str | None = None,
    ) -> str:
        system, chat = self._chat_parts(
            user_message,
            context,
            system_prompt=system_prompt,
            working_directory=working_directory,
        )
        client = self._get_client()
        response = await client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system,
            messages=chat,
        )
        return _claude_text(response).strip()

    async def respond_stream(
        self,
        user_message: str,
        context: list[dict[str, str]],
        *,
        system_prompt: str | None = None,
        working_directory: str | None = None,
    ) -> AsyncGenerator[str, None]:
        system, chat = self._chat_parts(
            user_message,
            context,
            system_prompt=system_prompt,
            working_directory=working_directory,
        )
        client = self._get_client()
        async with client.messages.stream(
            model=self.model,
            max_tokens=1024,
            system=system,
            messages=chat,
        ) as stream:
            async for text in stream.text_stream:
                if text:
                    yield text


class OllamaProvider(LLMProvider):
    def __init__(
        self,
        base_url: str,
        model: str,
        working_directory: str,
        http_client: httpx.AsyncClient | None = None,
        host_platform: HostPlatform | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.working_directory = working_directory
        self.host_platform = host_platform or detect_host_platform()
        self._http_client = http_client

    async def respond(
        self,
        user_message: str,
        context: list[dict[str, str]],
        *,
        system_prompt: str | None = None,
        working_directory: str | None = None,
    ) -> str:
        cwd = (
            working_directory
            if working_directory is not None
            else self.working_directory
        )
        messages = _build_messages(
            user_message,
            context,
            cwd,
            system_prompt=system_prompt,
            host_platform=self.host_platform,
        )
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        url = f"{self.base_url}/api/chat"
        if self._http_client is not None:
            response = await self._http_client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
        else:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
        message = data.get("message") or {}
        return (message.get("content") or data.get("response") or "").strip()

    async def respond_stream(
        self,
        user_message: str,
        context: list[dict[str, str]],
        *,
        system_prompt: str | None = None,
        working_directory: str | None = None,
    ) -> AsyncGenerator[str, None]:
        cwd = (
            working_directory
            if working_directory is not None
            else self.working_directory
        )
        messages = _build_messages(
            user_message,
            context,
            cwd,
            system_prompt=system_prompt,
            host_platform=self.host_platform,
        )
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        url = f"{self.base_url}/api/chat"

        async def _iter_lines(
            client: httpx.AsyncClient,
        ) -> AsyncGenerator[str, None]:
            async with client.stream("POST", url, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    message = data.get("message") or {}
                    content = message.get("content") or ""
                    if content:
                        yield content

        if self._http_client is not None:
            async for chunk in _iter_lines(self._http_client):
                yield chunk
        else:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async for chunk in _iter_lines(client):
                    yield chunk


class OpenAIProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        working_directory: str,
        http_client: httpx.AsyncClient | None = None,
        host_platform: HostPlatform | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.working_directory = working_directory
        self.host_platform = host_platform or detect_host_platform()
        self._http_client = http_client

    async def respond(
        self,
        user_message: str,
        context: list[dict[str, str]],
        *,
        system_prompt: str | None = None,
        working_directory: str | None = None,
    ) -> str:
        cwd = (
            working_directory
            if working_directory is not None
            else self.working_directory
        )
        messages = _build_messages(
            user_message,
            context,
            cwd,
            system_prompt=system_prompt,
            host_platform=self.host_platform,
        )
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
        }
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self._http_client is not None:
            response = await self._http_client.post(
                url, json=payload, headers=headers
            )
            response.raise_for_status()
            data = response.json()
        else:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    url, json=payload, headers=headers
                )
                response.raise_for_status()
                data = response.json()
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return (message.get("content") or "").strip()

    async def respond_stream(
        self,
        user_message: str,
        context: list[dict[str, str]],
        *,
        system_prompt: str | None = None,
        working_directory: str | None = None,
    ) -> AsyncGenerator[str, None]:
        cwd = (
            working_directory
            if working_directory is not None
            else self.working_directory
        )
        messages = _build_messages(
            user_message,
            context,
            cwd,
            system_prompt=system_prompt,
            host_platform=self.host_platform,
        )
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "stream": True,
        }
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async def _iter_sse(
            client: httpx.AsyncClient,
        ) -> AsyncGenerator[str, None]:
            async with client.stream(
                "POST", url, json=payload, headers=headers
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content") or ""
                    if content:
                        yield content

        if self._http_client is not None:
            async for chunk in _iter_sse(self._http_client):
                yield chunk
        else:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async for chunk in _iter_sse(client):
                    yield chunk


class LLMRouter:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def respond_stream(
        self,
        user_message: str,
        context: list[dict[str, str]],
        *,
        system_prompt: str | None = None,
        working_directory: str | None = None,
    ) -> AsyncGenerator[str, None]:
        try:
            async for chunk in self.provider.respond_stream(
                user_message,
                context,
                system_prompt=system_prompt,
                working_directory=working_directory,
            ):
                yield chunk
        except Exception as exc:  # noqa: BLE001
            yield f"LLM request failed: {exc}"

    async def respond(
        self,
        user_message: str,
        context: list[dict[str, str]],
        *,
        system_prompt: str | None = None,
        working_directory: str | None = None,
    ) -> str:
        try:
            return await self.provider.respond(
                user_message,
                context,
                system_prompt=system_prompt,
                working_directory=working_directory,
            )
        except Exception as exc:  # noqa: BLE001
            return f"LLM request failed: {exc}"


def build_router(
    llm_config: LLMConfig,
    working_directory: str,
    host_platform: HostPlatform | None = None,
) -> LLMRouter:
    host = host_platform or detect_host_platform()
    provider_name = llm_config.provider
    if provider_name == "claude":
        provider: LLMProvider = ClaudeProvider(
            api_key=llm_config.claude.api_key,
            model=llm_config.claude.model,
            working_directory=working_directory,
            host_platform=host,
        )
    elif provider_name == "ollama":
        provider = OllamaProvider(
            base_url=llm_config.ollama.base_url,
            model=llm_config.ollama.model,
            working_directory=working_directory,
            host_platform=host,
        )
    elif provider_name == "openai":
        provider = OpenAIProvider(
            api_key=llm_config.openai.api_key,
            model=llm_config.openai.model,
            base_url=llm_config.openai.base_url,
            working_directory=working_directory,
            host_platform=host,
        )
    else:
        raise ValueError(
            f"llm.provider must be one of: claude, ollama, openai. "
            f"Got: {provider_name!r}"
        )
    return LLMRouter(provider)
