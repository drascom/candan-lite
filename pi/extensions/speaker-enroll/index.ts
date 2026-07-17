/**
 * speaker-enroll — pi extension: SESLE kayıt (voice enrollment) sinyali.
 *
 * Tool:
 *  - enroll_speaker(name) : kullanıcı sesini kaydettirmek istediğinde, adı ONAYLATTIKTAN
 *                           sonra çağrılır.
 *
 * Bu tool KENDİSİ kayıt YAPMAZ — mode-switch/index.ts ile aynı desen: sadece bir SİNYAL.
 * Sesli worker (Python, worker/pi_brain.py) pi'nin stdout event akışındaki toolCall'ı
 * görür (_detect_enroll_signal) ve turun sonunda _enroll_tool'u çalıştırır.
 *
 * NEDEN kayıt burada değil: ses embedding'leri (campplus pencereleri) worker'ın
 * BELLEĞİNDE yaşıyor — bu Node süreci onlara erişemez. Kaydın deterministik denetimi
 * (isim tek kelime mi, örnekler tutarlı mı, ses zaten kayıtlı birine mi ait) ve
 * speakers.db'ye yazma bu yüzden Python tarafında. Sonucu da KULLANICIYA WORKER söyler:
 * modelin "kaydettim" deyip geçmesi bu repo'da ÖLÇÜLMÜŞ bir hata sınıfı, o yüzden son
 * söz koda ait. Model bu tool'un dönüş metnini tekrar etmemeli.
 *
 * Yükleme: worker pi sürecine `-e pi/extensions/speaker-enroll/index.ts` ile.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const ENROLL_NOTE = `
<speaker-enroll>
Tanımadığın biri sesini kaydettirmek isterse (ya da sana "beni kaydet", "sesimi tanı",
"beni tanı" derse) şu sırayı izle: sesini kaydedersen onu tanıyacağını söyle ve izin
iste → SADECE adını TEK KELİMEYLE söylemesini iste (cümle kurmasın; uzun cümlede ses
tanıma ismi bozuyor) → birkaç saniye konuşmasını iste (ne diyeceğini bilmiyorsa yirmiye
kadar saysın) → anladığın ismi GERİ OKU ve onaylat → onayı ALDIKTAN SONRA enroll_speaker
tool'unu çağır. Kaydı tool yapar: onu ÇAĞIRMADAN "kaydettim" DEME. Sonucu tool bildirir,
sen tekrar etme. İstemiyorsa ısrar etme.
</speaker-enroll>`;

export default function speakerEnrollExtension(pi: ExtensionAPI) {
	pi.registerTool({
		name: "enroll_speaker",
		label: "Enroll Speaker",
		description:
			"Save the current speaker's voice under a name so the assistant recognises them " +
			"from now on. You MUST call this tool to enroll anyone — saying 'kaydettim' " +
			"without calling it saves NOTHING. Call it only after the person agreed to be " +
			"enrolled, spoke for a few seconds, and confirmed the name you read back to them. " +
			"Pass `name` as a SINGLE first name, no sentence and no surname. The tool performs " +
			"the actual save and reports the outcome (success or rejection) to the user itself " +
			"— do not repeat or paraphrase its result.",
		promptSnippet:
			"Unknown/new speaker wants to be recognised → run the short enrollment wizard, " +
			"confirm the name, then call enroll_speaker(name). Never claim enrollment without it.",
		parameters: Type.Object({
			name: Type.String({
				description:
					"The person's first name, exactly one word (e.g. 'Havi'). Never a sentence.",
			}),
		}),
		// İmza SDK'nın: (toolCallId, params, signal, onUpdate, ctx) — params İKİNCİ sırada.
		// İlk argümanı params sanmak sessiz arıza: `name` undefined gelir, kayıt patlar.
		// Bkz. websearch/index.ts ve family-memory/index.ts — aynı desen.
		async execute(_id, params: { name: string }) {
			const name = params.name;
			// Sinyal: gerçek kayıt + doğrulama worker'da (bkz. dosya başı).
			return {
				content: [
					{
						type: "text" as const,
						text: `Kayıt isteği alındı (${name}); sonucu worker bildirecek.`,
					},
				],
				details: { signal: "enroll_speaker", name },
			};
		},
	});

	pi.on("before_agent_start", async (event) => {
		return { systemPrompt: event.systemPrompt + ENROLL_NOTE };
	});
}
