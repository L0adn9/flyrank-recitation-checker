# One-pager: Recite, a recitation checker for Quran memorisation

## The problem

People who memorise the Quran need someone to listen while they recite from memory, because reading along with the text hides your own mistakes. That listener is a teacher or a friend, and they are not available every day, so mistakes go unnoticed for days and memorised passages quietly fade between lessons. Revision is also unplanned: most people revise whatever they remember to, not what they are actually forgetting.

## Who has it

Anyone memorising (hifz) or revising the Quran on their own between lessons: students, adults who memorised as children and want to keep it, and people learning a few surahs for prayer.

## The 10x claim

Getting a recitation checked goes from **waiting for the next lesson (days)** to **while you recite**: each word is checked as you say it, and every skipped, wrong, repeated or out-of-order word is marked against the Quran text on the spot. A daily plan then tells you exactly which ayahs to revise, based on your own mistakes.

## Concepts (5+ from the program)

| # | Concept | In this project |
|---|---|---|
| 1 | API endpoints | FastAPI: auth, surahs, attempts (audio upload), revision plan, reports, and a WebSocket that checks words while you recite; validation and honest status codes |
| 2 | Database | Postgres: users, Quran text, attempts, transcripts, progress, jobs |
| 3 | Authentication | Email + password (argon2), JWT bearer tokens; every attempt and report is scoped to its owner |
| 4 | Background jobs + cron | A Postgres job queue and worker transcribe recordings off the request path; a daily cron builds each user's revision plan |
| 5 | Reporting (PDF) | A "My progress" PDF built by a background job |
| 6 | Caching | Transcripts are cached by the audio's SHA-256: a recording that was already heard is checked instantly |

No swaps are needed. LLM integration is the one concept left out on purpose: speech recognition runs on a local open model (no LLM), and Quran meaning must not be AI-generated.

## Non-goal

No tajweed (pronunciation-rule) grading, no mobile app. The system checks **which words** you said, not **how** you pronounced them.
