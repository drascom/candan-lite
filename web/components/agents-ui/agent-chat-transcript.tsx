'use client';

import { type ComponentProps, useEffect, useRef, useState } from 'react';
import { AnimatePresence } from 'motion/react';
import {
  type AgentState,
  type ReceivedMessage,
  useVoiceAssistant,
} from '@livekit/components-react';
import { AgentChatIndicator } from '@/components/agents-ui/agent-chat-indicator';
import {
  Conversation,
  ConversationContent,
  ConversationScrollButton,
} from '@/components/ai-elements/conversation';
import { Message, MessageContent, MessageResponse } from '@/components/ai-elements/message';
import { Button } from '@/components/ui/button';
import { useToolEvents } from '@/hooks/useToolEvents';
import {
  type ToolEvent,
  formatToolCall,
  readShowTools,
  stripSystemPrefix,
  writeShowTools,
} from '@/lib/tool-events';

/**
 * Props for the AgentChatTranscript component.
 */
export interface AgentChatTranscriptProps extends ComponentProps<'div'> {
  /**
   * The current state of the agent. When 'thinking', displays a loading indicator.
   */
  agentState?: AgentState;
  /**
   * Array of messages to display in the transcript.
   * @defaultValue []
   */
  messages?: ReceivedMessage[];
  /**
   * Additional CSS class names to apply to the conversation container.
   */
  className?: string;
}

/** Transkript satırı: ya bir konuşma mesajı ya da bir tool olayı (kronolojik tek akış). */
type Row =
  | { kind: 'message'; id: string; ts: number; message: ReceivedMessage; text: string }
  | { kind: 'tool'; id: string; ts: number; event: ToolEvent };

function timeLabel(ts: number, timeStyle: 'full' | 'medium' = 'medium'): string {
  const locale = typeof navigator !== 'undefined' ? (navigator.language ?? 'en-US') : 'en-US';
  return new Date(ts).toLocaleTimeString(locale, { timeStyle });
}

/**
 * Tool olayı satırı — dashboard'daki (tools/dashboard.py) oturum dökümünün görsel dili:
 * rol etiketi + saat, tool ÇAĞRISI monospace kutuda, tool SONUCU turuncu şeritte.
 */
function ToolRow({ event }: { event: ToolEvent }) {
  const time = timeLabel(event.ts);

  if (event.type === 'tool_call') {
    return (
      <div className="is-assistant flex w-full max-w-[95%] flex-col gap-1">
        <div className="text-muted-foreground text-[11px] tracking-wider uppercase">
          tool çağrısı · {time}
        </div>
        <pre className="border-border bg-muted text-foreground overflow-x-auto rounded-md border px-3 py-2 font-mono text-xs">
          🔧 {formatToolCall(event)}
        </pre>
      </div>
    );
  }

  return (
    <div className="is-assistant flex w-full max-w-[95%] flex-col gap-1 border-l-2 border-amber-500 pl-3">
      <div className="text-muted-foreground text-[11px] tracking-wider uppercase">
        tool sonucu · {event.name} · {time}
      </div>
      <div className={event.isError ? 'text-destructive text-sm' : 'text-foreground text-sm'}>
        {event.result}
      </div>
    </div>
  );
}

/**
 * A chat transcript component that displays a conversation between the user and agent.
 * Shows messages with timestamps and origin indicators, plus a thinking indicator
 * when the agent is processing.
 *
 * Ayrıca Candan'ın NE YAPTIĞINI gösterir: `mate.tool` kanalından gelen tool çağrısı /
 * sonucu olayları mesajların arasına KRONOLOJİK olarak sokulur (dashboard'daki oturum
 * dökümü gibi). Tool detayları varsayılan AÇIK; sağ üstteki düğmeyle kapatılabilir
 * (localStorage'da kalır). Kanal boşsa (worker yayınlamıyorsa) render eskisiyle AYNI.
 *
 * @extends ComponentProps<'div'>
 *
 * @example
 * ```tsx
 * <AgentChatTranscript
 *   agentState={agentState}
 *   messages={chatMessages}
 * />
 * ```
 */
export function AgentChatTranscript({
  agentState,
  messages = [],
  className,
  ...props
}: AgentChatTranscriptProps) {
  // Uyurken (candan.awake === "false") söylenen KULLANICI mesajları gizlenir.
  // Karar MESAJ BAZINDA verilir: her mesaj id'si ilk göründüğü andaki awake durumunu
  // kalıcı olarak "yakalar" (snapshot). Böylece sonradan uykuya geçilse bile, uyanıkken
  // söylenmiş eski mesajlar ekrandan SİLİNMEZ — sadece uyurken söylenenler gizli kalır.
  // Agent mesajları asla gizlenmez.
  const { agentAttributes } = useVoiceAssistant();
  const awake = agentAttributes?.['candan.awake'];
  const awakeSnapshotRef = useRef<Map<string, string | undefined>>(new Map());

  const toolEvents = useToolEvents();
  // Tool detayları görünsün mü (localStorage; varsayılan AÇIK). null = henüz mount olmadı
  // → server/client ilk render'ı ayrışmasın diye düğme o ana kadar çizilmez.
  const [showTools, setShowTools] = useState<boolean | null>(null);
  useEffect(() => {
    setShowTools(readShowTools());
  }, []);

  const visibleMessages = messages.filter((m) => {
    const isUser = m.from?.isLocal === true;
    if (!isUser) return true; // agent mesajı: her zaman görünür
    if (!awakeSnapshotRef.current.has(m.id)) {
      awakeSnapshotRef.current.set(m.id, awake);
    }
    return awakeSnapshotRef.current.get(m.id) !== 'false';
  });

  // Mesajlar + tool olayları → tek kronolojik akış (ikisi de epoch ms damgalı).
  const messageRows: Row[] = visibleMessages
    .map((message) => ({
      kind: 'message' as const,
      id: message.id,
      ts: new Date(message.timestamp).getTime(),
      message,
      // Worker'ın modele enjekte ettiği "(Sistem: şu an ...)" öneki kullanıcıya
      // GÖSTERİLMEZ (bkz. lib/tool-events.ts → stripSystemPrefix).
      text: stripSystemPrefix(message.message),
    }))
    .filter((row) => row.text.length > 0); // sadece sistem notundan ibaret mesaj → gösterme

  const toolRows: Row[] = showTools
    ? toolEvents.map((event) => ({
        kind: 'tool' as const,
        id: `${event.type}:${event.id}`,
        ts: event.ts,
        event,
      }))
    : [];

  const rows: Row[] = [...messageRows, ...toolRows].sort((a, b) => a.ts - b.ts);

  return (
    <Conversation className={className} {...props}>
      {showTools !== null && (
        <Button
          size="xs"
          variant={showTools ? 'secondary' : 'ghost'}
          aria-pressed={showTools}
          onClick={() => {
            const next = !showTools;
            writeShowTools(next);
            setShowTools(next);
          }}
          className="absolute top-2 right-3 z-20 rounded-full font-mono"
          title="Tool çağrılarını ve sonuçlarını göster/gizle"
        >
          🔧 {showTools ? 'detay açık' : 'detay kapalı'}
        </Button>
      )}
      <ConversationContent>
        {rows.map((row) => {
          if (row.kind === 'tool') {
            return <ToolRow key={row.id} event={row.event} />;
          }
          const messageOrigin = row.message.from?.isLocal ? 'user' : 'assistant';
          return (
            <Message key={row.id} title={timeLabel(row.ts, 'full')} from={messageOrigin}>
              <MessageContent>
                <MessageResponse>{row.text}</MessageResponse>
              </MessageContent>
            </Message>
          );
        })}
        <AnimatePresence>
          {agentState === 'thinking' && <AgentChatIndicator size="sm" />}
        </AnimatePresence>
      </ConversationContent>
      <ConversationScrollButton />
    </Conversation>
  );
}
