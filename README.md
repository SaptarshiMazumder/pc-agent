<!-- Language: English | [日本語](#日本語) -->

# pc-agent

**A personal AI agent that acts on your own machine — any LLM, real tools.**

`pc-agent` is a commercial, provider-agnostic agent that can read and write your
files, run shell commands, search and fetch the web, and drive a real browser —
all on your local PC. You talk to it from a terminal; it plans, calls tools in a
loop, and reports back. Point it at any model (Gemini, Claude, GPT, …) through a
single config switch.

> Example: _"Read my `movies_watchlist.txt` from the desktop, find which streaming
> services in Japan have each title, and give me a list with watch links."_ —
> the agent finds the file, reads it, searches the web, and returns a ranked list.

## 🎬 Demo

https://github.com/user-attachments/assets/362d4180-ee6d-49aa-b7ff-fdd70f91be27

## What it does

- **Acts on this machine** — files, shell, web search/fetch, and a real headless browser.
- **Any LLM** — model is one config line via [LiteLLM](https://github.com/BerriAI/litellm)
  (`gemini/…`, `anthropic/…`, `openai/…`, and more). Keys stay local in `v2/.env`.
- **Agentic loop** — LLM → tool calls → execute → feed results back → repeat until done,
  with continuation/verification retries for incomplete turns.
- **Skills** — drop-in `SKILL.md` playbooks the agent reads on demand to learn how to
  do a task, without adding new code.
- **Self-contained** — keys, runtime data, and dependencies all live under `v2/`.

## Repository layout

| Path         | What it is                                                                                                                              |
| ------------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| **`v2/`**    | **Active version** — `agentd`: a WebSocket gateway daemon running the agent loop, plus a terminal client. Clean/hexagonal architecture. |
| `v1/`        | Earlier prototype stack (kept for reference).                                                                                           |
| `reference/` | Reference material and notes.                                                                                                           |

👉 **Start in [`v2/`](v2/)** — see [v2/README.md](v2/README.md) for full setup, run, and test instructions.

## Quick start

```powershell
cd v2
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium   # browser tool

# Create v2/.env from .env.example and add a provider key (e.g. GEMINI_API_KEY)

# Terminal 1 — gateway
.\.venv\Scripts\python.exe -X utf8 -m agentd
# Terminal 2 — client
.\.venv\Scripts\python.exe -X utf8 -m clients.terminal
```

---

<a name="日本語"></a>

<!-- 言語: [English](#pc-agent) | 日本語 -->

# pc-agent（日本語）

**あなたのPC上で実際に作業する、パーソナルAIエージェント — どんなLLMでも、本物のツールで。**

`pc-agent` は、プロバイダーに依存しない商用エージェントです。ローカルPC上で
ファイルの読み書き、シェルコマンドの実行、Web検索・取得、そして実際のブラウザ
操作まで行えます。ターミナルから話しかけると、エージェントが計画を立て、ツールを
ループで呼び出し、結果を報告します。設定を1行変えるだけで、任意のモデル
（Gemini、Claude、GPT など）に切り替えられます。

> 例：_「デスクトップの `movies_watchlist.txt` を読んで、各作品が日本のどの
> 配信サービスで観られるか調べて、視聴リンク付きのリストにして」_ —
> エージェントはファイルを見つけて読み、Webを検索し、ランク付けしたリストを返します。

## 🎬 デモ

https://github.com/user-attachments/assets/362d4180-ee6d-49aa-b7ff-fdd70f91be27

## できること

- **このPC上で作業** — ファイル、シェル、Web検索／取得、実際のヘッドレスブラウザ。
- **どんなLLMでも** — モデルは [LiteLLM](https://github.com/BerriAI/litellm) 経由で
  設定1行（`gemini/…`、`anthropic/…`、`openai/…` など）。キーは `v2/.env` にローカル保存。
- **エージェントループ** — LLM → ツール呼び出し → 実行 → 結果を返す → 完了まで繰り返し。
  不完全な応答には継続・検証リトライを行います。
- **スキル** — `SKILL.md` のプレイブックを置くだけで、コードを足さずに
  エージェントが必要なときに読み込んでタスクのやり方を学びます。
- **自己完結** — キー・実行データ・依存関係はすべて `v2/` 配下にまとまっています。

## リポジトリ構成

| パス         | 内容                                                                                                                                                       |
| ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`v2/`**    | **現行バージョン** — `agentd`：エージェントループを動かすWebSocketゲートウェイ常駐プロセスとターミナルクライアント。クリーン／ヘキサゴナルアーキテクチャ。 |
| `v1/`        | 初期プロトタイプ（参考用に保持）。                                                                                                                         |
| `reference/` | 参考資料とノート。                                                                                                                                         |

👉 **まずは [`v2/`](v2/) から** — セットアップ・実行・テストの詳細は [v2/README.md](v2/README.md) を参照してください。

## クイックスタート

```powershell
cd v2
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium   # ブラウザツール用

# .env.example をコピーして v2/.env を作成し、プロバイダーキー（例：GEMINI_API_KEY）を記入

# ターミナル1 — ゲートウェイ
.\.venv\Scripts\python.exe -X utf8 -m agentd
# ターミナル2 — クライアント
.\.venv\Scripts\python.exe -X utf8 -m clients.terminal
```
