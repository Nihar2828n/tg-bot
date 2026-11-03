#!/usr/bin/env python3
"""
Telegram bot with OpenRouter LLM + tools (files, shell, web search)
Deployable on Render (polling mode) or run locally.
"""
import os
import asyncio
import json
import subprocess
import tempfile
import shlex
from pathlib import Path
from typing import Optional, List, Dict, Any, Set

import httpx
from dotenv import load_dotenv
from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv()
# Render port binding
import threading
from flask import Flask
PORT = int(os.getenv("PORT", "8080"))
app_flask = Flask(__name__)
@app_flask.route("/")
def health(): return "OK"
threading.Thread(target=lambda: app_flask.run(host="0.0.0.0", port=PORT), daemon=True).start()

# ──────────────────────────────────────────────
# Render port binding (health check)
# ──────────────────────────────────────────────
import threading
from flask import Flask
PORT = int(os.getenv("PORT", "8080"))
app_flask = Flask(__name__)

@app_flask.route("/")
def health():
    return "OK"

@app_flask.route("/health")
def health_check():
    return {"status": "ok"}

# Start Flask in background thread
threading.Thread(target=lambda: app_flask.run(host="0.0.0.0", port=PORT), daemon=True).start()

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Render sets PORT, but polling mode doesn't need it
PORT = int(os.getenv("PORT", "8080"))

# No restrictions - full access
WORK_DIR = Path.cwd().resolve()
MAX_OUTPUT_CHARS = 50000
MAX_FILE_READ_CHARS = 50000
SHELL_TIMEOUT = 120

# ──────────────────────────────────────────────
# System prompt - makes it act like Claude Code
# ──────────────────────────────────────────────
SYSTEM_PROMPT = """You are a helpful AI assistant accessible via Telegram. You have access to tools for:
- Reading/writing files on the server
- Running shell commands
- Searching the web / fetching URLs

Be concise but thorough. Use tools when needed to answer accurately.
When running commands, prefer non-destructive operations first.
If a command fails, explain the error and suggest alternatives.

Working directory: {work_dir}
""".format(work_dir=WORK_DIR)

# ──────────────────────────────────────────────
# Tool definitions for OpenRouter function calling
# ──────────────────────────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the server filesystem",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to working directory"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file on the server",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to working directory"},
                    "content": {"type": "string", "description": "Content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in a directory",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path (relative), default: ."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run a shell command on the server",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Command to run"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 30},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_file",
            "description": "Send a file from the server to the Telegram chat as a document",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to file (absolute or relative to working directory)"},
                    "caption": {"type": "string", "description": "Optional caption for the file"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web via DuckDuckGo HTML scrape (simple, no API key)",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "description": "Max results", "default": 5},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch content from a URL",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"},
                    "max_chars": {"type": "integer", "description": "Max chars to return", "default": 5000},
                },
                "required": ["url"],
            },
        },
    },
]

# ──────────────────────────────────────────────
# Tool implementations
# ──────────────────────────────────────────────
def _safe_path(rel_path: str) -> Path:
    """Resolve path - allow absolute paths, no traversal restriction."""
    # If absolute path, use as-is; otherwise relative to WORK_DIR
    if os.path.isabs(rel_path):
        return Path(rel_path)
    return (WORK_DIR / rel_path).resolve()


async def tool_read_file(path: str) -> str:
    p = _safe_path(path)
    if not p.exists():
        return f"Error: File not found: {path}"
    if not p.is_file():
        return f"Error: Not a file: {path}"
    content = await asyncio.to_thread(p.read_text, encoding="utf-8", errors="replace")
    if len(content) > MAX_FILE_READ_CHARS:
        content = content[:MAX_FILE_READ_CHARS] + f"\n... [truncated, {len(content)} total chars]"
    return content


async def tool_write_file(path: str, content: str) -> str:
    p = _safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(p.write_text, content, encoding="utf-8")
    return f"Written {len(content)} chars to {path}"


async def tool_list_files(path: str = ".") -> str:
    p = _safe_path(path)
    if not p.exists():
        return f"Error: Directory not found: {path}"
    if not p.is_dir():
        return f"Error: Not a directory: {path}"
    items = []
    for item in sorted(p.iterdir()):
        prefix = "📁 " if item.is_dir() else "📄 "
        size = item.stat().st_size if item.is_file() else 0
        items.append(f"{prefix}{item.name} ({size} bytes)")
    return "\n".join(items) if items else "(empty)"


async def tool_send_file(update: Update, path: str, caption: str = "") -> str:
    """Send a file as a Telegram document."""
    p = _safe_path(path)
    if not p.exists():
        return f"Error: File not found: {path}"
    if not p.is_file():
        return f"Error: Not a file: {path}"

    try:
        from telegram import InputFile
        with p.open("rb") as f:
            await update.message.reply_document(
                document=InputFile(f, filename=p.name),
                caption=caption or f"File: {p.name}"
            )
        return f"✅ Sent file: {p.name}"
    except Exception as e:
        return f"Error sending file: {e}"


async def tool_run_shell(command: str, timeout: int = SHELL_TIMEOUT) -> str:
    try:
        # Run in WORK_DIR
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=WORK_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return f"Error: Command timed out after {timeout}s"

        out = stdout.decode(errors="replace")
        err = stderr.decode(errors="replace")
        result = f"Exit code: {proc.returncode}\n"
        if out:
            result += f"STDOUT:\n{out}"
        if err:
            result += f"STDERR:\n{err}"
        if len(result) > MAX_OUTPUT_CHARS:
            result = result[:MAX_OUTPUT_CHARS] + f"\n... [truncated]"
        return result
    except Exception as e:
        return f"Error running command: {e}"


async def tool_web_search(query: str, max_results: int = 5) -> str:
    """Simple DuckDuckGo HTML scrape - no API key needed."""
    url = "https://html.duckduckgo.com/html/"
    params = {"q": query}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; TelegramBot/1.0)"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, data=params, headers=headers)
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for result in soup.select(".result__snippet")[:max_results]:
            text = result.get_text(strip=True)
            if text:
                results.append(text)
        return "\n\n".join(results) if results else "No results found."
    except Exception as e:
        return f"Search error: {e}"


async def tool_fetch_url(url: str, max_chars: int = 5000) -> str:
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        content = resp.text
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n... [truncated, {len(content)} total]"
        return content
    except Exception as e:
        return f"Fetch error: {e}"


TOOL_FUNCS = {
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "list_files": tool_list_files,
    "run_shell": tool_run_shell,
    "web_search": tool_web_search,
    "fetch_url": tool_fetch_url,
    "send_file": tool_send_file,
}

# ──────────────────────────────────────────────
# OpenRouter API client with model fallback & caching
# ──────────────────────────────────────────────
FREE_MODELS = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "cohere/north-mini-code:free",
    "liquid/lfm-2.5-2.6b:free",
    "nvidia/nemotron-3.5-super-vl:free",
    "google/gemma-4-31b-it:free",
    "xingchen-agi/xing4.0-29b-a4b:free",
    "google/gemma-4-26b-a4b-it:free",
]

# Cache of working models (persists across requests)
_working_models_cache: List[str] = []
_failed_models_cache: Set[str] = set()

class OpenRouterClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.AsyncClient(
            base_url=OPENROUTER_BASE_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=60,
        )

    def _get_model_order(self) -> List[str]:
        """Return models ordered by: known working first, then untried, then failed last."""
        global _working_models_cache, _failed_models_cache
        ordered = []
        # First: models we know work
        for m in _working_models_cache:
            if m in FREE_MODELS:
                ordered.append(m)
        # Second: untried models
        for m in FREE_MODELS:
            if m not in _working_models_cache and m not in _failed_models_cache:
                ordered.append(m)
        # Last: failed models (but still try them in case they recovered)
        for m in _failed_models_cache:
            if m in FREE_MODELS:
                ordered.append(m)
        return ordered

    async def _ping_model(self, model: str) -> bool:
        """Quick ping to check if model is available (uses minimal tokens)."""
        try:
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 5,
                "temperature": 0,
            }
            resp = await self.client.post("/chat/completions", json=payload, timeout=10)
            if resp.status_code == 200:
                return True
            if resp.status_code == 404:
                _failed_models_cache.add(model)
                return False
            return False
        except Exception:
            return False

    async def _ensure_model_works(self, model: str) -> bool:
        """Ping model if not cached as working; return True if works."""
        global _working_models_cache, _failed_models_cache
        if model in _working_models_cache:
            return True
        if model in _failed_models_cache:
            return False
        # Ping it
        works = await self._ping_model(model)
        if works:
            _working_models_cache.insert(0, model)
            if len(_working_models_cache) > 3:
                _working_models_cache.pop()
        else:
            _failed_models_cache.add(model)
        return works

    async def chat(self, messages: List[Dict], tools: List[Dict] = None) -> Dict:
        global _working_models_cache, _failed_models_cache

        # Add thinking instruction to system prompt
        thinking_messages = list(messages)
        if thinking_messages and thinking_messages[0].get("role") == "system":
            thinking_messages[0] = {
                **thinking_messages[0],
                "content": thinking_messages[0]["content"] + "\n\nThink step by step before answering. Show your reasoning clearly."
            }

        last_error = None
        for model in self._get_model_order():
            # Ping first - only proceed if model responds
            if not await self._ensure_model_works(model):
                continue
            try:
                payload = {
                    "model": model,
                    "messages": thinking_messages,
                    "temperature": 0.3,
                    "max_tokens": 4000,
                }
                if tools:
                    payload["tools"] = tools
                    payload["tool_choice"] = "auto"
                resp = await self.client.post("/chat/completions", json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    data["_model_used"] = model
                    # Cache this model as working (move to front)
                    if model in _failed_models_cache:
                        _failed_models_cache.discard(model)
                    if model not in _working_models_cache:
                        _working_models_cache.insert(0, model)
                    # Keep cache small
                    if len(_working_models_cache) > 3:
                        _working_models_cache.pop()
                    return data
                last_error = f"{model}: {resp.status_code} - {resp.text}"
                if resp.status_code == 404:
                    _failed_models_cache.add(model)
                    _working_models_cache = [m for m in _working_models_cache if m != model]
            except Exception as e:
                last_error = f"{model}: {e}"
                continue
        raise RuntimeError(f"All free models failed: {last_error}")

    async def close(self):
        await self.client.aclose()


# ──────────────────────────────────────────────
# Conversation memory (per user)
# ──────────────────────────────────────────────
conversations: Dict[int, List[Dict]] = {}


def get_conversation(user_id: int) -> List[Dict]:
    if user_id not in conversations:
        conversations[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    return conversations[user_id]


def add_message(user_id: int, role: str, content: str, tool_calls=None, tool_call_id=None):
    msg = {"role": role, "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    if tool_call_id:
        msg["tool_call_id"] = tool_call_id
    get_conversation(user_id).append(msg)


# ──────────────────────────────────────────────
# Telegram handlers
# ──────────────────────────────────────────────
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Claude-like bot ready**\n\n"
        "I can:\n"
        "• Chat & reason\n"
        "• Read/write files on server\n"
        "• Run shell commands\n"
        "• Search the web & fetch URLs\n\n"
        "Just send me a message!",
        parse_mode="Markdown",
    )


async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conversations[user_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    await update.message.reply_text("🧹 Conversation cleared.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    user_id = update.effective_user.id
    text = update.message.text

    # Show "typing" indicator
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    conv = get_conversation(user_id)
    conv.append({"role": "user", "content": text})

    client = OpenRouterClient(OPENROUTER_API_KEY)

    try:
        # First LLM call
        resp = await client.chat(conv, TOOLS)
        choice = resp["choices"][0]
        msg = choice["message"]

        # Handle tool calls
        if msg.get("tool_calls"):
            conv.append(msg)  # assistant message with tool_calls

            for tc in msg["tool_calls"]:
                func_name = tc["function"]["name"]
                args = json.loads(tc["function"]["arguments"])

                await update.message.reply_text(f"🔧 *Using {func_name}...*", parse_mode="Markdown")

                if func_name in TOOL_FUNCS:
                    if func_name == "send_file":
                        result = await TOOL_FUNCS[func_name](update=update, **args)
                    else:
                        result = await TOOL_FUNCS[func_name](**args)
                else:
                    result = f"Unknown tool: {func_name}"

                conv.append({
                    "role": "tool",
                    "content": result,
                    "tool_call_id": tc["id"],
                })

            # Second LLM call with tool results
            resp = await client.chat(conv, TOOLS)
            choice = resp["choices"][0]
            msg = choice["message"]

        final_text = msg.get("content") or "(empty response)"
        conv.append({"role": "assistant", "content": final_text})

        # Send response (handle long messages)
        for chunk in _chunk_text(final_text, 4000):
            await update.message.reply_text(chunk, parse_mode="Markdown")

    except httpx.HTTPStatusError as e:
        await update.message.reply_text(f"❌ API Error: {e.response.status_code} - {e.response.text}")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
    finally:
        await client.close()


def _chunk_text(text: str, max_len: int) -> List[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    for i in range(0, len(text), max_len):
        chunks.append(text[i:i+max_len])
    return chunks


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"Error: {context.error}")


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
async def post_init(app: Application):
    await app.bot.set_my_commands([
        BotCommand("start", "Start the bot"),
        BotCommand("clear", "Clear conversation history"),
    ])


def main():
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    # OPENROUTER_MODEL no longer required - uses FREE_MODELS fallback

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    print("🤖 Bot starting (polling mode)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
