/**
 * candan-lite — web_search: LOKAL, anahtarsız web arama extension'ı.
 *
 * NEDEN: `web_search` pi'nin built-in'i DEĞİL; global `npm:pi-web-access`
 * paketinden geliyordu. PI_ISOLATED (--no-extensions) ile o paket artık
 * yüklenmiyor → yetenek kayboldu. Beyin VPS'te çalışacak ve orada global pi
 * kurulumu OLMAYACAK, o yüzden arama yeteneği projeye-lokal olmalı.
 *
 * SAĞLAYICI ZİNCİRİ (ilk çalışan kazanır):
 *   1. BRAVE  — yalnızca BRAVE_API_KEY varsa. GÜVENİLİR yol: resmi JSON API,
 *      bot-blok/captcha YOK, her IP'den (VPS dahil) çalışır. Ücretsiz kota mevcut.
 *   2. QWANT  — ANAHTARSIZ varsayılan (api.qwant.com/v3/search/web). Kutudan
 *      çıktığı gibi çalışır; JSON döner, Türkçe sorgularda güncel sonuç verir.
 *      UYARI: önünde DataDome var → çok sık/atımlı istekte IP geçici olarak
 *      captcha'ya (403) düşebilir. Ev kullanımında (günde birkaç arama) sorun
 *      değil; garanti isteniyorsa BRAVE_API_KEY eklenmeli.
 *
 * Denenip ELENEN anahtarsızlar: DuckDuckGo html+lite → bot-blok (HTTP 202, GET+POST);
 * Mojeek → ardışık istekte captcha; public SearxNG → bot duvarı; DDG Instant Answer
 * API → captcha yok ama sadece ansiklopedik özet (hava/haber/skor için işe yaramaz).
 *
 * NEDEN Qwant'ta `curl` (node fetch DEĞİL): DataDome, Node undici fetch'inin TLS
 * parmak izini TANIYIP captcha (403) veriyor — tarayıcı başlıklarını birebir taklit
 * etmek DE işe yaramıyor (denendi). Aynı istek `curl` ile HTTP 200 dönüyor. curl
 * macOS'ta ve pratikte her Linux VPS'te var; yoksa tool nazikçe pes eder. Argümanlar
 * argv olarak geçer (shell YOK) → sorgu enjeksiyonu mümkün değil. Brave'de bu sorun
 * olmadığı için orada düz `fetch` kullanılır.
 *
 * SESLİ ASİSTAN İÇİN: sonuç KISA ve konuşulabilir (link listesi/HTML yok);
 * ilk N sonucun başlık + kısa özeti, sade metin. Gecikme kritik → timeout var,
 * yavaşsa/erişilemezse NAZİKÇE pes eder (çökmez, tur bloklanmaz).
 *
 * ENV:
 *   WEB_SEARCH_ENABLED    (default true)  → false ise tool hiç kaydedilmez
 *   WEB_SEARCH_TIMEOUT_MS (default 6000)  → istek zaman aşımı
 *   WEB_SEARCH_LOCALE     (default en_GB) → Qwant locale (tr_TR DESTEKLENMİYOR;
 *                          en_GB Türkçe sorguda Türkçe sonuç döndürüyor)
 *   BRAVE_API_KEY         (opsiyonel)     → varsa Brave kullanılır (garantili yol)
 *
 * SADECE worker'ın pi'sinde `-e pi/extensions/websearch/index.ts` ile yüklenir
 * (global DEĞİL). PI_ISOLATED bunu bozmaz: `-e` explicit yoldur.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { execFile } from "node:child_process";

const ENABLED = (process.env.WEB_SEARCH_ENABLED ?? "true").trim().toLowerCase();
const IS_ENABLED = ["1", "true", "yes", "on"].includes(ENABLED);
const TIMEOUT_MS = Number(process.env.WEB_SEARCH_TIMEOUT_MS || 6000) || 6000;
const LOCALE = (process.env.WEB_SEARCH_LOCALE || "en_GB").trim();
const BRAVE_KEY = (process.env.BRAVE_API_KEY || "").trim();

const UA =
	"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
	"(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36";

interface Hit {
	title: string;
	desc: string;
}

const ENTITIES: Record<string, string> = {
	amp: "&",
	lt: "<",
	gt: ">",
	quot: '"',
	apos: "'",
	nbsp: " ",
	"#39": "'",
};

/** Qwant başlık/özetlerinde <b> vurgu etiketleri ve HTML entity'leri var → sade metne indir. */
function plain(s: unknown): string {
	if (typeof s !== "string") return "";
	return s
		.replace(/<[^>]*>/g, "")
		.replace(/&(#x?[0-9a-fA-F]+|[a-zA-Z]+);/g, (m, e: string) => {
			const key = e.toLowerCase();
			if (ENTITIES[key] !== undefined) return ENTITIES[key];
			if (key.startsWith("#x")) return String.fromCodePoint(parseInt(key.slice(2), 16));
			if (key.startsWith("#")) return String.fromCodePoint(parseInt(key.slice(1), 10));
			return m;
		})
		.replace(/\s+/g, " ")
		.trim();
}

/** Konuşulabilirlik: uzun özetleri kelime sınırında kırp. */
function clip(s: string, max: number): string {
	if (s.length <= max) return s;
	const cut = s.slice(0, max);
	const sp = cut.lastIndexOf(" ");
	return (sp > max * 0.6 ? cut.slice(0, sp) : cut).trim() + "…";
}

/** Qwant: data.result.items.mainline[] → type==="web" grubundaki sonuçlar. */
function parseQwant(data: any): Hit[] {
	const mainline = data?.data?.result?.items?.mainline;
	if (!Array.isArray(mainline)) return [];
	const out: Hit[] = [];
	for (const group of mainline) {
		if (group?.type !== "web" || !Array.isArray(group.items)) continue;
		for (const r of group.items) {
			const title = plain(r?.title);
			if (!title) continue;
			out.push({ title, desc: plain(r?.desc) });
		}
	}
	return out;
}

/** curl ile GET (argv — shell yok). Gövdeyi döner; hata/timeout'ta throw eder. */
function curlGet(url: string): Promise<string> {
	const secs = Math.max(1, Math.ceil(TIMEOUT_MS / 1000));
	const args = [
		"-s", // sessiz
		"-f", // HTTP >=400 → çıkış kodu != 0 (captcha/403 burada yakalanır)
		"-m", String(secs), // curl'ün kendi zaman aşımı
		"-A", UA,
		"-H", "Accept: application/json",
		url,
	];
	return new Promise((resolve, reject) => {
		execFile(
			"curl",
			args,
			// Süreç seviyesinde ikinci emniyet kemeri + makul çıktı sınırı.
			{ timeout: TIMEOUT_MS + 1000, maxBuffer: 4 * 1024 * 1024, encoding: "utf8" },
			(err, stdout) => (err ? reject(err) : resolve(stdout)),
		);
	});
}

/** Qwant (anahtarsız varsayılan). curl üzerinden — DataDome node fetch'i captcha'lıyor. */
async function searchQwant(query: string, limit: number): Promise<Hit[]> {
	const url =
		"https://api.qwant.com/v3/search/web?" +
		new URLSearchParams({
			q: query,
			count: "10", // Qwant yalnızca 10 kabul ediyor; kırpmayı biz yapıyoruz.
			locale: LOCALE,
			offset: "0",
			device: "desktop",
		}).toString();

	return parseQwant(JSON.parse(await curlGet(url))).slice(0, limit);
}

/** Brave Search API (BRAVE_API_KEY varsa). Bot-blok yok → VPS'te garantili yol. */
async function searchBrave(query: string, limit: number): Promise<Hit[]> {
	const url =
		"https://api.search.brave.com/res/v1/web/search?" +
		new URLSearchParams({ q: query, count: String(limit) }).toString();

	const res = await fetch(url, {
		headers: { Accept: "application/json", "X-Subscription-Token": BRAVE_KEY },
		signal: AbortSignal.timeout(TIMEOUT_MS),
	});
	if (!res.ok) throw new Error(`Brave HTTP ${res.status}`);
	const data: any = await res.json();
	const results = data?.web?.results;
	if (!Array.isArray(results)) return [];
	return results
		.map((r: any) => ({ title: plain(r?.title), desc: plain(r?.description) }))
		.filter((h: Hit) => h.title)
		.slice(0, limit);
}

/** Anahtar varsa Brave, yoksa anahtarsız Qwant. */
async function search(query: string, limit: number): Promise<{ hits: Hit[]; provider: string }> {
	if (BRAVE_KEY) return { hits: await searchBrave(query, limit), provider: "brave" };
	return { hits: await searchQwant(query, limit), provider: "qwant" };
}

function fmt(hits: Hit[]): string {
	return hits
		.map((h, i) => {
			const d = clip(h.desc, 180);
			return `${i + 1}. ${clip(h.title, 110)}${d ? `\n   ${d}` : ""}`;
		})
		.join("\n");
}

export default function webSearchExtension(pi: ExtensionAPI) {
	if (!IS_ENABLED) return; // WEB_SEARCH_ENABLED=false → tool hiç kaydedilmez.

	pi.registerTool({
		name: "web_search",
		label: "Web Search",
		// Açıklama BİLEREK geniş: eski hali aramayı yalnız "güncel bilgi"ye kilitliyordu →
		// model bilmediği bir yeri (kişi/kurum/ürün) aramak yerine UYDURUYORDU.
		// Ölçüm (26B, N=6): "Potterspar nedir?" eski 0/6 → yeni 6/6; "Potter's Bar nerede?"
		// eski 0/6 → yeni 6/6. Aşırı tetikleme YOK: "Merhaba, nasılsın?" ikisinde de 0/6.
		description:
			"İnternette bilgi ara. ŞU DURUMLARDA MUTLAKA kullan: (a) güncel/değişken bilgi " +
			"(haber, hava durumu, skor, fiyat, 'şu an', 'bugün', 'son durum'); (b) kullanıcı " +
			"açıkça aramanı istediğinde ('internetten bak', 'ara'); (c) sorulan şeyi BİLMİYORSAN " +
			"ya da emin değilsen (yer, kişi, kurum, ürün adı vb.) — TAHMİN ETME, ARA. " +
			"Emin olmadığın bir şeyi uydurmaktansa aramak her zaman doğrudur. " +
			"Sonuç: ilk birkaç web sonucunun başlık + kısa özeti (sade metin).",
		promptSnippet:
			"Bilmediğin, emin olmadığın ya da güncel olabilecek bir şey sorulduğunda web_search çağır — " +
			"tahmin etme. Kullanıcı 'ara/internetten bak' derse mutlaka çağır. " +
			"Sonuçları KISA ve konuşma diliyle özetle; link/URL okuma.",
		parameters: Type.Object({
			query: Type.String({ description: "Arama sorgusu (kullanıcının dilinde, kısa anahtar kelimeler)." }),
			limit: Type.Optional(
				Type.Number({ description: "En fazla sonuç (varsayılan 3).", minimum: 1, maximum: 5 }),
			),
		}),
		async execute(_id, params: { query: string; limit?: number }) {
			const query = (params.query || "").trim();
			if (!query) return { content: [{ type: "text" as const, text: "Boş sorgu." }] };
			const limit = Math.min(Math.max(params.limit ?? 3, 1), 5);

			let hits: Hit[];
			let provider: string;
			try {
				({ hits, provider } = await search(query, limit));
			} catch (e: any) {
				// Timeout / ağ / captcha / curl yok / bozuk JSON → sesli akış ÇÖKMESİN.
				// Model bu metni görüp "şu an arayamadım" diyebilir; tur bloklanmaz.
				const why = e?.killed
					? "zaman aşımı"
					: e?.code === "ENOENT"
						? "curl bulunamadı"
						: e?.message || String(e);
				return {
					content: [{ type: "text" as const, text: "Web aramasına şu an ulaşamadım." }],
					details: { error: String(why).slice(0, 200) },
				};
			}

			if (hits.length === 0)
				return { content: [{ type: "text" as const, text: `"${query}" için web sonucu bulunamadı.` }] };

			return {
				content: [{ type: "text" as const, text: fmt(hits) }],
				details: { count: hits.length, provider },
			};
		},
	});
}
