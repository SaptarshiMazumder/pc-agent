"""All configuration in one place. Reads from environment / .env file."""
import os
from dotenv import load_dotenv

load_dotenv()

# --- LLM (swap this whole section later for a local model client) ---
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# Model that supports computer use. Sonnet is much cheaper for a screenshot-heavy
# loop; bump to "claude-opus-4-8" if you want maximum reliability.
MODEL = os.getenv("MODEL", "claude-sonnet-4-6")

# Tool + beta versions for current Claude 4.x models.
COMPUTER_TOOL_TYPE = "computer_20251124"
BASH_TOOL_TYPE = "bash_20250124"
EDITOR_TOOL_TYPE = "text_editor_20250728"
EDITOR_TOOL_NAME = "str_replace_based_edit_tool"
BETA_FLAG = "computer-use-2025-11-24"

MAX_TOKENS = 4096
MAX_ITERATIONS = 40          # hard cap per request → stops runaway API spend
MODEL_DISPLAY_W = 1280       # coordinate space the model "sees" (kept small on purpose)
MODEL_DISPLAY_H = 800

# --- Discord transport ---
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
OWNER_ID = int(os.environ["DISCORD_OWNER_ID"])   # only this user can command the agent
POST_SCREENSHOTS = os.getenv("POST_SCREENSHOTS", "true").lower() == "true"

# --- Safety ---
# If True, every bash command must be approved in Discord before it runs.
REQUIRE_BASH_APPROVAL = os.getenv("REQUIRE_BASH_APPROVAL", "true").lower() == "true"
