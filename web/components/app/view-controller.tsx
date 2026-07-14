'use client';

import { useEffect, useRef } from 'react';
import { useTheme } from 'next-themes';
import { AnimatePresence, motion } from 'motion/react';
import { useAgent, useSessionContext } from '@livekit/components-react';
import type { AppConfig } from '@/app-config';
import { AgentSessionView_01 } from '@/components/agents-ui/blocks/agent-session-view-01';
import { classifyConnectError, useConnectError } from '@/hooks/useConnectError';

const MotionSessionView = motion.create(AgentSessionView_01);

const VIEW_MOTION_PROPS = {
  variants: {
    visible: {
      opacity: 1,
    },
    hidden: {
      opacity: 0,
    },
  },
  initial: 'hidden',
  animate: 'visible',
  exit: 'hidden',
  transition: {
    duration: 0.5,
    ease: 'linear',
  },
};

interface ViewControllerProps {
  appConfig: AppConfig;
}

/**
 * Agent düştükten sonra yeniden bağlanmadan önce beklenen süre.
 *
 * NEDEN GEREKLİ: worker restart edilince agent odadan çıkar; livekit `ParticipantDisconnected`
 * → agent.state = 'failed' ("Agent left the room unexpectedly.") → useAgentErrors `end()`
 * çağırır ve oturum KAPANIR. Aşağıdaki auto-connect mount'ta BİR KEZ çalıştığı için (startedRef)
 * bir daha `start()` YOKTU → yeni token İSTENMEZ → token route'u hiç çalışmaz → agent odaya
 * bir daha DİSPATCH EDİLMEZ. Kullanıcının tek çıkışı sekmeyi kapatıp açmaktı (yeni mount).
 * Şimdi: agent düşünce oturumu yeniden başlatıyoruz → yeni token POST'u → route var olan oda
 * için agent'ı yeniden dispatch eder (app/api/token/route.ts: ensureAgentDispatch).
 *
 * Süre neden 3 sn: end() akışının bitmesini bekler; worker restart'ı zaten saniyeler sürer.
 * Deneme başarısız olursa (worker hâlâ ayakta değil) agent yine 'failed' olur ve bu döngü
 * kendini tekrarlar — sürekli-açık cihazda istenen davranış budur.
 */
const RECONNECT_DELAY_MS = 3000;

export function ViewController({ appConfig }: ViewControllerProps) {
  const { start } = useSessionContext();
  const agent = useAgent();
  const { resolvedTheme } = useTheme();
  const startedRef = useRef(false);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const { setConnectError } = useConnectError();

  // Auto-connect: WelcomeView'i atla, mount'ta doğrudan bağlan (sürekli-açık).
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    setConnectError(null);
    start()
      .then(() => setConnectError(null))
      .catch((err) => {
        console.error('auto-connect failed', err);
        setConnectError(classifyConnectError(err));
      });
  }, [start, setConnectError]);

  // Yeniden bağlanma: agent düştüğünde/hiç gelmediğinde oturumu yeniden başlat.
  //
  // Zamanlayıcı ref'te TUTULUYOR ve efekt temizliğinde İPTAL EDİLMİYOR (yalnız unmount'ta):
  // useAgentErrors `end()` çağırınca oda bağlantısı kopar, livekit "agent left" gerekçesini
  // temizler ve agent.state 'failed' → 'disconnected'a döner. Efekt temizliğinde iptal etseydik
  // bu geçiş zamanlayıcıyı öldürür, yeniden bağlanma HİÇ olmazdı.
  useEffect(() => {
    if (agent.state !== 'failed' || reconnectTimerRef.current) return;
    reconnectTimerRef.current = setTimeout(() => {
      reconnectTimerRef.current = null;
      setConnectError(null);
      start()
        .then(() => setConnectError(null))
        .catch((err) => {
          console.error('reconnect failed', err);
          setConnectError(classifyConnectError(err));
        });
    }, RECONNECT_DELAY_MS);
  }, [agent.state, start, setConnectError]);

  useEffect(
    () => () => {
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
    },
    []
  );

  return (
    <AnimatePresence mode="wait">
      {/* Session view (auto-connect: doğrudan gösterilir) */}
      {
        <MotionSessionView
          key="session-view"
          {...VIEW_MOTION_PROPS}
          supportsChatInput={appConfig.supportsChatInput}
          supportsVideoInput={appConfig.supportsVideoInput}
          supportsScreenShare={appConfig.supportsScreenShare}
          isPreConnectBufferEnabled={appConfig.isPreConnectBufferEnabled}
          audioVisualizerType={appConfig.audioVisualizerType}
          audioVisualizerColor={
            resolvedTheme === 'dark'
              ? appConfig.audioVisualizerColorDark
              : appConfig.audioVisualizerColor
          }
          audioVisualizerColorShift={appConfig.audioVisualizerColorShift}
          audioVisualizerBarCount={appConfig.audioVisualizerBarCount}
          audioVisualizerGridRowCount={appConfig.audioVisualizerGridRowCount}
          audioVisualizerGridColumnCount={appConfig.audioVisualizerGridColumnCount}
          audioVisualizerRadialBarCount={appConfig.audioVisualizerRadialBarCount}
          audioVisualizerRadialRadius={appConfig.audioVisualizerRadialRadius}
          audioVisualizerWaveLineWidth={appConfig.audioVisualizerWaveLineWidth}
          className="fixed inset-0"
        />
      }
    </AnimatePresence>
  );
}
