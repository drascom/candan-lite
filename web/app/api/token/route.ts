import { NextRequest, NextResponse } from 'next/server';
import { AccessToken, type AccessTokenOptions, type VideoGrant } from 'livekit-server-sdk';
import { RoomAgentDispatch, RoomConfiguration } from '@livekit/protocol';
import { AGENT_NAME } from '@/lib/agent-name';

type ConnectionDetails = {
  serverUrl: string;
  roomName: string;
  participantName: string;
  participantToken: string;
};

// NOTE: you are expected to define the following environment variables in `.env.local`:
const API_KEY = process.env.LIVEKIT_API_KEY;
const API_SECRET = process.env.LIVEKIT_API_SECRET;
const LIVEKIT_URL = process.env.MATE_PUBLIC_LIVEKIT_URL ?? process.env.LIVEKIT_URL;
const FALLBACK_ROOM_NAME = process.env.MATE_LIVEKIT_ROOM;
const HERMES_TOKEN_ENDPOINT = process.env.MATE_TOKEN_ENDPOINT;
const HERMES_DISCOVERY_ENDPOINT = process.env.MATE_DISCOVERY_ENDPOINT ?? HERMES_TOKEN_ENDPOINT;
const HERMES_CLIENT_KEY = process.env.MATE_VOICE_CLIENT_KEY;
const DEVICE_COOKIE = 'mate-web-device-id';

// don't cache the results
export const revalidate = 0;

export async function POST(req: NextRequest) {
  try {
    // Parse room config from request body.
    const body = await req.json();
    const roomConfig = withAgentDispatch(
      body?.room_config
        ? RoomConfiguration.fromJson(body.room_config, { ignoreUnknownFields: true })
        : new RoomConfiguration()
    );

    // Generate participant token
    const deviceId = req.cookies.get(DEVICE_COOKIE)?.value ?? crypto.randomUUID();
    const participantName = 'Web';
    const participantIdentity = `web-${deviceId}`;
    let serverUrl: string;
    let roomName: string;
    let participantToken: string;
    if (HERMES_TOKEN_ENDPOINT && HERMES_CLIENT_KEY) {
      const hermes = await fetchHermesToken(participantIdentity);
      serverUrl = hermes.url;
      roomName = hermes.room;
      participantToken = hermes.token;
    } else {
      if (!LIVEKIT_URL || !API_KEY || !API_SECRET) {
        throw new Error(
          'Configure MATE_TOKEN_ENDPOINT + MATE_VOICE_CLIENT_KEY or the LIVEKIT_* variables'
        );
      }
      const discovery = HERMES_DISCOVERY_ENDPOINT ? await fetchHermesDiscovery() : undefined;
      roomName = discovery?.room ?? FALLBACK_ROOM_NAME ?? '';
      serverUrl = discovery?.url ?? LIVEKIT_URL;
      if (!roomName) {
        throw new Error(
          'Configure MATE_DISCOVERY_ENDPOINT or MATE_LIVEKIT_ROOM for direct token minting'
        );
      }
      participantToken = await createParticipantToken(
        { identity: participantIdentity, name: participantName },
        roomName,
        roomConfig
      );
    }

    // Return connection details
    const data: ConnectionDetails = {
      serverUrl,
      roomName,
      participantName,
      participantToken,
    };
    const headers = new Headers({
      'Cache-Control': 'no-store',
    });
    const response = NextResponse.json(data, { headers });
    response.cookies.set(DEVICE_COOKIE, deviceId, {
      httpOnly: true,
      sameSite: 'lax',
      secure: process.env.NODE_ENV === 'production',
      path: '/',
      maxAge: 60 * 60 * 24 * 365,
    });
    return response;
  } catch (error) {
    if (error instanceof Error) {
      console.error(error);
      return new NextResponse(error.message, { status: 500 });
    }
  }
}

/**
 * Explicit agent dispatch (https://docs.livekit.io/agents/worker/agent-dispatch).
 *
 * Otomatik dispatch SADECE oda ilk kez OLUŞTURULURKEN tetiklenir. Oda adımız sabit
 * (MATE_LIVEKIT_ROOM) olduğu için, oda yaşarken worker restart edilince agent odaya
 * bir daha giremiyordu. Çözüm: token'a roomConfig.agents[] koyup agent'ı HER
 * bağlantıda açıkça çağırmak — oda yeni de olsa eski de olsa dispatch olur.
 *
 * Client (livekit-client TokenSource) zaten `room_config` gönderiyor; yine de adı
 * burada, server-side env'den ZORLUYORUZ: sessizce düşerse agent hiç çağrılmaz.
 */
function withAgentDispatch(roomConfig: RoomConfiguration): RoomConfiguration {
  if (!AGENT_NAME) return roomConfig;
  const existing = roomConfig.agents.find((a) => a.agentName === AGENT_NAME);
  if (!existing) {
    roomConfig.agents.push(new RoomAgentDispatch({ agentName: AGENT_NAME }));
  }
  return roomConfig;
}

async function fetchHermesToken(identity: string) {
  const endpoint = new URL('/mate/token', HERMES_TOKEN_ENDPOINT);
  endpoint.searchParams.set('identity', identity);
  const response = await fetch(endpoint, {
    headers: { 'X-Mate-Key': HERMES_CLIENT_KEY! },
    cache: 'no-store',
  });
  if (!response.ok) {
    throw new Error(`Hermes token endpoint returned HTTP ${response.status}`);
  }
  const data = (await response.json()) as { url?: string; room?: string; token?: string };
  if (!data.url || !data.room || !data.token) {
    throw new Error('Hermes token endpoint returned an invalid response');
  }
  return { url: data.url, room: data.room, token: data.token };
}

async function fetchHermesDiscovery() {
  const endpoint = new URL('/mate/health', HERMES_DISCOVERY_ENDPOINT);
  const response = await fetch(endpoint, { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`Hermes discovery endpoint returned HTTP ${response.status}`);
  }
  const data = (await response.json()) as { url?: string; room?: string };
  if (!data.url || !data.room) {
    throw new Error('Hermes discovery endpoint returned an invalid response');
  }
  return { url: data.url, room: data.room };
}

function createParticipantToken(
  userInfo: AccessTokenOptions,
  roomName: string,
  roomConfig: RoomConfiguration | undefined
): Promise<string> {
  const at = new AccessToken(API_KEY, API_SECRET, {
    ...userInfo,
    ttl: '15m',
  });
  const grant: VideoGrant = {
    room: roomName,
    roomJoin: true,
    canPublish: true,
    canPublishData: true,
    canSubscribe: true,
  };
  at.addGrant(grant);

  if (roomConfig) {
    at.roomConfig = roomConfig;
  }

  return at.toJwt();
}
