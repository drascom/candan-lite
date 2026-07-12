'use client';

import { createContext, type ReactNode, useContext, useState } from 'react';
import { ConnectionError, MediaDeviceFailure } from 'livekit-client';

/**
 * Bağlantı kurulurken (token alma / Room.connect / mikrofon açma) oluşan hatanın türü.
 * DebugStatus bu bilgiyi anlaşılır Türkçe metne çevirip gösterir.
 */
export type ConnectErrorKind = 'token' | 'livekit' | 'mic' | 'unknown';

export interface ConnectError {
  kind: ConnectErrorKind;
  /** Kullanıcıya gösterilecek, ikon içeren hazır Türkçe metin. */
  message: string;
}

/**
 * `useSession().start()` içinden fırlayan hatayı sınıflandırır.
 * - Token endpoint'i (ör. /api/token) hata döndürdüyse veya fetch başarısız olduysa → 'token'
 * - LiveKit sunucusuna (websocket) bağlanılamadıysa → 'livekit'
 * - Mikrofon izni reddedildiyse → 'mic'
 * - Diğer her şey → 'unknown' (hatanın mesajı yutulmadan gösterilir)
 */
export function classifyConnectError(err: unknown): ConnectError {
  const micFailure = MediaDeviceFailure.getFailure(err);
  if (micFailure === MediaDeviceFailure.PermissionDenied) {
    return { kind: 'mic', message: '❌ Mikrofon izni verilmedi' };
  }

  if (err instanceof ConnectionError) {
    return { kind: 'livekit', message: '❌ LiveKit sunucusuna bağlanılamadı' };
  }

  const msg = err instanceof Error ? err.message : String(err);

  if (/error generating token from endpoint/i.test(msg)) {
    return { kind: 'token', message: '❌ Token alınamadı (sunucu hatası)' };
  }
  if (err instanceof TypeError && /fetch/i.test(msg)) {
    return { kind: 'token', message: '❌ Token sunucusuna ulaşılamadı (server kapalı olabilir)' };
  }

  return { kind: 'unknown', message: `⚠️ Bağlantı hatası: ${msg || 'bilinmeyen hata'}` };
}

interface ConnectErrorContextValue {
  connectError: ConnectError | null;
  setConnectError: (error: ConnectError | null) => void;
}

const ConnectErrorContext = createContext<ConnectErrorContextValue | null>(null);

export function ConnectErrorProvider({ children }: { children: ReactNode }) {
  const [connectError, setConnectError] = useState<ConnectError | null>(null);
  return (
    <ConnectErrorContext.Provider value={{ connectError, setConnectError }}>
      {children}
    </ConnectErrorContext.Provider>
  );
}

/** Bağlantı hatasını okumak/yazmak için. ConnectErrorProvider içinde kullanılmalı. */
export function useConnectError() {
  const ctx = useContext(ConnectErrorContext);
  if (!ctx) {
    throw new Error('useConnectError must be used within a ConnectErrorProvider');
  }
  return ctx;
}
