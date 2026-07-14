/**
 * `mate.tool` — Candan'ın NE YAPTIĞI (tool çağrısı + sonucu) kanalı.
 *
 * Worker (pi_brain) pi'nin akışındaki `toolCall` / `toolResult` mesajlarını yakalar ve
 * odaya `mate.tool` topic'inde TEXT STREAM olarak (JSON satırı) yayınlar. Web bu olayları
 * transkriptin arasına kronolojik olarak sokar (dashboard'daki oturum dökümü gibi).
 *
 * İsim alanı `mate.*` — docs/MULTI-CLIENT-PLAN.md §6 kararı (candan.* değil).
 *
 * Şema (tek satır JSON, topic başına bir olay):
 *   { "type": "tool_call",   "id": "<toolCallId>", "name": "reminder_add",
 *     "args": { ... }, "ts": 1784064893915 }
 *   { "type": "tool_result", "id": "<toolCallId>", "name": "reminder_add",
 *     "result": "Hatırlatma kuruldu: ... (#12)", "isError": false, "ts": 1784064893953 }
 *
 * `id` = pi'nin toolCall id'si → çağrı ile sonuç eşleşir, tekrar gelen olay elenir.
 * `ts` = epoch MİLİSANİYE (transkript mesajlarının `timestamp`'i ile aynı birim → tek
 * kronolojik sıra kurulabilir).
 */
export const TOOL_EVENTS_TOPIC = 'mate.tool';

export type ToolCallEvent = {
  type: 'tool_call';
  id: string;
  name: string;
  args: unknown;
  ts: number;
};

export type ToolResultEvent = {
  type: 'tool_result';
  id: string;
  name: string;
  result: string;
  isError: boolean;
  ts: number;
};

export type ToolEvent = ToolCallEvent | ToolResultEvent;

/** Dedupe anahtarı: aynı olay iki kez gelirse (yeniden bağlanma) bir kez gösterilir. */
export function toolEventKey(event: ToolEvent): string {
  return `${event.type}:${event.id}`;
}

/** Ham JSON satırını olaya çevir. Bozuk/eksik → null (render akışı bozulmaz). */
export function parseToolEvent(raw: string): ToolEvent | null {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof data !== 'object' || data === null) return null;
  const o = data as Record<string, unknown>;
  const id = typeof o.id === 'string' ? o.id : '';
  const name = typeof o.name === 'string' ? o.name : '';
  const ts = typeof o.ts === 'number' && Number.isFinite(o.ts) ? o.ts : Date.now();
  if (!id || !name) return null;

  if (o.type === 'tool_call') {
    return { type: 'tool_call', id, name, args: o.args ?? {}, ts };
  }
  if (o.type === 'tool_result') {
    return {
      type: 'tool_result',
      id,
      name,
      result: typeof o.result === 'string' ? o.result : '',
      isError: o.isError === true,
      ts,
    };
  }
  return null;
}

/** Tool çağrısının tek satırlık monospace gösterimi: `reminder_add({"in_minutes":2})`. */
export function formatToolCall(event: ToolCallEvent): string {
  let args = '';
  try {
    args = JSON.stringify(event.args ?? {});
  } catch {
    args = '{…}';
  }
  return `${event.name}(${args})`;
}

/**
 * Worker'ın modele enjekte ettiği `(Sistem: ...)` / `(Sistem notu: ...)` önekini kırp.
 *
 * `pi_brain._now_note()` her tura güncel saati, ilk turda da selam direktifini ekler —
 * MODEL için gerekli, KULLANICIYA gösterilmemeli. Parantezler iç içe olabildiği için
 * (ör. "(Sistem notu: Ayhan az önce bağlandı (~22:00, gece); ...)") derinlik sayarak
 * kapanışı buluyoruz; kapanmamış parantezde metne DOKUNMUYORUZ (veri kaybı olmasın).
 */
export function stripSystemPrefix(text: string): string {
  let rest = text;
  for (;;) {
    const trimmed = rest.trimStart();
    if (!trimmed.startsWith('(Sistem')) return trimmed === rest ? rest : trimmed;
    let depth = 0;
    let end = -1;
    for (let i = 0; i < trimmed.length; i++) {
      const ch = trimmed[i];
      if (ch === '(') depth++;
      else if (ch === ')') {
        depth--;
        if (depth === 0) {
          end = i;
          break;
        }
      }
    }
    if (end === -1) return trimmed; // kapanmayan parantez → olduğu gibi bırak
    rest = trimmed.slice(end + 1);
  }
}

/** Tool detaylarını göster/gizle — kalıcı (localStorage). Varsayılan: AÇIK. */
const SHOW_TOOLS_KEY = 'candan.showTools';

export function readShowTools(): boolean {
  if (typeof window === 'undefined') return true;
  try {
    return window.localStorage.getItem(SHOW_TOOLS_KEY) !== 'false';
  } catch {
    return true;
  }
}

export function writeShowTools(value: boolean): void {
  try {
    window.localStorage.setItem(SHOW_TOOLS_KEY, value ? 'true' : 'false');
  } catch {
    // storage kapalı → seçim yalnız bu sekmede yaşar
  }
}
