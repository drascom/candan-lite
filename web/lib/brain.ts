/**
 * Beyin seçimi (hangi modelle konuşuyoruz) — TEK KAYNAK (client + server ortak).
 *
 * Seçim OTURUM BAŞINDA yapılır ve oturum boyunca sabittir:
 *   1. İstemci seçimi `localStorage`'da tutar (varsayılan: yerel).
 *   2. Token isteğine `?brain=<local|remote>` olarak takılır (web/components/app/app.tsx).
 *   3. Token route'u bunu AGENT DISPATCH METADATA'sına gömer (RoomAgentDispatch.metadata /
 *      createDispatch({metadata})) — web/app/api/token/route.ts.
 *   4. Worker `ctx.job.metadata` ile job DOĞARKEN okur → pi alt-süreci doğru modelle başlar.
 *
 * Neden dispatch metadata (participant metadata DEĞİL): job metadata entrypoint'in İLK
 * satırında, `ctx.connect()`'ten bile ÖNCE elde. Participant metadata'sı için agent'ın
 * katılımcının odaya girmesini BEKLEMESİ gerekirdi → pi süreci doğarken seçim henüz
 * gelmemiş olabilirdi (yarış). Dispatch metadata'sında yarış YOK: iş zaten seçimle doğuyor.
 *
 * Geçersiz/eksik değer → route metadata GÖNDERMEZ → worker `worker/.env` içindeki
 * PI_MODEL/PI_THINKING varsayılanına düşer (bugünkü davranış).
 */
export const BRAINS = ['local', 'remote'] as const;

export type Brain = (typeof BRAINS)[number];

/** Varsayılan: yerel beyin (Gemma-4-12B, .25:8082 llama-server). */
export const BRAIN_DEFAULT: Brain = 'local';

/** Kullanıcıya görünen etiketler (seçici düğmeler). */
export const BRAIN_LABELS: Record<Brain, string> = {
  local: 'Yerel (Gemma)',
  remote: 'Uzak (GPT)',
};

const STORAGE_KEY = 'candan.brain';

export function isBrain(value: unknown): value is Brain {
  return typeof value === 'string' && (BRAINS as readonly string[]).includes(value);
}

/** localStorage'daki seçim; yok/bozuk/SSR → varsayılan (yerel). */
export function readBrain(): Brain {
  if (typeof window === 'undefined') return BRAIN_DEFAULT;
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return isBrain(stored) ? stored : BRAIN_DEFAULT;
  } catch {
    return BRAIN_DEFAULT; // private mode / storage kapalı → varsayılan
  }
}

/** Seçimi kalıcılaştır (sonraki oturumda tekrar sorulmasın). */
export function writeBrain(brain: Brain): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, brain);
  } catch {
    // storage yoksa sessizce geç — seçim yine bu sekmede geçerli olur
  }
}
