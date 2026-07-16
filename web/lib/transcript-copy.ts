/**
 * Sohbeti panoya kopyalamak için DÜZ METİN (markdown) döküm üretir.
 *
 * Amaç: kullanıcı sohbeti kopyalayıp bir modele (Claude'a) yapıştırsın ve Candan'ın
 * davranışını —hangi sözden sonra hangi tool'u çağırdığını— gösterebilsin. Bu yüzden
 * çıktı JSON dump DEĞİL, okunabilir markdown; mesajlar ve eylemler `ts`'e göre TEK
 * kronolojik akışta.
 *
 * Not: eylem (tool) satırları, ekrandaki "detay kapalı" seçiminden BAĞIMSIZ olarak
 * kopyaya girer — çağrıyı görmek kopyalamanın asıl sebebi (bkz. agent-chat-transcript).
 */
import type { ToolEvent } from '@/lib/tool-events';

/** Kopyalanacak tek satır: konuşma mesajı ya da tool olayı. */
export type CopyRow =
  | { kind: 'message'; ts: number; isUser: boolean; text: string }
  | { kind: 'tool'; ts: number; event: ToolEvent };

function locale(): string {
  return typeof navigator !== 'undefined' ? (navigator.language ?? 'tr-TR') : 'tr-TR';
}

/** `14:32` — mesaj/eylem başına saat: model gecikmesi de görülebilsin. */
function clock(ts: number): string {
  return new Date(ts).toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' });
}

/** Argümanları kompakt JSON'a çevir; çevrilemezse akışı bozma. */
function formatArgs(args: unknown): string {
  try {
    return JSON.stringify(args ?? {});
  } catch {
    return '{…}';
  }
}

/**
 * Satırları markdown dökümüne çevir. Boş liste → boş string (çağıran "kopyalanacak
 * bir şey yok" der).
 */
export function buildTranscriptMarkdown(rows: CopyRow[]): string {
  if (rows.length === 0) return '';

  const sorted = [...rows].sort((a, b) => a.ts - b.ts);
  const startedAt = new Date(sorted[0].ts).toLocaleString(locale(), {
    dateStyle: 'short',
    timeStyle: 'short',
  });

  const lines: string[] = [`## Candan sohbeti — ${startedAt}`, ''];

  for (const row of sorted) {
    const time = clock(row.ts);
    if (row.kind === 'message') {
      lines.push(`**[${time}] ${row.isUser ? 'Kullanıcı' : 'Candan'}:** ${row.text}`, '');
      continue;
    }
    const event = row.event;
    if (event.type === 'tool_call') {
      lines.push(`🔧 [${time}] **${event.name}** ← ${formatArgs(event.args)}`);
    } else {
      const mark = event.isError ? '❌' : '✅';
      const suffix = event.isError ? ' (HATA)' : '';
      lines.push(`${mark} [${time}] **${event.name}**${suffix} → ${event.result.trim()}`, '');
    }
  }

  return `${lines.join('\n').trimEnd()}\n`;
}

/** Kopyalanan satırların özeti (toast metni için): kaç mesaj, kaç eylem. */
export function countRows(rows: CopyRow[]): { messages: number; tools: number } {
  return {
    messages: rows.filter((r) => r.kind === 'message').length,
    tools: rows.filter((r) => r.kind === 'tool').length,
  };
}
