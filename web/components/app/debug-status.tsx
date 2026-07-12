'use client';

import { useEffect, useRef } from 'react';
import { useVoiceAssistant } from '@livekit/components-react';
import { useConnectError } from '@/hooks/useConnectError';
import { cn } from '@/lib/shadcn/utils';

/**
 * candan.awake geçişinde kısa bir çan sesi çalar (Web Audio, harici dosya yok).
 * uyan (false→true): 660→990Hz yükselen; uyu (true→false): 660→440Hz alçalan.
 */
function playChime(kind: 'wake' | 'sleep') {
  try {
    const Ctx =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const now = ctx.currentTime;
    const [f0, f1] = kind === 'wake' ? [660, 990] : [660, 440];
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(f0, now);
    osc.frequency.exponentialRampToValueAtTime(f1, now + 0.18);
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(1.0, now + 0.02); // %100 ses
    gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.35);
    osc.connect(gain).connect(ctx.destination);
    osc.start(now);
    osc.stop(now + 0.37);
    osc.onended = () => ctx.close();
  } catch {
    // sessizce yut (autoplay engeli vb.)
  }
}

/**
 * Ekranın en altında sabit, tek satırlık Türkçe durum göstergesi.
 * Kaynaklar:
 *  - Agent state: useVoiceAssistant().state
 *  - Wake durumu: agent participant attribute `candan.awake` ("true"/"false"), worker yayınlar.
 */
export function DebugStatus({ className }: { className?: string }) {
  const { state, agent, agentAttributes } = useVoiceAssistant();
  const { connectError } = useConnectError();
  const awake = agentAttributes?.['candan.awake'];

  // Wake geçişinde çan sesi. İlk değer gelişinde çalma; sadece gerçek geçişte.
  const prevAwakeRef = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (awake !== 'true' && awake !== 'false') return;
    const prev = prevAwakeRef.current;
    prevAwakeRef.current = awake;
    if (prev === undefined || prev === awake) return;
    playChime(awake === 'true' ? 'wake' : 'sleep');
  }, [awake]);

  let text = 'Bağlanıyor…';
  if (connectError) {
    // Bağlantı hiç kurulamadı: token/LiveKit/mikrofon hatası — sebebini göster.
    text = connectError.message;
  } else if (state === 'failed') {
    // Odaya bağlandık ama asistan (worker) katılmadı — en sık sebep: worker kapalı/restart sonrası dispatch yok.
    text = '❌ Asistan odaya katılmadı — worker çalışıyor mu?';
  } else if (!agent || state === 'disconnected' || state === 'connecting') {
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
