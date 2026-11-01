# Telegram Claude-like Bot

A Telegram bot powered by OpenRouter (Claude, GPT, etc.) with tools:
- 📁 File read/write/list
- 🐚 Shell command execution
- 🔍 Web search (DuckDuckGo)
- 🌐 URL fetching

## Quick Start (Local)

```bash
# 1. Clone & enter
cd tg-claude-bot

# 2. Install deps
pip install -r requirements.txt

# 3. Configure
cp .env.example .env
# Edit .env with your tokens

# 4. Run
python bot.py
```

## Deploy to Render (Easiest)

### Option A: Blueprint (Recommended)
1. Push this folder to a GitHub repo
2. In Render Dashboard → **New +** → **Blueprint**
3. Connect your repo → Render detects `render.yaml`
4. Click **Apply** → sets up worker service
5. In service **Environment** tab, add:
   - `TELEGRAM_BOT_TOKEN` (from @BotFather)
   - `OPENROUTER_API_KEY` (from openrouter.ai)
6. Deploy → runs as background worker (polling mode)

### Option B: Manual Worker
1. New + → **Background Worker**
2. Connect repo → Docker
3. Add env vars (same as above)
4. Deploy

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | From @BotFather |
| `OPENROUTER_API_KEY` | Yes | From openrouter.ai/keys |
| `OPENROUTER_MODEL` | No | Default: `anthropic/claude-3.5-sonnet` |

## Bot Commands

- `/start` — Welcome message
- `/clear` — Clear conversation history

## Example Models on OpenRouter

| Model | ID |
|-------|-----|
| Claude 3.5 Sonnet | `anthropic/claude-3.5-sonnet` |
| Claude 3 Opus | `anthropic/claude-3-opus` |
| GPT-4o | `openai/gpt-4o` |
| GPT-4o-mini | `openai/gpt-4o-mini` |
| Llama 3.1 405B (free) | `meta-llama/llama-3.1-405b-instruct` |
| Nemotron 3 Ultra (free) | `nvidia/nemotron-3-ultra` |

See all at: https://openrouter.ai/models

## Safety Notes

- Shell commands run in the container's `/app` directory
- File access restricted to `/app` (no path traversal)
- On Render free tier: worker spins down after 15 min inactivity
- First message after spin-down takes ~30s to wake up

## Local + Tunnel Alternative

If you prefer running locally with a public URL:

```bash
# Install cloudflared
# Run bot locally
python bot.py &

# In another terminal, expose port (not needed for polling mode!)
# But if you add webhook later:
cloudflared tunnel --url http://localhost:8080
```