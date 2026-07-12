/**
 * family-memory — event store (reminders / finished tasks) + consolidation core.
 *
 * Standalone module: uses ONLY `node:` builtins (no pi API, no typebox), so it can be
 * run and tested with plain node:
 *     node --experimental-strip-types pi/extensions/family-memory/events.ts selftest
 *
 * ── Why SQLite, not markdown?
 * Notes stay in markdown (human-readable, authoritative). But a reminder is an EVENT:
 * it needs time queries (due_at <= now), status transitions (pending → delivered) and a
 * retry counter (attempts). Running that on markdown is brittle. Events live in a
 * separate file: memory/events.db (override with EVENTS_DB).
 * `.index/mem.db` is NOT used: that file is an FTS cache which is DROPped and rebuilt on
 * every call, i.e. it must stay disposable. Events are authoritative state → own file.
 *
 * ── TIME (the critical part)
 * The model does NOT know "now": the warm pi process lives for days, so any date injected
 * at boot goes stale. Therefore `due_at` is computed HERE, in code — never by the model.
 * The tool only receives what the user said (relative `in_minutes` or wall-clock `at`);
 * resolution happens server-side against the real `now` in CANDAN_TZ (Europe/London).
 * A wall-clock time that already passed today rolls over to the NEXT DAY
 * (at 23:50 "remind me at 1" → tomorrow 01:00).
 *
 * Contract with the worker (separate Python process): the shared SQLite file. This module
 * never depends on the worker.
 */
import * as fs from "node:fs";
import * as path from "node:path";

export const TZ = process.env.CANDAN_TZ || "Europe/London";
/** Hard limit for context files (profile.md / family.md) — injected on EVERY turn. */
export const CONTEXT_LIMIT = Number(process.env.MEM_CONTEXT_LIMIT_BYTES || 2048);

export type Kind = "reminder" | "task_done";
export type Status = "pending" | "delivered" | "cancelled";

export interface Ev {
	id: number;
	kind: Kind;
	user: string;
	text: string;
	requested_at: string; // ISO UTC — WHEN IT WAS ASKED FOR
	due_at: string; // ISO UTC — WHEN IT SHOULD HAPPEN
	status: Status; // WHAT HAPPENED
	attempts: number;
	delivered_at: string | null;
	source: string | null;
}

// ── Paths (env-overridable; no project layout hardcoded) ────────────────────
export function memDir(cwd: string): string {
	return process.env.MEM_DIR || path.join(cwd, "memory");
}
export function eventsDbPath(cwd: string): string {
	return process.env.EVENTS_DB || path.join(memDir(cwd), "events.db");
}

// ── Timezone arithmetic (no deps; Intl is enough) ───────────────────────────
interface Wall {
	y: number;
	mo: number;
	d: number;
	h: number;
	mi: number;
}

/** Wall-clock fields of an instant in `tz`. */
export function wallOf(date: Date, tz: string = TZ): Wall {
	const p = new Intl.DateTimeFormat("en-US", {
		timeZone: tz,
		hour12: false,
		year: "numeric",
		month: "2-digit",
		day: "2-digit",
		hour: "2-digit",
		minute: "2-digit",
	}).formatToParts(date);
	const g = (t: string) => Number(p.find((x) => x.type === t)!.value);
	return { y: g("year"), mo: g("month"), d: g("day"), h: g("hour") % 24, mi: g("minute") };
}

/** UTC offset of `tz` at that instant (ms). Handles DST automatically. */
function tzOffsetMs(date: Date, tz: string): number {
	const w = wallOf(date, tz);
	const asUtc = Date.UTC(w.y, w.mo - 1, w.d, w.h, w.mi, date.getUTCSeconds());
	return asUtc - date.getTime();
}

/** Wall-clock time in `tz` → real instant (UTC Date). Two passes = DST-correct. */
export function zonedToUtc(w: Wall, tz: string = TZ): Date {
	const naive = Date.UTC(w.y, w.mo - 1, w.d, w.h, w.mi);
	let off = tzOffsetMs(new Date(naive), tz);
	off = tzOffsetMs(new Date(naive - off), tz); // 2nd pass fixes DST boundaries
	return new Date(naive - off);
}

/** Human-readable local time (Turkish locale — it is read back to the user). */
export function fmtLocal(d: Date, tz: string = TZ): string {
	return new Intl.DateTimeFormat("tr-TR", {
		timeZone: tz,
		dateStyle: "full",
		timeStyle: "short",
	}).format(d);
}

export interface DueOpts {
	at?: string; // wall clock: "HH:MM" | "H" | "YYYY-MM-DD HH:MM" | "YYYY-MM-DD"
	in_minutes?: number; // relative: minutes from now
}

/**
 * The SERVER computes due_at (never the model). Relative → now + minutes.
 * Wall clock → resolved in tz; if only a time-of-day was given and it already passed
 * today, it rolls over to tomorrow.
 */
export function resolveDue(
	now: Date,
	o: DueOpts,
	tz: string = TZ,
): { due: Date } | { error: string } {
	if (o.in_minutes != null && Number.isFinite(o.in_minutes)) {
		const m = Number(o.in_minutes);
		if (m < 0) return { error: "in_minutes cannot be negative." };
		return { due: new Date(now.getTime() + m * 60_000) };
	}
	const raw = (o.at || "").trim();
	if (!raw) return { error: "No time given: pass 'in_minutes' or 'at'." };

	let m = /^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{1,2})[:.](\d{2}))?$/.exec(raw);
	if (m) {
		const w: Wall = {
			y: +m[1],
			mo: +m[2],
			d: +m[3],
			h: m[4] == null ? 9 : +m[4], // date without time → 09:00
			mi: m[5] == null ? 0 : +m[5],
		};
		if (w.h > 23 || w.mi > 59) return { error: "Invalid time." };
		return { due: zonedToUtc(w, tz) };
	}
	m = /^(\d{1,2})(?:[:.](\d{2}))?$/.exec(raw); // "01:00" | "1.00" | "1"
	if (m) {
		const h = +m[1];
		const mi = m[2] == null ? 0 : +m[2];
		if (h > 23 || mi > 59) return { error: "Invalid time." };
		const n = wallOf(now, tz);
		let due = zonedToUtc({ y: n.y, mo: n.mo, d: n.d, h, mi }, tz);
		if (due.getTime() <= now.getTime()) {
			// Wall-clock time already passed today → roll to the next day
			// (at 23:50, "at 1 o'clock" → TOMORROW 01:00).
			const t = new Date(Date.UTC(n.y, n.mo - 1, n.d) + 86_400_000);
			due = zonedToUtc(
				{ y: t.getUTCFullYear(), mo: t.getUTCMonth() + 1, d: t.getUTCDate(), h, mi },
				tz,
			);
		}
		return { due };
	}
	return { error: `Unrecognized time format: ${raw}` };
}

// ── SQLite (node:sqlite; no external deps) ──────────────────────────────────
let _DatabaseSync: any = null;
let _tried = false;

export async function sqliteReady(): Promise<boolean> {
	if (!_tried) {
		_tried = true;
		try {
			_DatabaseSync = (await import("node:sqlite")).DatabaseSync;
		} catch {
			_DatabaseSync = null;
		}
	}
	return _DatabaseSync != null;
}

const SCHEMA = `
CREATE TABLE IF NOT EXISTS events(
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  kind         TEXT NOT NULL,
  user         TEXT NOT NULL,
  text         TEXT NOT NULL,
  requested_at TEXT NOT NULL,
  due_at       TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'pending',
  attempts     INTEGER NOT NULL DEFAULT 0,
  delivered_at TEXT,
  source       TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_due ON events(status, due_at);`;

/** Open the event DB (schema is idempotent). Returns null if node:sqlite is missing. */
export async function openEvents(cwd: string): Promise<any | null> {
	if (!(await sqliteReady())) return null;
	try {
		const p = eventsDbPath(cwd);
		fs.mkdirSync(path.dirname(p), { recursive: true });
		const db = new _DatabaseSync(p);
		db.exec(SCHEMA);
		return db;
	} catch {
		return null;
	}
}

export function addEvent(
	db: any,
	e: { kind: Kind; user: string; text: string; due: Date; now: Date; source?: string },
): number {
	db.prepare(
		"INSERT INTO events(kind,user,text,requested_at,due_at,status,attempts,source) " +
			"VALUES(?,?,?,?,?,'pending',0,?)",
	).run(e.kind, e.user, e.text, e.now.toISOString(), e.due.toISOString(), e.source || null);
	return Number(db.prepare("SELECT last_insert_rowid() AS id").get().id);
}

export function listEvents(db: any, user: string, status: Status = "pending", limit = 20): Ev[] {
	return db
		.prepare("SELECT * FROM events WHERE user=? AND status=? ORDER BY due_at LIMIT ?")
		.all(user, status, limit) as Ev[];
}

/** Cancel by id or by (fuzzy) text. Returns how many rows were cancelled. */
export function cancelEvent(db: any, user: string, o: { id?: number; text?: string }): number {
	if (o.id != null) {
		const r = db
			.prepare("UPDATE events SET status='cancelled' WHERE id=? AND user=? AND status='pending'")
			.run(o.id, user);
		return Number(r.changes || 0);
	}
	const q = (o.text || "").trim().toLowerCase();
	if (!q) return 0;
	let n = 0;
	for (const e of listEvents(db, user, "pending", 100)) {
		const t = e.text.toLowerCase();
		if (t.includes(q) || q.includes(t)) {
			db.prepare("UPDATE events SET status='cancelled' WHERE id=?").run(e.id);
			n++;
		}
	}
	return n;
}

// ── Consolidation (context bloat) ───────────────────────────────────────────
// profile.md + family.md are injected into the model on EVERY turn → their size is
// latency. Once over the limit: a summary stays in the file and the event-like lines are
// demoted into notes/ (notes are NOT injected, only FTS-searched → nothing is lost).
const LINE_RE = /^-\s*\[(\d{4}-\d{2}-\d{2})\]\s*(.+)$/;

const ym = (d = new Date()) => d.toISOString().slice(0, 7);
const today = (d = new Date()) => d.toISOString().slice(0, 10);

/** The context file being consolidated. */
export function contextFile(cwd: string, user: string, which: "profile" | "family"): string {
	const root = memDir(cwd);
	return which === "family"
		? path.join(root, "family.md")
		: path.join(root, "users", user, "profile.md");
}

/** Where demoted lines land (not injected into context, still searchable via FTS). */
export function notesFile(cwd: string, user: string, which: "profile" | "family"): string {
	const root = memDir(cwd);
	return which === "family"
		? path.join(root, "family-notes", ym() + ".md")
		: path.join(root, "users", user, "notes", ym() + ".md");
}

export function fileSize(p: string): number {
	try {
		return fs.statSync(p).size;
	} catch {
		return 0;
	}
}

/**
 * Consolidate: `text` (new content, ≤ CONTEXT_LIMIT) replaces the context file and the
 * `demoted` lines are appended to notes/ (original `- [YYYY-MM-DD]` dates preserved).
 * LOSSLESS BY CONSTRUCTION: demoted lines are written to notes BEFORE the context file is
 * overwritten.
 */
export function consolidate(
	cwd: string,
	user: string,
	which: "profile" | "family",
	text: string,
	demoted: string[],
):
	| { before: number; after: number; moved: number; file: string; notes: string }
	| { error: string } {
	const body = (text || "").trim();
	if (!body) return { error: "Empty text — nothing consolidated." };
	const size = Buffer.byteLength(body, "utf-8");
	if (size > CONTEXT_LIMIT)
		return {
			error: `New text is still too big (${size} bytes > ${CONTEXT_LIMIT}). Demote more lines to notes and retry.`,
		};

	const file = contextFile(cwd, user, which);
	const notes = notesFile(cwd, user, which);
	const before = fileSize(file);

	// 1) Close the data-loss window FIRST: write demoted lines to notes.
	const lines = (demoted || [])
		.map((s) => (s || "").trim())
		.filter(Boolean)
		.map((s) => {
			const m = LINE_RE.exec(s);
			return m ? `- [${m[1]}] ${m[2].trim()}` : `- [${today()}] ${s.replace(/^-\s*/, "")}`;
		});
	if (lines.length) {
		fs.mkdirSync(path.dirname(notes), { recursive: true });
		fs.appendFileSync(notes, lines.join("\n") + "\n", "utf-8");
	}
	// 2) Only then swap the context file (atomic).
	fs.mkdirSync(path.dirname(file), { recursive: true });
	const tmp = file + ".tmp";
	fs.writeFileSync(tmp, body.endsWith("\n") ? body : body + "\n", "utf-8");
	fs.renameSync(tmp, file);

	return { before, after: fileSize(file), moved: lines.length, file, notes };
}

// ── selftest (plain node — no pi, no deps) ──────────────────────────────────
async function selftest(): Promise<number> {
	const os = await import("node:os");
	const results: [string, boolean, string][] = [];
	const ok = (n: string, c: boolean, d = "") => results.push([n, c, d]);

	// (1) THE critical one: at 23:50, "remind me at 1" → due TOMORROW 01:00 (right day?)
	const now = new Date("2026-07-12T22:50:00.000Z"); // = 23:50 London (BST, UTC+1)
	const r1 = resolveDue(now, { at: "01:00" }) as { due: Date };
	ok(
		"(1) 23:50 London + 'at 1' -> TOMORROW 01:00 (midnight rollover)",
		r1.due.toISOString() === "2026-07-13T00:00:00.000Z" &&
			fmtLocal(r1.due).includes("13 Temmuz 2026"),
		`${r1.due.toISOString()} = ${fmtLocal(r1.due)}`,
	);

	// (2) Time not yet passed → stays TODAY (must not jump to tomorrow)
	const now2 = new Date("2026-07-12T09:00:00.000Z"); // 10:00 London
	const r2 = resolveDue(now2, { at: "13:30" }) as { due: Date };
	ok(
		"(2) 10:00 + 'at 13:30' -> TODAY 13:30",
		r2.due.toISOString() === "2026-07-12T12:30:00.000Z",
		`${r2.due.toISOString()} = ${fmtLocal(r2.due)}`,
	);

	// (3) Winter (GMT = UTC): 23:50 + "at 1" → next day 01:00 GMT
	const now3 = new Date("2026-01-15T23:50:00.000Z");
	const r3 = resolveDue(now3, { at: "1" }) as { due: Date };
	ok(
		"(3) GMT (winter) 23:50 + 'at 1' -> tomorrow 01:00 GMT",
		r3.due.toISOString() === "2026-01-16T01:00:00.000Z",
		`${r3.due.toISOString()} = ${fmtLocal(r3.due)}`,
	);

	// (4) Relative
	const r4 = resolveDue(now, { in_minutes: 10 }) as { due: Date };
	ok(
		"(4) in_minutes=10 -> now+10m",
		r4.due.getTime() - now.getTime() === 600_000,
		r4.due.toISOString(),
	);

	// (5) Absolute date+time
	const r5 = resolveDue(now, { at: "2026-12-24 20:00" }) as { due: Date };
	ok(
		"(5) 'YYYY-MM-DD HH:MM' -> exact instant",
		r5.due.toISOString() === "2026-12-24T20:00:00.000Z",
		`${r5.due.toISOString()} = ${fmtLocal(r5.due)}`,
	);

	// (6..8) events.db round-trip
	const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "candan-ev-"));
	process.env.MEM_DIR = path.join(tmp, "memory");
	process.env.EVENTS_DB = path.join(tmp, "memory", "events.db");
	const db = await openEvents(tmp);
	if (!db) {
		ok("(6) node:sqlite", false, "node:sqlite unavailable");
	} else {
		const id = addEvent(db, {
			kind: "reminder",
			user: "ayhan",
			text: "yatma vakti",
			due: r1.due,
			now,
			source: "voice",
		});
		const rows = listEvents(db, "ayhan");
		const e = rows[0];
		ok(
			"(6) events row records requested_at / due_at / status — ALL THREE",
			rows.length === 1 &&
				e.requested_at === now.toISOString() &&
				e.due_at === r1.due.toISOString() &&
				e.status === "pending" &&
				e.attempts === 0,
			`id=${id} requested=${e.requested_at} due=${e.due_at} status=${e.status} attempts=${e.attempts}`,
		);
		const n = cancelEvent(db, "ayhan", { text: "yatma" });
		ok(
			"(7) cancel by text -> drops out of pending",
			n === 1 && listEvents(db, "ayhan").length === 0,
			`cancelled=${n}`,
		);
		addEvent(db, { kind: "reminder", user: "zeynep", text: "x", due: r1.due, now });
		ok(
			"(8) user isolation: another user's reminder is not listed",
			listEvents(db, "ayhan").length === 0,
		);
		db.close();
	}

	// (9) Consolidation: >2KB profile → ≤2KB + content demoted to notes (lossless)
	const prof = contextFile(tmp, "ayhan", "profile");
	fs.mkdirSync(path.dirname(prof), { recursive: true });
	const demoted = Array.from(
		{ length: 60 },
		(_, i) => `- [2026-07-0${(i % 9) + 1}] Event ${i}: a long line summarising the day.`,
	);
	fs.writeFileSync(prof, "# Profile\n" + demoted.join("\n") + "\n");
	const beforeSize = fileSize(prof);
	const c = consolidate(
		tmp,
		"ayhan",
		"profile",
		"# Profile\n- Ayhan lives in London.\n- Two dogs: Oscar, Amy.\n",
		demoted,
	) as any;
	const notesTxt = fs.readFileSync(c.notes, "utf-8");
	ok(
		"(9) consolidation: >2KB profile -> <=2KB, 60 lines demoted to notes (LOSSLESS)",
		beforeSize > CONTEXT_LIMIT &&
			c.after <= CONTEXT_LIMIT &&
			c.moved === 60 &&
			notesTxt.includes("Event 0:") &&
			notesTxt.includes("Event 59:") &&
			/\[2026-07-0\d\]/.test(notesTxt), // original dates preserved
		`before=${beforeSize}B after=${c.after}B moved=${c.moved}`,
	);

	// (10) A new text that is still over the limit must be REJECTED
	const bad = consolidate(tmp, "ayhan", "profile", "x".repeat(CONTEXT_LIMIT + 1), []) as any;
	ok("(10) new text > limit -> REJECTED (limit is enforced)", !!bad.error, bad.error || "");

	fs.rmSync(tmp, { recursive: true, force: true });

	let all = true;
	for (const [n, c2, d] of results) {
		all = all && c2;
		console.log(`  ${c2 ? "PASS" : "FAIL"}  ${n}${d ? `  [${d}]` : ""}`);
	}
	console.log(`[events] RESULT: ${all ? "PASS" : "FAIL"}`);
	return all ? 0 : 1;
}

if (process.argv[1]?.endsWith("events.ts") && process.argv[2] === "selftest") {
	selftest().then((c) => process.exit(c));
}
