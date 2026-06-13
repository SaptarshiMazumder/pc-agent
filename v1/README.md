# pc-agent

A minimal "computer use" agent that controls your **real** desktop and that you
drive remotely from Discord. ~400 lines, four files, no framework.

```
config.py     all settings (env-driven)
computer.py   executor: turns model actions into real mouse/keyboard/bash/file ops
agent.py      the Claude loop (screenshot -> decide -> act -> repeat)
bot.py        Discord transport + entrypoint
```

## How it works

The LLM never touches your machine. It looks at a screenshot and returns an
action like `left_click @ (840, 210)` or `bash: code .`; **your** code performs
it, screenshots again, and sends the result back. Loop until the task is done.

The model reasons in a fixed 1280x800 coordinate space; `computer.py` scales
between that and your real resolution, so clicks land correctly on any monitor.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then fill it in
python bot.py
```

**Discord token + owner id:** create an app at the Discord Developer Portal →
Bot → copy the token. Turn on the **Message Content Intent**. Invite it to a
private server (a server with just you). Your owner id: enable Developer Mode in
Discord, right-click your name → Copy User ID. Then DM the bot or post in a
channel it can see: *"open youtube and search for lofi"*.

## OS-specific notes

- **macOS:** grant the terminal/Python **Accessibility** *and* **Screen
  Recording** permission (System Settings → Privacy & Security), or pyautogui
  can't move the mouse or screenshot.
- **Windows:** works out of the box. Run as your normal user, not elevated.
- **Linux:** pyautogui needs **X11**. On Wayland, log into an "Xorg" session or
  the mouse/keyboard calls silently fail.

## Swapping the API for a local LLM later

Everything model-specific is isolated in `agent.py` (`client` + `run_turn`) and
the tool/version names in `config.py`. To go local, replace the `Anthropic`
client with your server's client and keep the same loop shape: send messages +
tool definitions, read back `tool_use` blocks, return `tool_result`. Your local
model must support vision (it has to read screenshots) and tool calling. If it
uses a different tool schema, adapt the `TOOLS` list and the dispatch in
`agent.py`; `computer.py` doesn't change.

## Safety — read this before pointing it at your real machine

This is an autonomous LLM with full control of your desktop, reachable from your
phone. Treat it accordingly.

- **Start sandboxed.** First runs in a throwaway VM or a separate OS user, not
  your main account with your saved passwords and payment methods.
- **Keep the bash approval gate on** (`REQUIRE_BASH_APPROVAL=true`) until you
  trust it. Add a confirmation step for `computer` actions too if you want.
- **Owner lock is enforced**, but the bot is only as private as its server —
  use a server with just you, and rotate the token if it ever leaks.
- **Prompt injection is real.** If the agent reads a web page or file containing
  hidden instructions, it can be steered into doing things you didn't ask.
  Anything that both browses the web *and* runs shell commands is high-risk.
- **Failsafe:** slam the mouse into a screen corner to abort pyautogui instantly.
- The `MAX_ITERATIONS` cap stops a confused loop from burning your API budget.
```
