'use client';

import { useEffect, useState } from 'react';
import type { TextStreamReader } from 'livekit-client';
import { useSessionContext } from '@livekit/components-react';
import { TOOL_EVENTS_TOPIC, type ToolEvent, parseToolEvent, toolEventKey } from '@/lib/tool-events';

/**
 * Odadaki `mate.tool` text-stream'ini dinle → tool çağrısı/sonucu olayları.
 *
 * Worker yayınlamıyorsa (eski worker / yayın hatası) liste boş kalır ve transkript
 * BUGÜNKÜ gibi çalışır — bu kanal tamamen additive.
 */
export function useToolEvents(): ToolEvent[] {
  const session = useSessionContext();
  const room = session.room;
  const [events, setEvents] = useState<ToolEvent[]>([]);

  useEffect(() => {
    if (!room) return;
    let cancelled = false;

    const handler = (reader: TextStreamReader) => {
      reader
        .readAll()
        .then((raw) => {
          if (cancelled) return;
          const event = parseToolEvent(raw);
          if (!event) return;
          setEvents((prev) => {
            // Aynı olay iki kez gelebilir (yeniden bağlanma) → id ile ele.
            const key = toolEventKey(event);
            if (prev.some((e) => toolEventKey(e) === key)) return prev;
            return [...prev, event];
          });
        })
        .catch((error) => {
          // Okuma hatası konuşmayı BOZMAZ; olay düşer.
          console.warn(`[${TOOL_EVENTS_TOPIC}] stream okunamadı`, error);
        });
    };

    try {
      room.registerTextStreamHandler(TOOL_EVENTS_TOPIC, handler);
    } catch (error) {
      // Aynı topic için handler zaten kayıtlıysa (hot-reload) → sessizce geç.
      console.warn(`[${TOOL_EVENTS_TOPIC}] handler kaydedilemedi`, error);
      return;
    }

    return () => {
      cancelled = true;
      try {
        room.unregisterTextStreamHandler(TOOL_EVENTS_TOPIC);
      } catch {
        // zaten kaldırılmış → önemsiz
      }
    };
  }, [room]);

  return events;
}
