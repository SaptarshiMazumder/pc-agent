"""
Discord transport + entrypoint.

  - Only OWNER_ID can command the agent.
  - Each message from the owner is a task; results stream back into the channel.
  - The agent runs in a worker thread (pyautogui + API calls are blocking), so
    callbacks schedule coroutines back onto the event loop.
  - bash commands trigger a ✅/❌ approval prompt when REQUIRE_BASH_APPROVAL is on.

Run with:  python bot.py
"""
import asyncio
import io

import discord

import config
import agent
import computer

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

LOOP = None
SESSION = []          # running conversation history for the owner
BUSY = asyncio.Lock()


async def send_chunks(channel, text):
    for i in range(0, len(text), 1900):
        await channel.send(text[i:i + 1900])


def make_callbacks(channel):
    """Build thread-safe callbacks the worker thread can call."""
    def on_text(text):
        asyncio.run_coroutine_threadsafe(send_chunks(channel, text), LOOP).result()

    def approve_bash(cmd):
        async def ask():
            msg = await channel.send(f"🔧 Run this shell command?\n```\n{cmd}\n```")
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")
            def check(r, u):
                return u.id == config.OWNER_ID and str(r.emoji) in ("✅", "❌") \
                    and r.message.id == msg.id
            try:
                reaction, _ = await client.wait_for("reaction_add", timeout=120, check=check)
                return str(reaction.emoji) == "✅"
            except asyncio.TimeoutError:
                return False
        return asyncio.run_coroutine_threadsafe(ask(), LOOP).result()

    return on_text, approve_bash


@client.event
async def on_ready():
    global LOOP
    LOOP = asyncio.get_running_loop()
    print(f"Agent online as {client.user}. Owner: {config.OWNER_ID}")


@client.event
async def on_message(message):
    if message.author.id != config.OWNER_ID or message.author.bot:
        return

    text = message.content.strip()
    if text.lower() in ("reset", "clear", "stop"):
        SESSION.clear()
        await message.channel.send("🧹 Conversation reset.")
        return

    if BUSY.locked():
        await message.channel.send("⏳ Still working on the previous task.")
        return

    async with BUSY:
        await message.channel.send("👀 On it…")
        SESSION.append({"role": "user", "content": text})
        on_text, approve_bash = make_callbacks(message.channel)
        try:
            await asyncio.to_thread(agent.run_turn, SESSION, on_text, approve_bash)
        except Exception as e:
            await message.channel.send(f"💥 Error: {e}")
            return
        if config.POST_SCREENSHOTS:
            png = await asyncio.to_thread(computer.latest_screenshot_png)
            await message.channel.send(file=discord.File(io.BytesIO(png), "screen.png"))


if __name__ == "__main__":
    client.run(config.DISCORD_TOKEN)
