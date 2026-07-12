'use client';

import { useEffect, useRef } from 'react';
import { useTheme } from 'next-themes';
import { AnimatePresence, motion } from 'motion/react';
import { useSessionContext } from '@livekit/components-react';
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

export function ViewController({ appConfig }: ViewControllerProps) {
  const { start } = useSessionContext();
  const { resolvedTheme } = useTheme();
  const startedRef = useRef(false);
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
