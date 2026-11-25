# My 10x Solution: Recite

**Repository:** https://github.com/L0adn9/flyrank-recitation-checker
**Track:** Backend AI Engineering · **Capstone:** Your 10x Solution

## 1. What is the problem?

People who memorise the Quran have to recite it **from memory** to find out whether they still know it. Reading along with the text does not work, because seeing the words hides your own mistakes. So someone has to listen: a teacher or a friend who knows the passage. That listener is usually available once or twice a week. In between, a skipped ayah, a wrong word or two ayahs said in the wrong order go unnoticed and settle into memory, and passages learned months ago fade, because revision is unplanned: people revise what they happen to remember, not what they are losing.

Who has this problem: students doing hifz between lessons, adults who memorised as children and want to keep it, and anyone learning a few surahs for prayer.

**The 10x claim:** getting a recitation checked goes from *waiting for the next lesson (days)* to *while you recite*, any time, at no cost. You recite from memory and the words appear as you say them; a skipped, wrong, repeated or out-of-order word is marked against the Quran text on the spot, like a teacher listening along. A daily plan then tells you which passages to revise, based on your own mistakes.

## 2. How did I implement it?

**In plain words.** You pick a passage (say Al-Fil, ayahs 1–5), press the button and recite from memory. The page streams the microphone audio to the API over a WebSocket. The server cuts the stream at your pauses as it arrives, so that each piece is roughly one ayah, and runs two open Quran speech-recognition models **locally** through whisper.cpp: no paid API, and the audio never leaves the server. While you are saying a phrase, the faster model (Tarteel's `whisper-base-ar-quran`, Apache-2.0) looks at it every third of a second, and the words it heard that match the passage, in order, appear in mushaf script as you say them. When you pause, both models recognise the phrase (the more accurate one, a Whisper small fine-tuned on recitations, MIT, gets the passage text as context), and the transcript that fits the passage better is kept: a real mistake is heard by both, so this removes each model's misreadings without hiding yours. Only then can a word turn wrong or missed, so marks never flicker. The words heard are compared with the passage (the Tanzil Quran text) by a weighted edit-distance alignment, as recited rather than as spelled: mushaf and modern spelling, a sun-letter "al", the silent alef after a vowel and letters that sound alike do not make a word wrong. The alignment names each difference: a wrong word (showing what you said instead), a missed word, an extra word, a repeat (a hesitation where you went back), or words in the wrong order. Going back to fix a word or restart an ayah is not a mistake: the last attempt is what counts. When you reach the end of the passage the check finishes by itself; the stored result is made exactly as for an uploaded recording, so both ways agree. You can also upload a recording: it is queued, and a background worker checks it the same way. Every check also updates a per-ayah schedule (mistake: tomorrow; clean: after 1, 3, 7, 14, 30, 60 days), and a daily cron job turns what is due into a short revision plan. A PDF report shows accuracy over time, the most-missed words and where each surah stands.

**Measured:** on 150 test recordings by ten reciters of different styles, each with a known slip (skipped, swapped, repeated and wrong ayahs, restarts, stopping early), Recite reports exactly the slips in 110 (73%); the first version, with one model, managed 95, and only on reciters like the one it was tuned on. On two recitations by a non-professional on a laptop microphone, 97% and 92% of words were judged right (first version: 76% and 83%). Live, words appear within about a second of being said, mistakes about 2 s after a pause, and the result about 2–3 s after the last word, all on a laptop CPU. Weak spots, measured too: very fast, heavily compressed studio recitation (Sudais, 2/15), and ayahs run together without a pause.

### The concepts (6 of 7, no swaps)

| # | Concept | How Recite uses it | Where in the code |
|---|---|---|---|
| 1 | API endpoints | FastAPI: auth, Quran text, attempts (audio upload), revision plan, progress, reports, and a WebSocket (`/live`) that takes audio in and sends word statuses back while you recite. Validation errors are 400; also 401, 404, 409, 413, 415 where they apply, and 202 for queued work | `app/main.py`, `app/routes/`, `app/live.py` |
| 2 | Database | Postgres (users, Quran text, attempts, transcripts, ayah progress, plans, jobs, reports) with versioned SQL migrations; data lives in a Docker volume and survives restarts | `app/db.py`, `app/migrations/` |
| 3 | Authentication | Email and password (argon2 hashes), JWT bearer tokens with expiry; every attempt and report is visible only to its owner (others get 404) | `app/auth.py`, `app/routes/auth.py` |
| 4 | Background jobs + cron | For uploads and PDFs, a job queue in Postgres (`FOR UPDATE SKIP LOCKED`, `LISTEN/NOTIFY`, retries with exponential backoff, permanent vs. retryable errors, recovery of jobs from crashed workers) runs speech recognition and PDFs off the request path; a daily cron (each slot runs exactly once, and a missed one is caught up) builds revision plans | `app/jobs.py`, `app/worker.py`, `app/cron.py`, `app/revision.py` |
| 5 | Reporting (PDF) | "My progress" PDF: accuracy chart, most-missed words, surah table, recent checks; built by the worker with headless Chromium | `app/report.py`, `app/templates/report.html` |
| 6 | Caching | Speech recognition is the expensive step, so transcripts are cached under the SHA-256 of the audio and the model name. Uploading a recording that was heard before is scored instantly in the API (201 instead of 202) without running the model; live checks are stored in the cache too | `app/checker.py`, migration `004` |

**Swaps:** none. The one concept not used is LLM integration, on purpose: recognition is done by a dedicated speech model, and the Quran text and its meaning must not be generated by an LLM. A test suite (50 tests, real Postgres) and a Docker Compose stack are included anyway.

**Non-goal:** Recite checks *which words* you said, not *how* you pronounced them: no tajweed grading. It is also not a mobile app.

## 3. How to run it

Requires Docker. From the repository folder:

```bash
cp .env.example .env          # then set POSTGRES_PASSWORD and JWT_SECRET in .env
docker compose up --build     # first build takes a few minutes (it compiles whisper.cpp)
```

Open http://localhost:8000 and sign in with the seeded demo account **demo@recite.test / demo-recite-2026**. It already has three weeks of practice history, a revision plan for today and data for the PDF. Choose a passage, press the round button (or Space) and recite. To try the checker without reciting yourself, `python3 scripts/make_samples.py` builds test recordings with known mistakes in `samples/`. Upload one with "or upload an audio file". The README has the full 5-minute demo path, running without Docker, and the tests.
