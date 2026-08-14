/**
 * dev-model-switch — geliştirme modunda sesle model ve düşünme seviyesi seçimi.
 *
 * Tool yalnız dev Pi sürecine yüklenir. Kendisi model değiştirmez; tamamlanmış
 * toolCall mesajını worker yakalar, seçimi kalıcılaştırır ve tur bittikten sonra
 * aynı dev session'ını seçilen modelle yeniden bağlar.
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { getSupportedThinkingLevels } from "@earendil-works/pi-ai";
import { Type } from "typebox";

const MODEL_NOTE = `
<dev-model-switch>
Yalnız GELİŞTİRME modunda kullanıcı "Sol'a geç", "Sol modelini kullan",
"Terra'ya dön" veya benzeri açık bir model değişikliği isterse set_dev_model
aracını çağır. model alanı yalnız "terra" veya "sol" olabilir. Araçtan sonra tek
kısa cümleyle onayla; uzun açıklama veya özellik listesi verme. Kullanıcı model
değiştirmeyi istemediyse bu aracı çağırma.

Kullanıcı düşünme seviyesini değiştirmeyi isterse set_dev_reasoning aracını çağır.
Bir seviye söylemediyse level alanını boş bırak: araç aktif modelden desteklenen
seviyeleri okuyup kullanıcıya seçenekleri verecek. Türkçe bir seviye söylediyse
level alanına duyduğun değeri yaz; araç bunu doğrulayacak. Mevcut seviyeyi sorarsa
sistem istemindeki aktif seviyeyi söyle; ortam değişkenlerini araştırma.
</dev-model-switch>`;

export default function devModelSwitchExtension(pi: ExtensionAPI) {
	pi.registerTool({
		name: "set_dev_model",
		label: "Set Dev Model",
		description:
			"Switch the active self-development model after this turn. Call only when the user " +
			"explicitly asks to use Terra or Sol while in development mode.",
		promptSnippet:
			"Explicit dev model request: call set_dev_model with model='terra' or model='sol'.",
		parameters: Type.Object({
			model: Type.Union([Type.Literal("terra"), Type.Literal("sol")]),
		}),
		async execute(_toolCallId, params) {
			const label = params.model === "sol" ? "Sol" : "Terra";
			return {
				content: [
					{
						type: "text" as const,
						text: `${label} modeline geçiliyor.`,
					},
				],
				details: { signal: "set_dev_model", model: params.model },
			};
		},
	});

	pi.registerTool({
		name: "set_dev_reasoning",
		label: "Set Dev Reasoning",
		description:
			"Switch the active self-development reasoning level after this turn. Call only " +
			"when the user explicitly requests a different thinking/reasoning level.",
		promptSnippet:
			"Reasoning-level request: call set_dev_reasoning; omit level to list levels supported by the active model.",
		parameters: Type.Object({
			level: Type.Optional(
				Type.String({
					description: "Requested level; omit to list levels supported by the active model.",
				}),
			),
		}),
		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			const levels = ctx.model ? getSupportedThinkingLevels(ctx.model) : [];
			const aliases: Record<string, string> = {
				kapalı: "off",
				kapali: "off",
				minimum: "minimal",
				düşük: "low",
				dusuk: "low",
				medyum: "medium",
				orta: "medium",
				yüksek: "high",
				yuksek: "high",
				"çok yüksek": "xhigh",
				"cok yuksek": "xhigh",
				maksimum: "max",
			};
			const raw = (params.level ?? "").trim().toLocaleLowerCase("tr");
			const requested = aliases[raw] ?? raw.replaceAll("_", "-");
			const availableText = levels.length > 0 ? levels.join(", ") : "bulunamadı";

			if (!requested || !levels.includes(requested as (typeof levels)[number])) {
				const prefix = requested
					? `“${params.level}” bu modelde kullanılamıyor. `
					: "";
				return {
					content: [
						{
							type: "text" as const,
							text: `${prefix}Bu modelde kullanılabilen düşünme seviyeleri: ${availableText}. Hangisini istersin?`,
						},
					],
					details: {
						signal: "list_dev_reasoning",
						levels,
						current: ctx.thinkingLevel,
						requested: params.level,
					},
				};
			}

			return {
				content: [
					{
						type: "text" as const,
						text: `Düşünme seviyesi ${requested} olarak ayarlanıyor.`,
					},
				],
				details: { signal: "set_dev_reasoning", level: requested, levels },
			};
		},
	});

	pi.on("before_agent_start", async (event, ctx) => {
		const levels = ctx.model ? getSupportedThinkingLevels(ctx.model) : [];
		const runtimeState = `
<dev-reasoning-runtime>
Aktif model: ${ctx.model?.provider ?? "bilinmiyor"}/${ctx.model?.id ?? "bilinmiyor"}.
Aktif düşünme seviyesi: ${ctx.thinkingLevel ?? "bilinmiyor"}.
Bu modelin şu anda desteklediği düşünme seviyeleri: ${levels.join(", ") || "bulunamadı"}.
Bu değerler aktif model metadata'sından çalışma anında okunmuştur; ortam değişkeni
veya ezberlenmiş sabit liste kullanma.
</dev-reasoning-runtime>`;
		return { systemPrompt: event.systemPrompt + MODEL_NOTE + runtimeState };
	});
}
