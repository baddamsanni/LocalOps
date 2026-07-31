# localops

Talk to your laptop from your phone.

A local Telegram bot that chats with you normally — and runs shell
commands on your laptop when you need them.

**Just talk normally — the assistant decides on its own whether your
message needs to run something on your machine or just needs an answer.**

No SSH. No VPN. No cloud server. Just your laptop and your phone.

```
You: what's my disk space?
Bot: ✅ df -h
     ─────────────────────
     ...output...
     ─────────────────────
💬 You're using 320GB of 500GB (64% full), 180GB free.

You: hello
Bot: Hey! What can I help you with?
```

## How it works

```
Your phone (Telegram)
        ↓ message
Telegram Bot API
        ↓
localops (running on your laptop)
        ↓
ONE LLM call (plain text)
        │
        ├─ normal reply → sent to Telegram
        │
        └─ reply starts with CMD: <shell command>
                ↓
           Safety check — read / write / destructive / blocked
                ↓
              read → runs immediately
              write / destructive → shows exact command, waits for yes/no
              blocked → never runs (no confirmation override)
                ↓
           Shell executor — streams raw output to Telegram
                ↓
           Second LLM call explains the output → 💬 message
```

The switch is a deterministic string check on the LLM reply (`CMD:`),
not a separate classifier and not native tool-calling APIs. Every write
or destructive command is shown to you in full before it runs. File and
directory deletion is permanently disabled — there is no way to delete
or destructively overwrite a file through localops, even with confirmation.

## Requirements

- Python 3.10+
- Git
- A Telegram account (to create a bot and message it)
- One of:
  - An Anthropic API key (for Claude), or
  - Ollama installed and running locally (free, offline, private), or
  - Any OpenAI-compatible API (OpenAI, Groq, Together AI, LM Studio, vLLM)

No IDE required. No Docker. No database. No cloud account.

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/localops.git
cd localops
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv

# Mac/Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Get a Telegram bot token

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot`
3. Follow the prompts (choose a name and a username ending in `bot`)
4. BotFather replies with a token that looks like:
   `123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw`
5. Copy it — you'll need it in step 6.

### 5. Find your Telegram user ID

1. Message [@userinfobot](https://t.me/userinfobot) on Telegram
2. It replies with your numeric user ID, e.g. `987654321`
3. Copy it — this ensures only you can control your laptop through
   the bot. Anyone else who messages your bot is rejected automatically.

### 6. Configure

```bash
cp config.example.yml config.yml
```

Open `config.yml` in any text editor and fill in:

```yaml
telegram:
  bot_token: "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"   # from step 4
  allowed_user_id: 987654321                                    # from step 5

llm:
  provider: "ollama"    # "claude" | "ollama" | "openai"
```

Then fill in the section matching your chosen provider — see
[Choosing an LLM provider](#choosing-an-llm-provider) below.

`config.yml` is gitignored — your token and API keys never get committed.

### 7. Run it

```bash
python main.py
```

If your config is valid, you'll see the bot start polling. If something's
missing, you'll get a clear error message telling you exactly what to fix
— no stack traces.

### 8. Send your first message

Open Telegram, find the bot you created, and send:

```
hello
```

Then try:

```
what's my current directory?
```

You should get a response within a few seconds. You're live.

## Choosing an LLM provider

Switching providers is a one-line change in `config.yml`.

### Option A — Ollama (free, offline, private)

Best if you already have models downloaded locally and want zero API cost.

```bash
# check what models you have
ollama list
```

```yaml
llm:
  provider: "ollama"
  ollama:
    base_url: "http://localhost:11434"
    model: "qwen2.5:7b"  # use the exact name from `ollama list`
```

Requires Ollama installed and running (`ollama serve`
or the desktop app).

### Option B — Claude

Best quality, requires an API key.

```yaml
llm:
  provider: "claude"
  claude:
    api_key: "sk-ant-..."
    model: "claude-sonnet-4-6"
```

Get a key at [console.anthropic.com](https://console.anthropic.com).

### Option C — OpenAI or any OpenAI-compatible API

Works with OpenAI, Groq, Together AI, LM Studio, or any local server
that speaks the OpenAI chat completions format.

```yaml
llm:
  provider: "openai"
  openai:
    api_key: "sk-..."
    model: "gpt-4o"
    base_url: "https://api.openai.com/v1"   # change this to point elsewhere
```

To use a local server like LM Studio instead, just change `base_url`:

```yaml
    base_url: "http://localhost:1234/v1"
```

## Safety model

Every command you send is classified before it runs:

| Level | Example | Behavior |
|---|---|---|
| Read | `ls`, `ps`, `df`, `git status` | Runs immediately |
| Write | `git pull`, `npm install`, `mv` to a new path | Confirmation required |
| Destructive | `kill`, `pkill` | Confirmation required, with a warning |
| Blocked | `rm`, `rmdir`, `shred`, `find -delete`, `git clean -f`, `mv` overwriting an existing file | Never executed, no confirmation possible, no exceptions |

File and directory deletion is permanently disabled in this tool — there
is no way to delete or destructively overwrite a file through localops,
even with confirmation.

All of these rules live in `config.yml` under the `safety:` section — you
can add your own blocked patterns or read-only commands without touching
any code. Overwrite detection for `mv` also checks the filesystem.

The LLM's own classification and the local safety checker's classification
are both run, and the stricter of the two wins — so the LLM can never
talk its way past a rule you've configured.

## Using GitHub CLI, AWS CLI, kubectl, or anything else

localops runs whatever is authenticated on your machine. If you've already
run `gh auth login`, `aws configure`, or similar, those commands work
through localops exactly like any other shell command:

```
You: show me open PRs on my repo
Bot: → runs `gh pr list` (read-only, runs immediately)

You: create an issue titled login bug
Bot: → shows `gh issue create --title "login bug"` → waits for ✅
```

Add frequently-used read-only commands to `safety.read_only_commands` in
`config.yml` so they run without a confirmation prompt every time.

## Example conversations

### Plain chat

```
You: hello
Bot: Hey! What can I help you with?

You: explain what a memory leak is
Bot: A memory leak happens when a program keeps allocating memory
     without releasing it, causing usage to grow over time...
```

### System check (LLM replies CMD: df -h — runs if read-only)

```
You: what's my disk space?
Bot: ✅ df -h
     ─────────────────────
     Filesystem   Size  Used  Avail  Use%
     /dev/disk1   500G  320G  180G   64%
     ─────────────────────
     Done in 0.3s

💬 You're using 320GB of 500GB, which is 64% full — you
   still have a healthy 180GB of free space.
```

### Write action (confirmation required)

```
You: pull the latest changes
Bot: 📋 git pull origin main
     Pull the latest changes from main branch.
     Tap yes to run it, or no to cancel.
     [yes] [no]   ← reply keyboard (tap = sends "yes"/"no" as a message)
You: yes
Bot: ✅ git pull origin main
     ─────────────────────────
     Already up to date.
     ─────────────────────────
     Done in 0.8s
```

### Destructive action (confirmation + warning)

```
You: kill the stuck node process
Bot: ⚠️ kill -9 1234
     Stop process 1234.
     This is destructive and may be hard to reverse.
     Tap yes to run it, or no to cancel.
     [yes] [no]
```

### Blocked command (file deletion — no override)

```
You: run: delete the dist folder
Bot: 🚫 Blocked: this command matches a blocked pattern
     and will never be executed.

You: wipe the whole disk
Bot: 🚫 Blocked: this command matches a blocked pattern
     and will never be executed.
```

## Running tests

```bash
pytest
```

All tests run offline against mocked providers — no real API calls or
Telegram connections required to verify the codebase.

## Project structure

```
localops/
├── main.py                 # entry point, polling loop
├── routing.py              # CMD: reply parsing
├── config.py               # loads and validates config.yml
├── config.example.yml      # template — copy to config.yml
├── telegram_client.py      # Telegram Bot API wrapper
├── llm_router.py           # Claude / Ollama / OpenAI providers
├── platform_info.py        # host OS detection for command hints
├── safety.py               # command classification
├── executor.py             # sandboxed shell execution
├── confirmation_gate.py    # yes/no confirm (reply keyboard or typed)
├── explain.py              # post-execution explanation prompt
├── requirements.txt
└── tests/
```

## Security notes

- Only the Telegram user ID configured in `allowed_user_id` can send
  commands. Every message is checked before anything else happens.
- `config.yml` is gitignored — never commit real tokens or API keys.
- Blocked patterns are checked before the LLM's classification is even
  trusted — a compromised or manipulated LLM response cannot bypass them.
- Run this on a machine you control. It executes real shell commands
  with your local user's permissions.

## License

MIT
