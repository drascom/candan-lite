'use client';

import { useMemo } from 'react';
import { Room, TokenSource } from 'livekit-client';
import { useSession } from '@livekit/components-react';
import { WarningIcon } from '@phosphor-icons/react/dist/ssr';
import type { AppConfig } from '@/app-config';
import { AgentSessionProvider } from '@/components/agents-ui/agent-session-provider';
import { StartAudioButton } from '@/components/agents-ui/start-audio-button';
import { BrainSelector } from '@/components/app/brain-selector';
import { NewSessionButton } from '@/components/app/new-session-button';
import { ViewController } from '@/components/app/view-controller';
import { Toaster } from '@/components/ui/sonner';
import { useAgentErrors } from '@/hooks/useAgentErrors';
import { ConnectErrorProvider } from '@/hooks/useConnectError';
import { useDebugMode } from '@/hooks/useDebug';
import { readBrain } from '@/lib/brain';
import { getSandboxTokenSource } from '@/lib/utils';

const IN_DEVELOPMENT = process.env.NODE_ENV !== 'production';

function AppSetup() {
  useDebugMode({ enabled: IN_DEVELOPMENT });
  useAgentErrors();

  return null;
}

interface AppProps {
  appConfig: AppConfig;
}

export function App({ appConfig }: AppProps) {
  // Beyin seçimi OTURUM BAŞINDA sabitlenir (bkz. lib/brain.ts): mount'ta localStorage'dan
  // okunur, token isteğine `?brain=...` olarak takılır; token route'u bunu agent dispatch
  // metadata'sına gömer → worker pi sürecini doğru modelle DOĞURUR (yarış yok).
  // Sonradan seçim değiştirilirse bu oturum etkilenmez (selector bunu kullanıcıya söyler).
  const sessionBrain = useMemo(() => readBrain(), []);

  const tokenSource = useMemo(() => {
    return typeof process.env.NEXT_PUBLIC_CONN_DETAILS_ENDPOINT === 'string'
      ? getSandboxTokenSource(appConfig)
      : TokenSource.endpoint(`/api/token?brain=${encodeURIComponent(sessionBrain)}`);
  }, [appConfig, sessionBrain]);

  const room = useMemo(
    () =>
      new Room({
        adaptiveStream: true,
        dynacast: true,
        audioCaptureDefaults: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      }),
    []
  );

  const session = useSession(
    tokenSource,
    appConfig.agentName ? { agentName: appConfig.agentName, room } : { room }
  );

  return (
    <ConnectErrorProvider>
      <AgentSessionProvider session={session}>
        <AppSetup />
        <main className="grid h-svh grid-cols-1 place-content-center">
          <ViewController appConfig={appConfig} />
        </main>
        <BrainSelector sessionBrain={sessionBrain} />
        <NewSessionButton />
        <StartAudioButton label="Start Audio" />
        <Toaster
          icons={{
            warning: <WarningIcon weight="bold" />,
          }}
          position="top-center"
          className="toaster group"
          style={
            {
              '--normal-bg': 'var(--popover)',
              '--normal-text': 'var(--popover-foreground)',
              '--normal-border': 'var(--border)',
            } as React.CSSProperties
          }
        />
      </AgentSessionProvider>
    </ConnectErrorProvider>
  );
}
