'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { BRAINS, BRAIN_LABELS, type Brain, readBrain, writeBrain } from '@/lib/brain';

interface BrainSelectorProps {
  /** Bu oturum HANGİ beyinle başladı (token isteğinde gönderilen değer). */
  sessionBrain: Brain;
}

/**
 * Beyin seçici — oturum BAŞINDA hangi modelle konuşulacağını seçer.
 *
 * Seçim localStorage'a yazılır ve bir SONRAKİ oturum başlangıcında (sayfa yeniden
 * yüklenip agent'a yeni bir iş dispatch edildiğinde) geçerli olur. Şu anki oturumun
 * beyni (pi alt-süreci) DEĞİŞMEZ — konuşma ortasında devir henüz YOK; seçim
 * `sessionBrain`'den farklıysa bunu açıkça yazıyoruz ki kullanıcı yanılmasın.
 */
export function BrainSelector({ sessionBrain }: BrainSelectorProps) {
  // localStorage yalnız client'ta okunabilir → mount'a kadar render etme
  // (server/client ilk render'ı ayrışmasın).
  const [brain, setBrain] = useState<Brain | null>(null);

  useEffect(() => {
    setBrain(readBrain());
  }, []);

  if (brain === null) return null;

  return (
    <div className="border-border bg-background/80 fixed top-3 left-3 z-50 flex items-center gap-1 rounded-full border p-1 backdrop-blur">
      {BRAINS.map((option) => (
        <Button
          key={option}
          size="xs"
          variant={option === brain ? 'default' : 'ghost'}
          className="rounded-full"
          aria-pressed={option === brain}
          onClick={() => {
            writeBrain(option);
            setBrain(option);
          }}
        >
          {BRAIN_LABELS[option]}
        </Button>
      ))}
      {brain !== sessionBrain && (
        <span className="text-muted-foreground px-2 text-[10px] leading-tight">
          sonraki oturumda geçerli
        </span>
      )}
    </div>
  );
}
