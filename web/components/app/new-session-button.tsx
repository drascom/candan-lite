'use client';

import { useState } from 'react';
import { toast } from 'sonner';
import { useRoomContext, useVoiceAssistant } from '@livekit/components-react';
import { Button } from '@/components/ui/button';

/** Worker'ın kaydettiği RPC metodu (worker/agent.py). İki taraf da bu adı kullanır. */
const NEW_SESSION_RPC = 'candan.new_session';

/**
 * "Yeni sohbet" butonu — sohbet geçmişini sıfırlar, YENİ oturum başlatır.
 *
 * Sesli komutla ("Candan, yeni sohbet başlat") AYNI yola iner: RPC → worker
 * `brain.new_session()` → eski jsonl'in header id'si döndürülür (dosya SİLİNMEZ,
 * diskte ve panoda kalır) + aynı persona/session-id ile taze pi süreci doğar.
 *
 * SIFIRLANAN yalnız SOHBET geçmişidir. Hafıza (memory/ — memory_add/soul_add ile
 * kaydedilenler) KORUNUR; Candan seni ve kalıcı notlarını unutmaz.
 *
 * Neden RPC (data message DEĞİL): RPC'nin dönüş değeri var → butonun başarıyı mı
 * hatayı mı gösterdiğini BİLİYORUZ (data message ateşle-unut olurdu, kullanıcı
 * sıfırlanmadığını fark etmezdi).
 */
export function NewSessionButton() {
  const room = useRoomContext();
  const { agent } = useVoiceAssistant();
  const [busy, setBusy] = useState(false);

  // Agent odada değilken (bağlanıyor / kopuk) buton anlamsız → gizle.
  if (!agent?.identity) return null;

  const handleClick = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const res = await room.localParticipant.performRpc({
        destinationIdentity: agent.identity,
        method: NEW_SESSION_RPC,
        payload: '',
        // pi sürecinin durup yeniden doğması birkaç saniye sürebilir → cömert timeout.
        responseTimeout: 15_000,
      });
      if (res === 'ok') {
        toast.success('Yeni sohbet başladı', {
          description: 'Geçmiş sohbet silinmedi, arşivlendi. Hafıza korundu.',
        });
      } else {
        toast.warning('Sohbet sıfırlanamadı', { description: 'Biraz sonra tekrar dene.' });
      }
    } catch (e) {
      // RPC hiç ulaşmadı / zaman aşımı → kullanıcı sessizce yanılmasın.
      console.error('yeni sohbet RPC başarısız', e);
      toast.warning('Sohbet sıfırlanamadı', { description: 'Biraz sonra tekrar dene.' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="border-border bg-background/80 fixed top-3 right-3 z-50 flex items-center rounded-full border p-1 backdrop-blur">
      <Button
        size="xs"
        variant="ghost"
        className="rounded-full"
        disabled={busy}
        onClick={handleClick}
        title="Sohbet geçmişini sıfırla (hafıza korunur)"
      >
        {busy ? 'Başlatılıyor…' : 'Yeni sohbet'}
      </Button>
    </div>
  );
}
