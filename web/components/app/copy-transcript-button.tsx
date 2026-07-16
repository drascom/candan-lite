'use client';

import { useState } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { type CopyRow, buildTranscriptMarkdown, countRows } from '@/lib/transcript-copy';

/**
 * "Kopyala" — tüm sohbeti (mesajlar + eylemler) markdown olarak panoya kopyalar.
 *
 * Neden transkriptin İÇİNDE değil de propla besleniyor: kopyalanacak veri zaten
 * transkriptte kronolojik olarak birleşiyor (mesaj + `mate.tool` olayları). Onu ikinci
 * kez kurmak yerine hazır satırları alıyoruz — üstelik `useToolEvents` topic başına TEK
 * handler kaydedebildiği için ikinci bir dinleyici açmak da mümkün değil.
 *
 * Eylem kartları, ekrandaki "detay kapalı" seçiminden BAĞIMSIZ olarak kopyaya girer.
 */
export function CopyTranscriptButton({ rows }: { rows: CopyRow[] }) {
  const [busy, setBusy] = useState(false);
  const empty = rows.length === 0;

  const handleClick = async () => {
    if (busy || empty) return;
    setBusy(true);
    try {
      const text = buildTranscriptMarkdown(rows);
      // navigator.clipboard yalnız HTTPS/localhost'ta var → yoksa zarif başarısızlık.
      if (!navigator.clipboard?.writeText) {
        toast.warning('Pano kullanılamıyor', {
          description: 'Tarayıcı panoya yazmaya izin vermiyor (HTTPS ya da localhost gerekir).',
        });
        return;
      }
      await navigator.clipboard.writeText(text);
      const { messages, tools } = countRows(rows);
      toast.success('Sohbet kopyalandı', {
        description: `${messages} mesaj, ${tools} eylem panoda.`,
      });
    } catch (e) {
      console.error('sohbet kopyalanamadı', e);
      toast.warning('Kopyalanamadı', { description: 'Biraz sonra tekrar dene.' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Button
      size="xs"
      variant="ghost"
      className="rounded-full"
      disabled={busy || empty}
      onClick={handleClick}
      title={
        empty
          ? 'Kopyalanacak sohbet yok'
          : 'Tüm sohbeti (eylemler dahil) markdown olarak panoya kopyala'
      }
    >
      Kopyala
    </Button>
  );
}
