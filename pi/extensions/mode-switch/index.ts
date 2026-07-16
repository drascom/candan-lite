/**
 * mode-switch — pi extension: SESLE geliştirme moduna geçiş sinyali.
 *
 * Tools:
 *  - enter_dev_mode()  : kullanıcı "geliştirme moduna geç" dediğinde çağrılır.
 *  - exit_dev_mode()   : kullanıcı "normal moda dön" dediğinde çağrılır.
 *
 * Bu tool'lar KENDİLERİ hiçbir şey yapmaz — sadece bir SİNYALdir. Sesli worker (Python,
 * worker/pi_brain.py) pi'nin stdout event akışındaki toolCall'ı görür (_detect_mode_signal)
 * ve warm pi alt-sürecini SWAP eder: normal beyin (Gemma, kod tool'ları kapalı) ↔ dev beyin
 * (GPT-5.6, kod tool'ları açık, izole git worktree). Swap, tool'u çağıran turun cevabı temiz
 * verildikten SONRA, bir sonraki tur başında olur. Sözleşme = toolCall adı; worker'a başka
 * bağımlılık yok.
 *
 * Yükleme: worker pi sürecine `-e pi/extensions/mode-switch/index.ts` ile (normal + dev
 * modunda). Normal modda enter_dev_mode, dev modunda exit_dev_mode allowlist'e girer.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const SWITCH_NOTE = `
<mode-switch>
Kullanıcı SENDEN geliştirme/kodlama moduna geçmeni isterse ("geliştirme moduna geç",
"kendini geliştirme moduna gir", "kod moduna geç" gibi) enter_dev_mode tool'unu çağır,
sonra tek kısa cümleyle geçtiğini söyle. "Normal moda dön" / "geliştirme modundan çık"
derse exit_dev_mode'u çağır ve kısaca onayla. Bu tool'lar arka planda beyni değiştirir;
başka açıklama yapma, teklif sıralama.
</mode-switch>`;

export default function modeSwitchExtension(pi: ExtensionAPI) {
	pi.registerTool({
		name: "enter_dev_mode",
		label: "Enter Dev Mode",
		description:
			"Switch the assistant into self-development (coding) mode. Call this ONLY when the " +
			"user explicitly asks to enter development/coding mode ('geliştirme moduna geç', " +
			"'kod moduna geç'). Confirm briefly out loud afterwards; the actual brain swap happens " +
			"in the background right after this turn.",
		promptSnippet:
			"User asks to enter development/coding mode → call enter_dev_mode, then confirm briefly.",
		parameters: Type.Object({}),
		async execute() {
			return {
				content: [
					{
						type: "text" as const,
						text: "Geliştirme moduna geçiliyor.",
					},
				],
				details: { signal: "enter_dev_mode" },
			};
		},
	});

	pi.registerTool({
		name: "exit_dev_mode",
		label: "Exit Dev Mode",
		description:
			"Leave self-development mode and return to normal assistant mode. Call this ONLY when " +
			"the user explicitly asks to return to normal ('normal moda dön', 'geliştirme " +
			"modundan çık'). Confirm briefly out loud; the brain swap happens in the background " +
			"right after this turn.",
		promptSnippet:
			"User asks to return to normal mode → call exit_dev_mode, then confirm briefly.",
		parameters: Type.Object({}),
		async execute() {
			return {
				content: [
					{
						type: "text" as const,
						text: "Normal moda dönülüyor.",
					},
				],
				details: { signal: "exit_dev_mode" },
			};
		},
	});

	pi.on("before_agent_start", async (event) => {
		return { systemPrompt: event.systemPrompt + SWITCH_NOTE };
	});
}
