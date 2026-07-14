# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

This repo has two parts:

1. A working directory for distilling raw source material (a spreadsheet, a screen-recording) into organized markdown reference docs (`meeting_room_manual.md`, `bond_manual.md`). These were formerly named `skill_1.md`/`skill_2.md` — renamed to be content-descriptive; there is no `skill_3.md` (it was a one-off Gemini re-analysis draft, merged into `meeting_room_manual.md` and deleted, see below).
2. A small Flask app (`app.py` + `templates/index.html`) — "iM증권 업무 Q&A" — that serves both `bond_manual.md` and `meeting_room_manual.md` as a Q&A web UI, currently backed by local keyword search only (no LLM/embeddings). A question is routed to `meeting_room_manual.md` if it contains a meeting-room keyword (`회의실`/`회의`/`미팅룸`/`미팅`), otherwise it searches `bond_manual.md`.

## Commands

- Install deps: `pip install -r requirements.txt` (just `flask`)
- Run the app: `python app.py` → serves at `http://127.0.0.1:5000`
- **Reloader gotcha**: Flask's `debug=True` reloader only watches `.py` files. Editing `bond_manual.md`, `meeting_room_manual.md`, or `templates/index.html` does NOT restart the process for the former two — both are loaded once into memory at startup (`BOND_CHUNKS`/`MEETING_ROOM_CHUNKS`), so a regenerated manual is invisible until you kill and relaunch `python app.py`. (Template edits *do* pick up live — Flask re-renders Jinja templates per request.)
- No build/lint/test commands exist in this repo.

## Source material

- `BOND_MANUAL.xlsx` — source spreadsheet, 19 non-empty sheets + 1 empty (`DFD`): 전체, 공시, 종목정보, 계좌거래, 대차, RP정산_펀드이동, 자동반자동, Invalid, 마감, 원천세, RBS체결잔고, 상품담보, 배분처리, 장내채권, REPO, 기타화면, 매도대행, 해외채권, CDCP. A `과제` sheet existed originally but has since been deleted from the source file — its content (which used to back a "진행 중 과제 메모" section) is intentionally absent from `skill_2.md` now. Sheet names are garbled by default codepage in some shells — read with `PYTHONIOENCODING=utf-8` or via `openpyxl` in Python directly rather than a raw terminal dump.
- `REC_MANUAL.mp4` — a large (~270MB) recording (meeting-room reservation system demo, on-screen id "화면번호 3691"). `REC_SUB.txt` is a manually-provided transcript of it (audio narration only, no visual/UI description). No transcription tooling (ffmpeg/whisper/pydub) is installed in this environment.
- `meeting_room_manual.md` — meeting-room reservation manual. Originally distilled from `REC_SUB.txt` alone; later cross-checked by uploading `REC_MANUAL.mp4` directly to the Gemini API (Files API + `interactions.create`, model `gemini-3.5-flash`, key from the root `AutoX/.env`'s `GEMINI_API_KEY` — not this folder's `.env`) and merging in what the transcript-only pass had missed (UI element names, floor/room options, the "입력 후 확인 팝업" completion step). See "Working conventions" below for the PII finding from that pass.
- `bond_manual.md` — bond back-office manual, distilled from `BOND_MANUAL.xlsx`, one `##` section per sheet (currently 19, numbered 1–19 in sheet order).
- `.env` — contains `OPENAI_API_KEY` and `OPENAI_MODEL`. Treat as sensitive; do not print its contents or commit it anywhere. The key has been returning 401 (invalid) on every test so far — LLM-based answer generation is wanted but not yet wired up because of this.

## app.py architecture (retrieval)

Retrieval is local keyword search, no embeddings/LLM involved. `bond_manual.md` and `meeting_room_manual.md` are each chunked independently into `BOND_CHUNKS`/`MEETING_ROOM_CHUNKS`; `is_meeting_room_question` (checks for `회의실`/`회의`/`미팅룸`/`미팅` in the question) picks which chunk set a given question searches — the two manuals are never mixed in one search.

1. `split_into_chunks` splits each manual on `##` headers into per-topic sections, loaded once at process start (see reloader gotcha above).
2. `relevant_tokens` tokenizes the question (regex splits into alnum-run tokens like `TR4014`/`NSCG07M00` and separate Hangul-run tokens), drops short/stopword-prefixed tokens, and expands `TR####` tokens to also match the bare number, since the manual uses both forms interchangeably.
3. `token_idf` downweights tokens that appear in most sections (e.g. "처리") so generic words don't drown out topic-specific ones — this was needed because raw substring-count scoring alone let long/generic sections win ties over the actually-relevant one.
4. `score_chunk` ranks whole sections; the top `TOP_K=3` are kept.
5. `extract_snippets` re-scores individual lines within those top sections and returns only the top-scoring lines (bullets/table rows), reattaching a table's header row via `find_table_header` for context — this is what keeps answers short instead of dumping entire sections.

If a future pass adds LLM-based generation, the intended shape (per prior discussion) is to keep this retrieval as-is and only send the already-extracted snippets + question to a chat completion for phrasing — not to re-implement retrieval via embeddings.

## Working conventions for this project

- **Preserve original identifiers verbatim.** TR numbers (e.g. `TR4014`), table names (e.g. `NSCG07M00`), and column names (e.g. `BD_GDS_TP`) must be copied exactly as they appear in the source — never paraphrase or "correct" them.
- **When reorganizing a sheet/source into `meeting_room_manual.md` or `bond_manual.md`**, dump the full source content first and diff it against what's already written rather than summarizing from memory — prior passes have found small gaps (a stray note, an offhand aside in an unrelated column, or in one case an entire sheet having been deleted) that are easy to miss without a full diff.
- **Personal names found in source material must not reach the output doc.** `BOND_MANUAL.xlsx` free-text cells have contained employee names attached to job titles (e.g. "조성준 부장"); when regenerating `bond_manual.md`, pseudonymize these to role/title only (or drop the attribution) rather than copying the name through. The same rule applies to `meeting_room_manual.md`: a Gemini re-analysis of `REC_MANUAL.mp4` found a real employee name and 사번-형 식별자 visible on-screen (monitor bezel label + '예약자' field) that the audio-only transcript never surfaced — that finding was pseudonymized out of the doc rather than recorded.
- **The mp4 recording may contain sensitive internal detail, and sending it to an external API cannot be undone once done.** Do not send it, or audio/frames extracted from it, to any external API without explicit user confirmation for that specific use — this was declined once before, then later explicitly authorized for a Gemini Files API analysis (see `meeting_room_manual.md`'s provenance note). Treat each new external-API use as needing its own confirmation, not a standing approval. Default to asking, or to using a manually-provided transcript, over automatic processing when unconfirmed.
- **Domain context**: `bond_manual.md` documents a Korean securities firm's (iM증권) bond (채권) back-office system — TR screens (numbered UI transactions), their underlying tables, settlement (결제)/tax withholding (원천세) flows, and adjacent products (CP, 전단채, CD, REPO, 대차, 해외채권, 랩).
