# Polyface · 同源万面

[中文](./README.zh-CN.md) | **English**

> **One source in, a thousand faces out.** Take one piece of original material and rewrite it
> automatically into publish-ready drafts tuned to each platform's voice — drafts that have a
> real shot at earning reach.

A **local web tool** for content creators: your material and drafts stay on your own machine, and
the model runs on your own API key. Inspired by Creative Minds Jam (*Content repurposing across
platforms*), but **built entirely from scratch** — it does not depend on any external agent platform.

> ⚠️ **About "your data never leaves your machine"**: earlier versions advertised this. **That claim
> was inaccurate** and has been corrected (see `docs/08` ADR-017). Please read **Data boundary** below first.

## Data boundary (read this first)

| Stays on your machine | Sent to an external LLM |
|---|---|
| Source material and drafts (SQLite) | **The material body text** (sent in full during analysis) |
| Audio/video files (`data/media`) | The confirmed fact list (reused during generation) |
| **Audio/video transcription** (local ASR / subtitle extraction) | Template contents |
| Template library, performance feedback, retrospectives | Creator profile / past-experience hints |
| LLM key (`python-service/.env`) | |

**In one sentence**: your data is **stored** locally, but **it is impossible for generation to stay
local** — the material body text is sent to the LLM provider *you* configure (SenseNova by default).

- ✅ **Still true**: no platform intermediary, no cloud account, and **transcription runs fully
  locally and is never uploaded** (cloud ASR is explicitly out of scope).
- ✅ **Still a real selling point**: data lives only on your machine, and the model runs on your own
  key — you don't hand your material to a third-party SaaS.
- ⚠️ **`LLM_MOCK=true` (the default when no key is set) is fully offline** — nothing goes out. But
  the output is a **demo result**, not real generation. The UI's mode badge tells you which you're in.

## Quick start

> Requires: **Java 17+** / **Python 3.11+** / a browser. No database needed.
> **Cover rendering (optional)** additionally needs **Node.js ≥ 20.19** + `npm install -g gimpish`.
> Without it everything still runs — "Generate cover" just degrades to an install hint (nothing else
> is affected). See ADR-019.
>
> **Windows** uses `.bat`; **macOS / Linux / Git Bash** uses `.sh` — the two sets behave identically,
> item by item.

### Option A: download the release package (recommended — no Maven needed)

**Windows**

```bat
:: After unzipping polyface-<version>.zip, inside that directory:
scripts\setup.bat     :: first run: create venv, install deps, generate .env
scripts\start.bat     :: start (opens the browser)
scripts\stop.bat      :: stop (add -y in scripts/CI to skip the confirmation)
```

**macOS / Linux**

```bash
# After unzipping polyface-<version>.zip, inside that directory:
bash scripts/setup.sh     # first run: create venv, install deps, generate .env
bash scripts/start.sh     # start (opens the browser)
bash scripts/stop.sh      # stop (add -y in scripts/CI to skip the confirmation)
```

The release package ships a pre-built `polyface.jar`, so **you don't need Maven**.

> **Port already in use?** If 8000 (Python) or 8080 (Java) is taken, you don't have to edit the scripts:
> ```bash
> # macOS / Linux / Git Bash
> POLYFACE_JAVA_PORT=18080 POLYFACE_PY_PORT=18000 bash scripts/start.sh
> ```
> ```bat
> :: Windows
> set POLYFACE_JAVA_PORT=18080
> set POLYFACE_PY_PORT=18000
> scripts\start.bat
> ```
> The pre-flight check tells you exactly which port is taken and prints this command.

### Option B: run from source (developers)

```bat
:: Windows
scripts\setup.bat          :: this step needs Maven to build the jar
scripts\start.bat
```

```bash
# macOS / Linux / Git Bash
bash scripts/setup.sh
bash scripts/start.sh
```

Or start the two services manually:

```bash
# Python LLM service (note: must be started from inside python-service — .env is resolved
# relative to the current directory)
cd python-service && .venv/Scripts/python -m uvicorn app.main:app --port 8000
# on macOS/Linux use .venv/bin/python

# Java backend (under Git Bash you must go through scripts/mvn.sh — calling mvn directly
# fails with ClassNotFoundException)
mvn -f java-backend/pom.xml spring-boot:run
```

Then open <http://127.0.0.1:8080>.

### About the LLM key (important)

**It runs without a key.** By default `LLM_MOCK=true`, which is an offline demo mode: instant
responses, no network at all — but the output is a **demo result** (template-filled content, not real
generation).

For real generation, edit `python-service/.env`:

```ini
LLM_API_KEY=your-key
LLM_MOCK=false
```

Any OpenAI-compatible endpoint works (SenseNova / DeepSeek / Qwen / OpenAI / Cline…); just change
`LLM_BASE_URL` + `LLM_MODEL`. Gateways whose response envelope isn't the standard shape are handled
too — in practice Cline wraps successful responses in an extra `data` layer, and without handling
that the SDK only reports `TypeError: 'NoneType' object is not subscriptable`, which looks nothing
like a protocol problem.

### Pre-flight self-check (optional, handy when something's wrong)

```bat
python scripts\doctor.py
```

Checks Java version / Python version / venv and dependencies / jar / port conflicts / data directory /
LLM mode, and for every blocker gives you **concrete instructions to fix it**. `start.bat` runs it
automatically and aborts the launch rather than leaving you staring at a blank page.

## Time expectations (important — read before complaining it's slow)

| Scenario | Expected |
|---|---|
| Offline demo (`LLM_MOCK=true`) | Instant |
| **Real LLM · single platform** | **roughly 1–4 minutes** (measured 101s–250s; longer under upstream rate limiting) |
| Real LLM · multiple platforms | Adds up serially (platforms × single-platform time); **only one platform is selected by default** |

**Why it's this slow**: each platform goes through `understand → brief → draft → qa` — **up to 4 LLM
calls** — and a failed QA triggers another rewrite round. Upstream enforces tight tpm/rpm limits, so
concurrent platforms get broadly 429'd; the default is therefore **serial + inter-call delay**. That's
a deliberate tradeoff, not a bug.

Generation is a **long task**, and its timeout budget is **explicitly configurable** (240s for
generation, 60s for short tasks by default) — not a magic number baked into the code:

```ini
POLYFACE_LLM_TIMEOUT_SEC=300        # Java side: budget for the whole generation task
POLYFACE_LLM_FAST_TIMEOUT_SEC=60    # Java side: parsing / learning / usage queries
```

> ⚠️ Don't confuse this with `LLM_TIMEOUT_SEC` in the Python-side `.env` (default 60) — that's the
> limit for a **single model call**, and it's a component of the task budget.

**Partial success is preserved**: if one platform fails, drafts already generated for the others are
**not discarded** — the UI shows "succeeded X / failed Y" plus the reason, and offers
"🔁 Retry failed platforms" (which re-runs only the failed one).

## Core capabilities

- 📥 Feed in one piece of material (long-form / spoken script / outline / notes / audio-video)
- 🧬 Rewrites per platform against **platform DNA** (language style / structural templates / title
  mechanics / tag strategy / red-line words) — not just reformatting, but changing the *soul*
- 🛡️ **Fact constraints**: numbers and stories in a draft must be traceable to the material's "fact
  list"; QA blocks the AI from inventing things
- 🧠 **Memory and retrospectives**: creator profile + performance feedback + retrospective reports —
  it understands you better the more you use it
- 🎬 **Clip sheet** (Douyin/Bilibili): drafts come with shot / duration / visual / subtitle / BGM
  suggestions
- 🎥 **Audio-video ingestion**: video/audio → text, **transcribed locally** (subtitle track first,
  ASR optional), never uploaded
- 🖼 **Cover rendering** (M6-1): draft title → platform-sized cover PNG, rendered by the headless
  image editor **gimpish** (Xiaohongshu 3:4 / Douyin 9:16 / Bilibili 16:9). A `scene.json` is kept
  alongside the output so you can keep editing with `gimpish serve`
- ✏️ **Edit the cover in an editor** (M6-2): one click in the workbench **embeds** gimpish's editor,
  and your changes land back in the same `scene.json`. The editor process is launched by polyface as
  a supervised child (lazy start, cleaned up on exit)
- 📚 **Asset library** (M7-1): images / audio / video and other **assets** go into one library that
  drafts and finished cuts can reference. Content-addressed (identical files stored once), with type
  filtering, search, upload and delete. **Cover generation and ingestion register automatically** —
  you don't have to remember to file anything (unlike "material history", which holds **text
  material**; this holds **assets** — see the FAQ)
- ✂️ **Rough cut** (M8-1): source video → **remove pauses** (cut dead air by silence) → burn in
  subtitles → finished cut. All **local ffmpeg**, nothing uploaded. When a prerequisite is missing it
  **fails loudly** with actionable instructions rather than quietly handing you a cut that "looks
  fine" (see the FAQ "What do I need installed for rough cut?")
- ⏱ **Async long tasks** (M8-2): time-consuming work like draft generation and rough cutting goes
  through a **task queue** with live stage progress in the UI (probe / find pauses / cut / extract
  audio / transcribe / burn subtitles). You can leave the page, cancel, and stop watching a spinner
- 🔒 **Clear data boundary**: material and drafts live only on your machine; during generation the
  body text goes to the LLM provider you configured (see above)

## Platform support

| Stage | Platforms |
|---|---|
| v1 | Xiaohongshu · Douyin · WeChat Official Account · Zhihu · Bilibili |
| Planned | X (Twitter) · Instagram · Facebook · YouTube |

Platform differences live in `platform-dna/*.yaml` and are **directly editable** (style, structural
templates, title rules, tags, red lines, length limits). Restart and the changes take effect — no code
changes needed.

## Architecture

```
Browser (127.0.0.1:8080)
   └─ Java backend :8080 (Spring Boot)   ← orchestration / task state / local storage (SQLite)
        └─ Python LLM service :8000 (FastAPI)  ← material analysis / platform strategy / drafts / QA / cover adapter
             ├─ LLM (OpenAI-compatible, SenseNova by default)
             └─ gimpish (Node child process, headless)  ← cover rendering (optional dependency, see ADR-019)
```

Both services **listen on 127.0.0.1 only** — never exposed to the LAN or the internet. CORS on the
Python service is **off** by default (see below).

## Directory layout

```
polyface/
├── polyface.jar       # Java backend (pre-built in the release package; from source it's under java-backend/target/)
├── java-backend/      # Spring Boot :8080 — orchestration + storage + web hosting
├── python-service/    # FastAPI :8000 — LLM pipeline (analysis / strategy / drafts / QA)
├── platform-dna/      # Per-platform DNA (YAML, editable and upgradeable)
├── scripts/           # setup / start / stop / doctor (self-check) / build_release (packaging)
├── docs/              # Design and blueprint documents (numbered index below)
├── data/              # Your data (material / drafts / audio-video) — not in the repo, not packaged
├── VERSION            # Single source of truth for the version
└── dist/              # Packaging output (generated locally)
```

## Configuration reference

| Variable | Where | Default | Notes |
|---|---|---|---|
| `LLM_API_KEY` | `.env` | empty | Your model key. Empty = offline demo mode |
| `LLM_MOCK` | `.env` | `true` | Only `false` uses a real model (needs a key) |
| `LLM_BASE_URL` / `LLM_MODEL` | `.env` | SenseNova | Change these two to switch providers |
| `LLM_TIMEOUT_SEC` | `.env` | `60` | Timeout for a **single** model call |
| `LLM_JSON_RETRIES` | `.env` | `2` | Retries when the model's JSON fails to parse. Models returning malformed JSON is normal — without retries a single bad response fails the whole platform |
| `LLM_PARALLEL` | `.env` | `false` | Parallel platforms. Serial by default (upstream rate limiting) |
| `CORS_ORIGINS` | `.env` | empty | Empty = CORS disabled (recommended) |
| `FFMPEG_PATH` | `.env` | empty | Used by audio-video ingestion; empty = look on PATH |
| `POLYFACE_LLM_TIMEOUT_SEC` | system env | `240` | Java side: total budget for a generation task |
| `POLYFACE_LLM_FAST_TIMEOUT_SEC` | system env | `60` | Java side: budget for short tasks |
| `POLYFACE_DATA_DIR` | system env | `../data` | Data directory. **The default is relative to the current working directory**, so a manual `java -jar` can write data outside the package; `start.bat` / `start.sh` set it explicitly to `<package root>/data` |
| `POLYFACE_JAVA_PORT` | system env | `8080` | Java backend port. Change this when it's taken instead of editing scripts |
| `POLYFACE_PY_PORT` | system env | `8000` | Python LLM service port. Same as above |
| `GIMPISH_PATH` | `.env` | empty | gimpish entry point for cover rendering (executable or `.js`). Empty = look on PATH |
| `POLYFACE_GIMPISH` | system env | empty | Same, but **takes precedence** (without gimpish, cover features degrade to an install hint) |
| `POLYFACE_COVER_DIR` | system env | `{data-dir}/media/covers` | Root directory for cover output |
| `POLYFACE_EDITOR_PORT` | system env | `8765` | Port for the embedded editor (gimpish serve). When taken it degrades with a hint rather than failing silently |
| `POLYFACE_EDITOR_ENABLED` | system env | `true` | Set to `false` and "Open in editor" only shows a hint instead of trying to spawn a process |

> Note the prefix difference: variables in `.env` have **no** `POLYFACE_` prefix; system environment
> variables **do**. A fully commented template lives in `python-service/.env.example`.

## FAQ

**Q: The browser won't open / the page is blank after starting?**
Run `python scripts\doctor.py` first (on macOS/Linux use `python3 scripts/doctor.py`). The most common
cause is port 8080 or 8000 being taken by another program; the self-check reports it and offers two ways out:

1. Clean up stale processes with `scripts\stop.bat` (or `scripts/stop.sh`) and retry (non-interactive: add `-y`);
2. **Just switch ports — no script edits needed**:
   ```bash
   POLYFACE_JAVA_PORT=18080 POLYFACE_PY_PORT=18000 bash scripts/start.sh
   ```
   ```bat
   set POLYFACE_JAVA_PORT=18080 & set POLYFACE_PY_PORT=18000 & scripts\start.bat
   ```

> Note: if the check reports "port cannot be bound (no listening process found)", the port is usually
> inside the system's reserved range (on Windows, check with
> `netsh int ipv4 show excludedportrange protocol=tcp`), or some program has bound it without
> listening yet — in both cases switching ports is the fastest fix.

**Q: Can I try it without a key?**
Yes. The default offline demo mode returns instantly and uses no network at all, but the output is a
**demo result** (template-filled), not real generation. The mode badge in the UI tells you which one
you're in.

**Q: Why is generation so slow?**
See "Time expectations" above. Up to 4 LLM calls per platform, serial execution, and upstream rate
limiting — that's the inherent cost of the current architecture.

**Q: Is my data uploaded to your servers?**
No — this project **has** no servers of ours. Your data stays on your machine. But during generation
the material body text is sent to the **LLM provider you configured yourself** — that's unavoidable
(the model runs in the cloud). See "Data boundary".

**Q: Will it generate videos for me?**
**No.** Right now it produces **text drafts + clip sheets** (shot / duration / visual / subtitle
suggestions) only. Local automatic video assembly (starting with **B1 image+text to video**, then
**B2 smart-cutting existing footage**) is the **core direction** planned in `docs/05` FR-52/53, but
it is **deferred** and **not yet implemented** — for the ordering rationale and the decision record,
see `docs/08` ADR-018 and `docs/49`.

**Q: What do I need installed for video ingestion?**
`ffmpeg` (on PATH, or point `FFMPEG_PATH` at it). Automatic speech transcription is **optional** and
needs `faster-whisper` installed separately; without it, a video with no subtitle track degrades to a
hint asking you to paste the transcript manually — **ingestion** does not error out. (Note: **rough
cut** is different — it needs subtitles to burn in, and fails explicitly when the component is
missing; see the next FAQ.)

**Q: What do I need installed for cover generation?**
**Node.js ≥ 20.19**, then `npm install -g gimpish`
([gimpish](https://github.com/jvanderberg/gimpish), MIT, renders locally, uploads nothing).
Without it the other features still work: clicking "Generate cover" returns **install instructions**
instead of an error (`needs_manual`, see `docs/64` §3).
Generated files land in `data/media/covers/<name>/`, with `scene.json` alongside them.

**Q: What do I need installed for rough cut?**
**ffmpeg** (on PATH, or point `FFMPEG_PATH` at it) — required; without it the feature is unusable.
**Burning subtitles** additionally needs `faster-whisper` (`pip install faster-whisper`; the first use
downloads a ~244MB model).
Without `faster-whisper`, **pause removal still works**, but the task **fails explicitly** at the
"transcribe" stage:

```
✓ cut 1.0s removed 7.19s
 transcribe
✗ transcribe failed(0.0s): no local transcription component found. Run `pip install faster-whisper` and restart the service
```

This is **deliberate** (`docs/spec_roughcut.md` item 10 explicitly chose "fail loudly" over silent
degradation): a rough cut is a finished video you publish directly, and quietly shipping one missing a
subtitle line is worse than an error. Install it and restart the service.

**Q: Can I edit the cover directly?**
Yes. After a cover is generated, click "✏️ Open in editor" — polyface **embeds** gimpish's editor into
the workbench. Drag the text around, change colours, save, and it lands back in the **same
`scene.json`**.
The editor is a supervised child process started by polyface (lazy start, cleaned up when you close
polyface). You can also open it standalone with `gimpish -C <that directory> serve`.
> ⚠️ The embedded editor and the workbench are **not same-origin** (different ports), so you can view
> and interact with it, but the workbench cannot read its internal DOM — this doesn't affect usage.

**Q: What's the difference between the asset library and material history?**
Two different things — don't conflate them:

| | Material history | Asset library |
|---|---|---|
| What it stores | **Text material** (long-form / spoken script / outline / notes) | **Assets**: images / audio / video / timelines |
| What it's for | Generating drafts again | Being **referenced** by drafts and finished cuts |

Generating a cover or uploading audio/video registers into the asset library **automatically** (no
manual filing). Deleting from the asset library means "delete the record + unlink"; **the file on disk
is only removed once nothing else references it** — it never silently deletes your files.

**Q: Where is my data? How do I back it up / migrate?**
All under `data/` at the package root (`polyface.db` is SQLite; `media/` holds audio/video and asset
library files). To back up, copy the whole `data/` directory; to migrate to a new version, copy `data/`
across.

**Q: Can I expose it on the LAN / deploy it to a server?**
The current version **deliberately** listens on 127.0.0.1 only and has no authentication. Exposing it
requires adding auth first — simply changing the bind address would expose your LLM key's quota to
anyone who can reach the port.

**Q: How do I change a platform's style?**
Edit `platform-dna/<platform>.yaml` (style, structural templates, title rules, tags and red-line words
all live there) and restart the Python service.

## Roadmap

- [x] M0 Solution and blueprint
- [x] M1 Skeleton + material analysis working end to end
- [x] M2 Single platform (Xiaohongshu) drafts + full QA pipeline + SQLite persistence
- [x] M3 5-platform DNA + web workbench + clip sheets + basic content templates
- [x] M3.5 Video/audio → text ingestion (local transcription, reusing the analysis pipeline)
- [x] M4 Performance feedback + retrospectives + profile memory loop + full template management + example learning
- [x] M5 Open-source release packaging (one-command start + self-check + release package validation)
- [ ] **M6 Orchestrating an open-source editor** (`docs/08` ADR-019) — reshaped into "desktop shell + headless editor + agent orchestration":
  - [x] M6-1 Cover rendering (headless gimpish, see `docs/63`–`docs/66`)
  - [x] M6-2 Editor process orchestration and workbench embedding (`docs/67`–`docs/70`; integration shape in ADR-020)
  - [ ] M6-2b Desktop shell and bundled distribution (a real window / Tauri / shipping Node+gimpish or installing it optionally)
  - [ ] M6-3 Video editing adapter (ffmpeg first; **OpenCut cannot currently be driven headlessly** — waiting on its headless/Editor API)
- [ ] **M7 Unified asset library** (`docs/72`–`docs/75`; model in ADR-021) — the foundation for multimodality and editing:
  - [x] M7-1 Asset table + content addressing + upload/list/filter/search/delete + automatic registration from covers and ingestion
  - [ ] M7-2 Timeline as a first-class asset (lands with M10)
- [ ] **M8 Task-based orchestration** (job/task + async + progress + resume) — **a hard prerequisite for finished cuts**
- [ ] **M9 Harness** (evaluation set / prompt versioning / token cost budget) — see `docs/71` §5
- [ ] **M10 Rough cut** (pause removal / automatic subtitles / shot breakdown → **timeline JSON** + first-cut MP4)
- [ ] **Local automatic video assembly** (starting with B1 image+text to video, then B2 smart-cutting existing footage) — **not yet implemented**.
      Positioned as **core direction · currently deferred** (`docs/08` ADR-018): it's on the core path
      rather than an add-on, but the ordering calls for finishing real-author validation first. See
      `docs/05` FR-52/53 and `docs/49`.
      (ADR-019 has paused the "validation first" ordering in favour of running it in parallel with M6.)

> An honest note on M5: the orchestration logic in `scripts/*.bat` is implemented per `docs/23`, but
> **the verification environment for this delivery cannot execute `.bat` files**, so the batch scripts
> themselves are **not verified by actual execution**; the `doctor.py` and `build_release.py` logic
> they call is covered by unit tests. See `docs/48`.

## Documentation (docs/)

| Stage | Documents |
|---|---|
| Solution | 01 Overall solution design |
| Modelling | 02 Domain modelling and product blueprint (including a quick competitive scan) |
| Requirements | 05 Software Requirements Specification (SRS) |
| Feasibility | 06 Feasibility analysis (technical / economic / market / operational / compliance) |
| Use cases | 07 Use-case model and acceptance criteria |
| Decisions | 08 Architecture Decision Records (ADR) (**data boundary in ADR-017**) |
| Milestones | 03 / 04 / 09 / 10 delivery notes |
| Special topics | 11–74: template management / example learning / audio-video ingestion / release packaging / LLM hardening / platform DNA research / QA hardening / fact loop / long-task budget / startup script hardening / in-package verification / **editor adapter and cover rendering** / **editor process orchestration and embedding** / **unified asset library** — each a four-piece set of task + spec + checklist + delivery note |
| Assessment | 71 Product roadmap reassessment (gap list against "AI-powered lightweight CapCut": asset library / multimodality / orchestration / harness / rough vs. fine cutting) |

### Evaluation set (regression guard) — for anyone changing a prompt

**The problem**: change one system prompt and the only way to spot a regression used to be reading a
few drafts by hand. 328 Python + 90 Java tests passing **does not** mean the prompt hasn't regressed —
they run under `LLM_MOCK=true`.

**The approach**: a set of **frozen material** + a set of **mechanical property assertions** + real
scoring runs. See `docs/spec_eval.md`.

```bash
# Cost estimate only, no requests sent
python scripts/eval/run_eval.py --dry-run

# Full matrix (5 platforms × 10 items); resumable after interruption
python scripts/eval/run_eval.py

# Small trial run / single platform
python scripts/eval/run_eval.py --limit 2 --platforms xhs

# Compare against the last run (a different prompt fingerprint is marked "not comparable",
# never compared silently)
python scripts/eval/run_eval.py --baseline eval/reports/<timestamp>/report.json

# Show the current prompt fingerprint
python scripts/eval/fingerprint.py
```

> ⚠️ **What it does not measure**: the report only says whether **the output got worse on the same
> input**. It **cannot** say whether the product is useful — that's the third gate (`docs/50`) and
> needs a real author. That sentence is printed at the top of every report.

## Compliance

This tool **only processes material you own or are authorised to use**; it produces "rewrite
suggestions" rather than copies; and it **does not publish automatically** — publishing is done by you
on each platform, in compliance with that platform's originality and AI-content rules.

## License

[MIT](./LICENSE)
