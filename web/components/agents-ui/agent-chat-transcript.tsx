'use client';

import { type ComponentProps, useRef } from 'react';
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

/**
 * A chat transcript component that displays a conversation between the user and agent.
 * Shows messages with timestamps and origin indicators, plus a thinking indicator
 * when the agent is processing.
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
  // Uyurken (candan.awake === "false") gelen KULLANICI mesajlarını gizle.
  // Agent mesajları asla gizlenmez. Bir kez gizlenen id kalıcı gizli kalır (interim→final tutarlı).
  const { agentAttributes } = useVoiceAssistant();
  const awake = agentAttributes?.['candan.awake'];
  const hiddenIdsRef = useRef<Set<string>>(new Set());

  const visibleMessages = messages.filter((m) => {
    const isUser = m.from?.isLocal === true;
    if (!isUser) return true; // agent mesajı: her zaman görünür
    if (awake === 'false') hiddenIdsRef.current.add(m.id);
    return !hiddenIdsRef.current.has(m.id);
  });

  return (
    <Conversation className={className} {...props}>
      <ConversationContent>
        {visibleMessages.map((receivedMessage) => {
          const { id, timestamp, from, message } = receivedMessage;
          const locale = navigator?.language ?? 'en-US';
          const messageOrigin = from?.isLocal ? 'user' : 'assistant';
          const time = new Date(timestamp);
          const title = time.toLocaleTimeString(locale, { timeStyle: 'full' });

          return (
            <Message key={id} title={title} from={messageOrigin}>
              <MessageContent>
                <MessageResponse>{message}</MessageResponse>
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
