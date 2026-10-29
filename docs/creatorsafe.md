# CreatorSafe Shorts MVP

CreatorSafe is a local, single-operator production workflow around MoneyPrinterTurbo,
not a replacement rendering engine. It produces one English portrait Short per job,
then requires a person to review the exact video and provenance package before export.
There is no publishing command. All existing public Upload-Post entry points return a
disabled error, and `auto_upload` always returns false, even with old settings enabled.
The old transport implementation remains a private helper solely to preserve protocol
regression tests; production callers must use the disabled public method.

## Architecture and dependencies reviewed

Baseline: `04f16047fc13e3d8911694ed80373b65050738e2` in TTVVBB/MoneyPrinterTurbo.

| Area | Existing implementation | MVP decision |
| --- | --- | --- |
| Entrypoints | Streamlit `webui`, FastAPI `app/asgi.py` and controllers, `cli.py`, agent integrations | Add `python -m app.creatorsafe`; retain engine interfaces |
| Pipeline | `app/services/task.py`: script → terms → audio → subtitles → materials → final videos | Call `task.start`, supplying the complete authored script and local assets |
| Media | `video.py`, MoviePy 2.2.1, FFmpeg/imageio-ffmpeg; material providers and local uploads | Use existing validated MP4 upload and portrait renderer |
| Script/model services | `llm.py`, OpenAI, LiteLLM, Google, Dashscope and other adapters | No scraped scripts or unattended LLM drafting in this first increment |
| Audio/subtitles | Edge TTS, Azure and other voice providers; faster-whisper/PyAV | Require licensed recorded English narration; use existing subtitle pipeline |
| State | Task state with memory/Redis managers, filesystem storage | Separate SQLite catalog for durable reservations, reviews and approval records |
| Cross-posting | Upload-Post automatic scheduling from shared pipeline | Public upload boundary disabled; old auto-upload settings ignored |
| Packaging/checks | Python >=3.11, `pyproject.toml` and `uv.lock`; legacy requirements, pytest, coverage, Ruff | No additional production dependencies; stdlib unit tests |

The dependency pins include `av<19` for faster-whisper compatibility and a Starlette
pin for Streamlit response compatibility. This change deliberately leaves those pins
and the lockfile intact. The upstream source is MIT licensed; that license does **not**
license stock footage, bundled music, third-party voices or identifiable people.
No dependency vulnerability audit or provider-rights certification is claimed.

## Run a job

1. Install the existing application with `uv sync --frozen`, configure FFmpeg and
   the existing subtitle provider (Whisper for uploaded narration). Do not configure
   publishing credentials. Follow the repository's normal setup instructions.
2. Write a specific original English story with a hook, setup, turn and payoff.
   Explain its original contribution. The MVP accepts 25–140 words and requires
   narration of at most 60 seconds. A person must verify the spoken language and
   actual final duration. Factual stories require source references; clearly mark
   fiction. Do not merely read news articles or make minimally varied templates.
3. Download or create MP4 footage and record/procure WAV or MP3 narration **outside**
   this workflow. Save a copy of the actual applicable license/grant/receipt and
   review commercial use, adaptation rights, attribution and releases. Record the
   creator, source page, license page, acquisition date and asset SHA-256. Merely
   selecting a provider is never treated as proof of permission. No music is added.
4. Copy `docs/creatorsafe-plan.example.json` into your working folder and replace
   every placeholder. Paths are relative to the plan file. Use an actual SHA-256
   of each asset (PowerShell: `Get-FileHash .\clip.mp4 -Algorithm SHA256`; lowercase
   the result). Evidence files are copied and hashed as part of the job package.
5. From the repository root:

   ```sh
   uv run python -m app.creatorsafe prepare /path/to/plan.json
   # Returns JOB_ID; inspect storage/creatorsafe/JOB_ID/.
   uv run python -m app.creatorsafe approve JOB_ID --reviewer "Your name" --notes "Review findings"
   uv run python -m app.creatorsafe export JOB_ID /path/to/new-export-folder
   # Or reject before approval:
   uv run python -m app.creatorsafe reject JOB_ID --reviewer "Your name" --notes "Revision needed"
   ```

Approval requires an interactive terminal, every checklist answer, reviewer name,
review notes and the exact job ID. Watch `final.mp4`, inspect all license evidence,
review facts, English, original value, attribution, advertising suitability and
synthetic-media disclosure needs. Approval only authorizes local export.
Export includes footage/narration snapshots, license evidence, plan, video, credits,
review manifest and timestamped approval. A human may then use YouTube Studio manually,
starting with private visibility and completing its checks before choosing publication.
CreatorSafe never uploads or changes video visibility, including after approval.

## Controls and limits

* Missing rights metadata, missing evidence or asset checksum mismatches stop production.
  Rights assertions are supplied by a trusted person; software cannot validate a grant's
  legal authenticity or automatically clear trademarks, likenesses or other rights.
* SQLite reserves stories before rendering using an immediate transaction. Exact and
  near-duplicate scripts are checked across nonfailed local jobs, including rejected
  ones; normalization, character similarity and word trigrams use a 0.80 threshold.
  Exact rendered-video SHA-256 duplicates are blocked. Failed jobs can be retried;
  interrupted `rendering` jobs remain conservatively reserved for operator inspection.
* Detection covers this catalog only. It does not recognize semantic paraphrases,
  visually similar re-encodes, other channels, or internet-wide reuse. Human review must
  assess footage reuse, originality and the channel's overall pattern. Keep one catalog
  for the channel; creating a fresh root discards historical detection.
* Approval is bound to the hashes of the plan, video, narration, footage and license
  evidence. Changes invalidate approval/export; create a new job and review again.
  The catalog is a trusted local ledger, not a signed, tamper-proof or multi-user system.
  Operators with write access can alter code or the ledger; do not expose this CLI as a
  remotely accessible approval service.
* The adapter tests use the existing engine contract with mocked media execution.
  No paid/provider calls, real media render or YouTube uploads are required by the tests.
  Run a real licensed clip/narration smoke test in your configured environment before
  operational use. No production footage, provider credentials or approval is fabricated.

Permission to use footage and eligibility for monetization are separate questions.
YouTube assesses originality and repetition at channel level; this workflow cannot
promise monetization. Policies consulted on 2026-10-30:
[YouTube monetization policies](https://support.google.com/youtube/answer/1311392?hl=en),
[Pexels license](https://www.pexels.com/license/) and
[Pexels third-party rights guidance](https://help.pexels.com/hc/en-us/articles/360042295214-Can-I-use-the-photos-and-videos-for-a-commercial-project).

## Validation

```sh
python -m unittest discover -s test/services -p test_creatorsafe.py -v
```

The tests cover approval/export, altered files, missing rights/checksums, duplicate
scripts across restarts, duplicate video output, rejection/failure, the existing engine
adapter contract and the disabled public upload boundary. Existing Upload-Post protocol
tests exercise only the private legacy transport, with the production boundary tested
separately. The repository CI also runs the complete dependency-backed test suite.

Next increments should add a real licensed-media integration fixture, perceptual/semantic
duplicate signals and a review UI. Expand automation only after the manual MVP is reviewed.
