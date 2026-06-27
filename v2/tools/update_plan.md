---
summary: Track short work plan
---
## Planning
For ANY task that takes more than one step, your FIRST action MUST be to call `update_plan` — do NOT start the work before you have a plan. BREAK THE TASK DOWN into the smallest concrete steps, and for EACH step name the specific tool it uses. Trigger planning whenever the task: needs more than one tool, has more than ~2 steps, processes MULTIPLE items (several people / files / links), or reads as 'do X, then Y, then Z'. Keep the plan current with `update_plan` as you go (mark steps in_progress / completed), and use the BEST tool per step — `browser` (it is SIGNED IN via a persistent profile) or `web_search` for the web; `computer` ONLY when a task truly needs the real desktop GUI. ONLY skip planning for a genuinely simple, single-step request.
Example - "summarize the 3 latest posts on a blog into a file":
  1. web_fetch: fetch the blog index; note the 3 latest post URLs
  2. web_fetch: fetch each post; extract the key points
  3. write: save the summaries to a file
Pick each step's tool by what it needs (public page vs signed-in/blocked); follow the web-access rules above.
