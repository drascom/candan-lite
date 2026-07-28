/**
 * family-memory — BEKLEYEN (kimliksiz) notların kuyruğu ve çözümü.
 *
 * Standalone modül: yalnız `node:` builtin'leri kullanır (pi API yok, typebox yok), yani
 * düz node ile koşulur/test edilir:
 *     node pi/extensions/family-memory/pending.ts selftest
 *
 * ── Neden var?
 * DEĞİŞMEZ KURAL: bir hafıza isteği ya YAZILIR, ya SORULUR, ya BEKLEMEYE ALINIR — asla
 * sessizce atılmaz. Kimlik çözülemediğinde (guest / boş kimlik) not buraya,
 * `memory/pending/unattributed.md`'ye düşer ve harness kullanıcıya kimliği SORAR.
 *
 * Ama sormak tek başına yetmedi (canlı, 28 Tem 17:56 ve 18:06): kullanıcı "Ayhan olarak
 * kaydedeceksin" / "Evet" dedi, model "Anladım Ayhan..." dedi ve HİÇBİR ŞEY yazılmadı.
 * Sessiz kayıp, kibar kayba dönüşmüştü. Bu modül cevabı işleyen yolu kurar: bekleyen not
 * OKUNUR, gerçek sahibine yazılır (index.ts) ve kuyruktan DÜŞER — silinmez, `resolved.md`
 * defterine "kaydedildi/atıldı" olarak geçer. Reddedilen not da izli düşer.
 *
 * Satır biçimi (geriye dönük okunur):
 *     - [ISO ts] (scope=family) (kimlik=?) metin
 *     - [ISO ts] (scope=family) metin          ← eski, kimlik alanı yok
 *     - [ISO ts] metin                          ← en eski, scope=private varsayılır
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

export interface PendingNote {
	ts: string; // notun kuyruğa girdiği an (ISO)
	scope: string; // istenen kapsam ("private" | "family" | "project:<ad>")
	who: string; // o anki (çözülemeyen) kimlik — "?" olabilir
	text: string; // notun kendisi
	raw: string; // dosyadaki satırın aynısı (silmek için)
}

/** memDir'i events.ts'ten ALMIYORUZ: bu modül node:sqlite'a dokunmadan koşabilsin. */
function memRoot(cwd: string): string {
	return process.env.MEM_DIR || path.join(cwd, "memory");
}

export function pendingFile(cwd: string): string {
	return path.join(memRoot(cwd), "pending", "unattributed.md");
}

export function resolvedFile(cwd: string): string {
	return path.join(memRoot(cwd), "pending", "resolved.md");
}

function stamp(): string {
	return new Date().toISOString().replace(/\.\d+Z$/, "Z");
}

/** Kimliksiz notu kuyruğa al. Dönen değer: yazılan dosyanın yolu. */
export function queuePending(cwd: string, text: string, scope: string, who: string): string {
	const file = pendingFile(cwd);
	fs.mkdirSync(path.dirname(file), { recursive: true });
	fs.appendFileSync(file, `- [${stamp()}] (scope=${scope}) (kimlik=${who || "?"}) ${text}\n`, "utf-8");
	return file;
}

const FULL_RE = /^-\s*\[([^\]]+)\]\s*\(scope=([^)]*)\)\s*\(kimlik=([^)]*)\)\s*(.+)$/;
const SCOPE_RE = /^-\s*\[([^\]]+)\]\s*\(scope=([^)]*)\)\s*(.+)$/;
const BARE_RE = /^-\s*\[([^\]]+)\]\s*(.+)$/;

/** Tek satırı çözümle. Tanınmayan satır → null (dosyada olduğu gibi KALIR). */
export function parsePendingLine(raw: string): PendingNote | null {
	const line = raw.trim();
	if (!line) return null;
	let m = FULL_RE.exec(line);
	if (m) return { ts: m[1], scope: m[2].trim() || "private", who: m[3].trim(), text: m[4].trim(), raw };
	m = SCOPE_RE.exec(line);
	if (m) return { ts: m[1], scope: m[2].trim() || "private", who: "?", text: m[3].trim(), raw };
	m = BARE_RE.exec(line);
	if (m) return { ts: m[1], scope: "private", who: "?", text: m[2].trim(), raw };
	return null;
}

/** Kuyruktaki notlar, dosyadaki sırayla (en yenisi SONDA). Dosya yoksa boş liste. */
export function readPending(cwd: string): PendingNote[] {
	let txt: string;
	try {
		txt = fs.readFileSync(pendingFile(cwd), "utf-8");
	} catch {
		return [];
	}
	const out: PendingNote[] = [];
	for (const raw of txt.split("\n")) {
		const n = parsePendingLine(raw);
		if (n) out.push(n);
	}
	return out;
}

/** Hangi notlar çözülecek? Belirsizse EN SONDAKİ (en yeni). `all` → hepsi.
 * Basit ve öngörülebilir: başka seçim kuralı YOK. */
export function pickPending(notes: PendingNote[], all: boolean): PendingNote[] {
	if (notes.length === 0) return [];
	return all ? notes.slice() : [notes[notes.length - 1]];
}

/** Verilen notların satırlarını kuyruktan SİL. Dönen: silinen satır sayısı.
 * Yalnızca çözüm YAZILDIKTAN sonra çağrılır → yazma patlarsa not kuyrukta kalır. */
export function dropPending(cwd: string, notes: PendingNote[]): number {
	const file = pendingFile(cwd);
	let txt: string;
	try {
		txt = fs.readFileSync(file, "utf-8");
	} catch {
		return 0;
	}
	const kill = new Set(notes.map((n) => n.raw.trim()));
	if (kill.size === 0) return 0;
	const kept: string[] = [];
	let n = 0;
	for (const raw of txt.split("\n")) {
		if (raw.trim() && kill.has(raw.trim())) {
			n++;
			continue;
		}
		kept.push(raw);
	}
	try {
		fs.writeFileSync(file, kept.join("\n"), "utf-8");
	} catch {
		return 0;
	}
	return n;
}

/** Çözüm defteri: not SESSİZCE silinmez, ne olduğu yazılır (kaydedildi | atildi). */
export function logResolved(
	cwd: string,
	notes: PendingNote[],
	status: "kaydedildi" | "atildi",
	owner: string,
	scope: string,
): void {
	if (notes.length === 0) return;
	const file = resolvedFile(cwd);
	const now = stamp();
	const body = notes
		.map(
			(n) =>
				`- [${now}] (durum=${status}) (sahip=${owner || "-"}) (scope=${scope || n.scope}) ` +
				`(kuyruk=${n.ts}) ${n.text}\n`,
		)
		.join("");
	fs.mkdirSync(path.dirname(file), { recursive: true });
	fs.appendFileSync(file, body, "utf-8");
}

// ── selftest (node pending.ts selftest) ─────────────────────────────────────
function selftest(): number {
	const results: [string, boolean][] = [];
	const ok = (n: string, c: boolean) => results.push([n, c]);

	// Testin gerçek memory/'ye SIZMAMASI için: MEM_DIR bu koşuda geçersiz kılınır.
	const oldMemDir = process.env.MEM_DIR;
	delete process.env.MEM_DIR;
	const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "pending-"));
	const cwd = tmp;

	// (1) biçim: yazılan satır aynen geri okunur
	queuePending(cwd, "Neva kızımın adı", "family", "");
	queuePending(cwd, "Elma 52 kalori", "private", "havi");
	let notes = readPending(cwd);
	ok("(1) iki not okunur", notes.length === 2);
	ok("(2) alanlar doğru", notes[0].scope === "family" && notes[0].who === "?" &&
		notes[0].text === "Neva kızımın adı" && notes[1].who === "havi");

	// (3) belirsizse EN SONDAKİ
	ok("(3) varsayılan = en son not", pickPending(notes, false)[0].text === "Elma 52 kalori");
	ok("(4) all → hepsi", pickPending(notes, true).length === 2);

	// (5) düşürme yalnız seçileni siler
	dropPending(cwd, pickPending(notes, false));
	notes = readPending(cwd);
	ok("(5) yalnız çözülen düştü", notes.length === 1 && notes[0].text === "Neva kızımın adı");

	// (6) çözüm defteri iz bırakır
	logResolved(cwd, notes, "atildi", "", "family");
	const log = fs.readFileSync(resolvedFile(cwd), "utf-8");
	ok("(6) red izli düşer", log.includes("durum=atildi") && log.includes("Neva kızımın adı"));

	// (7) geriye dönük biçimler
	ok("(7a) kimliksiz eski satır", parsePendingLine("- [2026-07-01T10:00:00Z] (scope=family) eski not")?.text === "eski not");
	ok("(7b) sade eski satır",
		parsePendingLine("- [2026-07-01T10:00:00Z] cok eski not")?.scope === "private");
	ok("(7c) satır olmayan → null", parsePendingLine("# başlık") === null);

	// (8) boş kuyruk patlamaz
	const empty = fs.mkdtempSync(path.join(os.tmpdir(), "pending-empty-"));
	ok("(8) boş kuyruk → boş liste", readPending(empty).length === 0 && dropPending(empty, []) === 0);

	fs.rmSync(tmp, { recursive: true, force: true });
	fs.rmSync(empty, { recursive: true, force: true });
	if (oldMemDir === undefined) delete process.env.MEM_DIR;
	else process.env.MEM_DIR = oldMemDir;

	let all = true;
	for (const [n, c] of results) {
		all = all && c;
		console.log(`  ${c ? "PASS" : "FAIL"}  ${n}`);
	}
	console.log(`[pending] RESULT: ${all ? "PASS" : "FAIL"}`);
	return all ? 0 : 1;
}

if (process.argv[1]?.endsWith("pending.ts") && process.argv[2] === "selftest") {
	process.exit(selftest());
}
