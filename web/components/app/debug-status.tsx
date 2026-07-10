'use client';

import { useVoiceAssistant } from '@livekit/components-react';
import { cn } from '@/lib/shadcn/utils';

/**
 * Ekranın en altında sabit, tek satırlık Türkçe durum göstergesi.
 * Kaynaklar:
 *  - Agent state: useVoiceAssistant().state
 *  - Wake durumu: agent participant attribute `candan.awake` ("true"/"false"), worker yayınlar.
 */
export function DebugStatus({ className }: { className?: string }) {
  const { state, agent, agentAttributes } = useVoiceAssistant();
  const awake = agentAttributes?.['candan.awake'];

  let text = 'Bağlanıyor…';
  if (!agent || state === 'disconnected' || state === 'connecting') {
    text = 'Bağlanıyor…';
  } else if (awake === 'false') {
    text = "😴 Uykuda — 'candan' de";
  } else if (state === 'listening') {
    text = '👂 Dinliyorum';
  } else if (state === 'thinking') {
    text = '🧠 Düşünüyorum';
  } else if (state === 'speaking') {
    text = '🗣️ Konuşuyorum';
  } else {
    text = 'Bağlanıyor…';
  }

  return (
    <div
      className={cn(
        'pointer-events-none fixed inset-x-0 bottom-0 z-[60] flex justify-center pb-1',
        className
      )}
    >
      <span className="rounded-full bg-black/60 px-3 py-1 text-xs font-medium text-white/90 backdrop-blur-sm dark:bg-white/15">
        {text}
      </span>
    </div>
  );
}
