/**
 * family-memory — hafıza kimliği (kim yazıyor/okuyor?).
 *
 * Standalone module: uses ONLY `node:` builtins (no pi API), so it can be run and
 * tested with plain node:
 *     node pi/extensions/family-memory/identity.ts selftest
 *
 * ── İKİ KİMLİK KAYNAĞI, TEK KURAL
 * 1) KLASİK mod (kişi başına ayrı pi süreci): kimlik SÜREÇ ömürlüdür → `MEM_USER`
 *    env'i. Worker her spawn'da doğru kişiyi geçirir. Bugünkü davranış, aynen.
 * 2) ORTAK ODA modu (tek sıcak pi süreci, herkes aynı sohbette): kimlik SÜREÇ
 *    ömürlü OLAMAZ — süreç "candan" oturumudur, policy.json'da böyle biri yoktur,
 *    dolayısıyla env'den okunan kimlik hep boş çıkar ve TANINAN kişi bile guest
 *    sayılır (canlı hata, 2026-07-26: notlar sessizce düştü). Bu modda worker her
 *    turda doğrulanmış konuşmacıyı `MEM_TURN_FILE`'a yazar; kimlik TUR ömürlüdür.
 *
 * ── NEDEN DOSYA, NEDEN PROMPT DEĞİL
 * Kimlik prompt'a yazılsaydı modelin gördüğü bir metin olurdu: "ben Ayhan'ım" diyen
 * biri ya da metne gömülü bir talimat kimliği çevirebilirdi. Dosyayı yalnız worker
 * yazar, extension yalnız OKUR; model bu kanala hiç dokunamaz. (Aynı sözleşme
 * events.db'de de kullanılıyor: iki süreç arasında paylaşılan dosya.)
 *
 * ── FAIL-CLOSED
 * MEM_TURN_FILE tanımlıysa kimlik SADECE oradan gelir; dosya yok/bozuk/bayat ise
 * sonuç "" = guest = hafıza yok. Yani en kötü durum "kaydedilmedi", asla "yanlış
 * kişinin hafızasına yazıldı" değil. Bayatlık kuralı
 * docs/TURN-SAFE-SPEAKER-IDENTITY.md ile aynı ruhta: önceki turun kimliği yeni tur
 * için delil değildir.
 */
import * as fs from "node:fs";

/** Dosyadaki kimlik en fazla bu kadar saniye taze sayılır (worker ölürse kilit). */
export const TURN_TTL_SECONDS = Number(process.env.MEM_TURN_TTL || 300) || 300;

/** Kabul edilen kimlik biçimi = worker'ın `_slug()` çıktısı. Dizin adına gittiği
 * için (memory/users/<user>/) burada da doğrulanır: `../` asla geçmez. */
const USER_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;

export interface TurnFile {
	user?: unknown;
	at?: unknown;
}

/** Dosya içeriğinden kimlik. Bozuk/eksik/bayat → "" (guest). Saf fonksiyon: test edilebilir. */
export function parseTurnUser(raw: string | null, nowSec: number, ttl = TURN_TTL_SECONDS): string {
	if (raw === null) return "";
	let j: TurnFile;
	try {
		j = JSON.parse(raw) as TurnFile;
	} catch {
		return "";
	}
	if (!j || typeof j !== "object") return "";
	const at = typeof j.at === "number" && Number.isFinite(j.at) ? j.at : 0;
	if (at <= 0) return "";
	if (nowSec - at > ttl) return ""; // bayat kanıt → kimlik yok
	if (at - nowSec > ttl) return ""; // gelecekten gelen damga → güvenme
	const u = typeof j.user === "string" ? j.user.trim() : "";
	if (!u) return ""; // worker "tanınmadı" dedi
	return USER_RE.test(u) ? u : "";
}

/** Bu süreçte kimlik tur başına mı çözülüyor (ortak oda)? */
export function perTurnIdentity(): boolean {
	return !!(process.env.MEM_TURN_FILE || "").trim();
}

/** Aktif hafıza kimliği. Ortak oda → tur dosyası; klasik → MEM_USER env'i. */
export function memUser(nowSec: number = Date.now() / 1000): string {
	const file = (process.env.MEM_TURN_FILE || "").trim();
	if (!file) return (process.env.MEM_USER || "").trim();
	let raw: string | null;
	try {
		raw = fs.readFileSync(file, "utf-8");
	} catch {
		return ""; // dosya yok/okunamıyor → guest (fail-closed)
	}
	return parseTurnUser(raw, nowSec);
}

// ── selftest (node identity.ts selftest) ────────────────────────────────────
function selftest(): number {
	const results: [string, boolean, string][] = [];
	const ok = (n: string, c: boolean, d = "") => results.push([n, c, d]);
	const now = 1_000_000;
	const f = (o: unknown) => JSON.stringify(o);

	ok("(1) taze + tanınan kimlik geçer", parseTurnUser(f({ user: "ayhan", at: now }), now) === "ayhan");
	ok("(2) boş kimlik (tanınmayan ses) guest", parseTurnUser(f({ user: "", at: now }), now) === "");
	ok("(3) dosya yok → guest", parseTurnUser(null, now) === "");
	ok("(4) bozuk JSON → guest", parseTurnUser("{bozuk", now) === "");
	ok("(5) damgasız → guest", parseTurnUser(f({ user: "ayhan" }), now) === "");
	ok(
		"(6) bayat kimlik (TTL+1) → guest",
		parseTurnUser(f({ user: "ayhan", at: now - TURN_TTL_SECONDS - 1 }), now) === "",
	);
	ok(
		"(7) gelecek damgası → guest",
		parseTurnUser(f({ user: "ayhan", at: now + TURN_TTL_SECONDS + 1 }), now) === "",
	);
	ok(
		"(8) yol kaçışı (../) reddedilir",
		parseTurnUser(f({ user: "../../etc", at: now }), now) === "",
	);
	ok("(9) string olmayan kimlik reddedilir", parseTurnUser(f({ user: 42, at: now }), now) === "");
	ok(
		"(10) TTL sınırında hâlâ geçerli",
		parseTurnUser(f({ user: "havi", at: now - TURN_TTL_SECONDS }), now) === "havi",
	);

	// Env yolu: MEM_TURN_FILE yoksa klasik davranış (MEM_USER), varsa dosya otoritedir.
	const oldUser = process.env.MEM_USER;
	const oldFile = process.env.MEM_TURN_FILE;
	process.env.MEM_USER = "ayhan";
	delete process.env.MEM_TURN_FILE;
	ok("(11) klasik mod: MEM_USER env'i geçerli", memUser() === "ayhan");
	process.env.MEM_TURN_FILE = "/nonexistent/turn-user.json";
	ok("(12) ortak oda: dosya yoksa MEM_USER'a DÜŞMEZ (guest)", memUser() === "");
	if (oldUser === undefined) delete process.env.MEM_USER;
	else process.env.MEM_USER = oldUser;
	if (oldFile === undefined) delete process.env.MEM_TURN_FILE;
	else process.env.MEM_TURN_FILE = oldFile;

	let all = true;
	for (const [n, c, d] of results) {
		all = all && c;
		console.log(`  ${c ? "PASS" : "FAIL"}  ${n}${d ? `  [${d}]` : ""}`);
	}
	console.log(`[identity] RESULT: ${all ? "PASS" : "FAIL"}`);
	return all ? 0 : 1;
}

if (process.argv[1]?.endsWith("identity.ts") && process.argv[2] === "selftest") {
	process.exit(selftest());
}
