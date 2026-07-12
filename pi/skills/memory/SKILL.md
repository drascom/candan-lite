---
name: memory
description: Store/search durable memory and set timed reminders. Use when the user asks
  to be reminded of something, says something should be remembered, when you learn an
  important durable fact, or when the user asks about the past.
---

# Memory rules

Your identity is the user in `$MEM_USER`. The active user and the memory path are given to
you explicitly at boot; use that path. If `$MEM_USER` is empty (guest) there is NO memory —
do not write, do not search, do not open files.

## Reminders ("hatırlat")
If the user asks to BE REMINDED of something ("bana ... hatırlat", "yarın şunu söyle"),
that is **not** a memory note → call **`reminder_add`**. `memory_add` stores durable facts;
it never fires at a time.

- **Never compute the time yourself.** You do not know the current time reliably (your
  process stays warm for days). Just forward what the user said:
  - "10 dakika sonra su içmemi hatırlat" → `reminder_add({ text: "su iç", in_minutes: 10 })`
  - "saat 1'de yatmamı hatırlat" → `reminder_add({ text: "yatma vakti", at: "01:00" })`
  - "13 Temmuz akşam 8'de" → `reminder_add({ text: "...", at: "2026-07-13 20:00" })`
- The server resolves the real date/time (Europe/London). A clock time that already passed
  today automatically rolls over to tomorrow — do not "fix" it yourself.
- The tool returns the resolved time; confirm it back in one short sentence.
- When it is due, the assistant calls the user by name and delivers it — nothing else to do.
- List with `reminder_list`, cancel with `reminder_cancel` (id or approximate text).

## Writing notes
Use `memory_add` (never grep/append/write files by hand).
- DEFAULT: private. `memory_add({ text: "<one-line fact>" })` → the user's own notes.
- Shared family memory: `memory_add({ text, scope: "family" })`. If the user explicitly
  asks ("aileye not et", "herkes bilsin"), write it directly.
- **If the content concerns the family's SHARED life** — a family event, a plan/date that
  affects everyone, house matters, shared shopping/visits — do not quietly write it to
  private: **ask ONE short question**, then store according to the answer.
  - "yarın akşam ailece yemek yiyeceğiz, not al" → *"Bunu aile notuna mı yazayım, yoksa
    sana özel mi kalsın?"* → then `scope: "family"` or private.
  - "cumartesi diş randevum var" → personal, do NOT ask, write to private.
  - When in doubt, ASK. One sentence max (this is a voice conversation).
- Never move private information into shared memory on your own; no `family` without consent.
- Project note (adults only): `memory_add({ text, scope: "project:<name>" })`.
- You cannot write into another user's memory; the tool always writes as your identity.

## Correcting / moving (do NOT add a new note)
If the user corrects a note's **place or content** ("hayır, bunu aile notuna yaz", "orayı
şöyle değiştir", "yanlış yazmışsın") → **do not add, MOVE/UPDATE**:
- `memory_add({ text: "<new/same note>", scope: "family", replaces: "<old note text>" })`
  → the old entry is DELETED and the new one written into the target scope.
- `replaces` may be approximate (diacritics/case do not matter).
- If the text is identical you may omit `replaces`: the tool never copies a note across
  scopes, it MOVES it (deleting the old one).
- If the same/near-identical note already exists the tool does NOT add it twice ("Zaten
  kayıtlı"). That is not an error — tell the user you had already noted it; do not retry.

## Searching
Use `memory_search`.
- If what was loaded at boot (profile + family) is not enough:
  `memory_search({ query: "<keywords>", limit: 5 })`. It only returns scopes you may see
  (own private + family + [if adult] projects); Turkish diacritic-insensitive
  (çocuk↔cocuk). Another user's note never shows up.
- Keep answers short; never read results verbatim — summarise them for speech.

## Consolidation
`profile.md` and `family.md` are injected into your context on EVERY turn, so their size is
latency. When the worker tells you a file is over the limit, call `memory_consolidate`:
keep the durable facts in `text`, and pass every line you removed in `demoted` (they are
appended to `notes/` and stay searchable — nothing may be lost). Only do this when asked.

Policy (default-private, family-only-on-request, isolation) is enforced inside the tools;
follow these rules. If `$MEM_USER` is empty (guest) the tools return "no memory".
