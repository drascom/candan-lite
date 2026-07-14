import { NextRequest, NextResponse } from 'next/server';
import {
  AccessToken,
  type AccessTokenOptions,
  AgentDispatchClient,
  RoomServiceClient,
  type VideoGrant,
} from 'livekit-server-sdk';
import { ParticipantInfo_Kind, RoomAgentDispatch, RoomConfiguration } from '@livekit/protocol';
import { AGENT_NAME } from '@/lib/agent-name';
import { isBrain } from '@/lib/brain';

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
    // Beyin seçimi (oturum başı): istemci `?brain=local|remote` gönderir. Geçersiz/eksik →
    // '' → dispatch metadata'sı YOK → worker `worker/.env` PI_MODEL varsayılanına düşer.
    const dispatchMetadata = brainMetadata(req);

    // Parse room config from request body.
    const body = await req.json();
    const roomConfig = withAgentDispatch(
      body?.room_config
        ? RoomConfiguration.fromJson(body.room_config, { ignoreUnknownFields: true })
        : new RoomConfiguration(),
      dispatchMetadata
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
      // Token'a gömülü dispatch YALNIZCA oda ilk yaratılırken işlenir; oda zaten
      // varsa yok sayılır. Worker restart edilince agent düşer ama oda YAŞAMAYA DEVAM eder
      // (sekme açıksa web katılımcısı odayı tutar; herkes çıksa bile departureTimeout=20 sn
      // boyunca durur) → yeni token'ın gömülü daveti işlenmez. Var olan oda için agent'ı
      // server tarafında açıkça çağırıyoruz.
      await ensureAgentDispatch(serverUrl, roomName, dispatchMetadata);
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
 * Beyin seçimini (oturum başı) dispatch metadata'sına çevir.
 *
 * `?brain=local|remote` → `{"brain":"local"}` (JSON string). Geçersiz/eksik → '' →
 * metadata GÖNDERİLMEZ → worker `worker/.env` içindeki PI_MODEL/PI_THINKING'e düşer
 * (bugünkü davranış; hiçbir koşulda oturum çökmez). Bkz. web/lib/brain.ts.
 *
 * Neden JOB (dispatch) metadata'sı, participant metadata'sı DEĞİL: worker bunu
 * `ctx.job.metadata` ile entrypoint'in İLK satırında, `ctx.connect()`'ten bile ÖNCE
 * görür — pi alt-süreci doğarken seçim ELDEDİR. Participant metadata'sı için agent
 * katılımcının odaya girmesini beklemek zorunda kalırdı → yarış.
 */
function brainMetadata(req: NextRequest): string {
  const brain = req.nextUrl.searchParams.get('brain');
  return isBrain(brain) ? JSON.stringify({ brain }) : '';
}

/**
 * Explicit agent dispatch (https://docs.livekit.io/agents/worker/agent-dispatch).
 *
 * Token'a gömülü `roomConfig.agents[]` daveti LiveKit'te YALNIZCA oda İLK KEZ
 * OLUŞTURULURKEN işlenir; oda zaten varsa yok sayılır. Bu yüzden gömülü davet tek
 * başına yetmez — YENİ oda yolunu (ilk bağlantı) o kaplar; VAR OLAN oda için
 * agent'ı `ensureAgentDispatch` ile server tarafında açıkça çağırıyoruz.
 *
 * Client (livekit-client TokenSource) zaten `room_config` gönderiyor; yine de adı
 * burada, server-side env'den ZORLUYORUZ: sessizce düşerse agent hiç çağrılmaz.
 *
 * `metadata` (beyin seçimi) HER İKİ yolda da aynı: gömülü davete de, açık dispatch'e
 * de basılır — yoksa yeni-oda yolunda seçim kaybolurdu. İstemci kendi `room_config`'inde
 * agent'ı zaten göndermiş olabilir (appConfig.agentName) → var olan kaydın metadata'sını
 * da BİZ yazıyoruz (seçim sessizce düşmesin).
 */
function withAgentDispatch(roomConfig: RoomConfiguration, metadata: string): RoomConfiguration {
  if (!AGENT_NAME) return roomConfig;
  const existing = roomConfig.agents.find((a) => a.agentName === AGENT_NAME);
  if (existing) {
    if (metadata) existing.metadata = metadata;
  } else {
    roomConfig.agents.push(new RoomAgentDispatch({ agentName: AGENT_NAME, metadata }));
  }
  return roomConfig;
}

/**
 * Aynı Next.js süreci içinde oda-başına kısa süreli in-flight kilidi (2. savunma hattı).
 *
 * YARIŞ: iki token POST'u AYNI ANDA gelirse (StrictMode çift-mount, iki sekme, retry),
 * ikisi de aşağıdaki kontrolleri dispatch kaydı DAHA GÖRÜNMEDEN geçer ve İKİSİ de
 * `createDispatch` atar → odaya iki agent → her cevap çift ses (canlıda 23:42:11).
 * Sunucudan okunan durum yalnız SIRALI (kayıt yayılmışsa) istekleri kapatır; gerçek
 * eşzamanlı istek için senkron kilit şart. Map get/set await'siz olduğundan JS
 * tek-thread'inde ilk çağrı damgayı basıp yield eder, sonrakiler damgayı görüp atlar.
 * Dev hot-reload map'i sıfırlar — zararsız.
 *
 * Kilit worker-restart senaryosunu YUTMAZ: restart sonrası istemci yeniden bağlanırken
 * yeni token'ı saniyeler sonra ister ve denemeye devam eder (bkz. view-controller.tsx
 * RECONNECT_DELAY_MS + agent'ın katılma zaman aşımı) → 8 sn'lik pencere dolar, sonraki
 * POST gerçek dispatch'i atar. Kilidi DARALTMAK çift-ses yarışını geri açar; bu yüzden 8 sn.
 */
const dispatchInFlight = new Map<string, number>();
const DISPATCH_LOCK_MS = 8000;

/**
 * Dispatch kaydı "agent yolda" sayılma penceresi.
 *
 * Dispatch verildikten sonra agent'ın odaya KATILMASI zaman alır (worker job süreci doğar,
 * prewarm çalışır). O aralıkta odada AGENT katılımcı görünmez; katılımcıya bakıp hemen
 * yeniden dispatch atarsak İKİ agent doğar (çift ses). Bu yüzden TAZE bir dispatch kaydı
 * varsa (< bu süre) agent'ı "yolda" kabul edip dokunmuyoruz.
 *
 * Ölçüm (candan-lite-dev, soğuk worker): dispatch → agent katılımcı odada ≈ 2-8 sn.
 * 20 sn bunun rahat üstü; tek maliyeti, worker GERÇEKTEN ölüyse yeniden dispatch'in en fazla
 * 20 sn gecikmesi — istemci denemeye devam ettiği için kendi kendine toparlanır.
 */
const DISPATCH_GRACE_MS = 20000;

/**
 * Var olan oda için agent'ı server tarafında açıkça dispatch et.
 *
 * CANLI KANIT (2026-07-14, candan-lite-dev): worker ölünce LiveKit dispatch KAYDINI SİLMİYOR
 * ve job'un durumunu da GÜNCELLEMİYOR. Worker öldükten 60+ sn sonra, odada AGENT katılımcı
 * YOKKEN bile kayıt aynen duruyordu:
 *     id=AD_RNUp7AemGwKw agentName=candan deletedAt=0
 *       jobs(1): job AJ_Xr7chM7NvxWT status=RUNNING endedAt=0
 * Yani "kayıt var mı" — hatta "job'un durumu RUNNING mi" — sorusu agent'ın CANLI olup
 * olmadığını SÖYLEMEZ: kayıt oda ömrü boyunca ÖLÜ olarak durur. Eski kod "kayıt var →
 * dokunma" dediği için worker restart'tan sonra agent BİR DAHA dispatch edilmiyordu.
 * Sekmeyi kapatıp açmak "çözüyordu", çünkü oda boşalınca (departureTimeout=20 sn) SİLİNİYOR,
 * kayıtlar da onunla gidiyor ve YENİ odada token'a gömülü davet yeniden işleniyordu.
 *
 * DOĞRU CANLILIK SİNYALİ: odadaki AGENT kind KATILIMCI. Sıra:
 * - Kısa süre içinde bu oda için zaten deneme yapıldıysa: dokunma (in-flight kilidi).
 * - Oda YOKSA: dokunma — token'a gömülü dispatch oda yaratılırken zaten tetiklenir.
 * - Odada AGENT katılımcı VARSA: dokunma — agent canlı. (Kaydı görünmeden düşen gömülü
 *   dispatch de buraya düşer.)
 * - Katılımcı yok ama bu agent için TAZE dispatch kaydı varsa (< DISPATCH_GRACE_MS):
 *   dokunma — agent yolda olabilir. Çift-agent (çift ses) yarışını kapatan koşul BUDUR.
 * - Aksi halde (kayıt yok VEYA kayıt bayat = ölü job): `createDispatch`. Worker restart
 *   senaryosu tam olarak burasıdır.
 *
 * Hata dayanıklılığı: dispatch adımı patlarsa token yine döner (yeni-oda yolu bozulmaz);
 * hata yalnız console'a loglanır.
 */
async function ensureAgentDispatch(
  serverUrl: string,
  roomName: string,
  metadata: string
): Promise<void> {
  if (!AGENT_NAME || !API_KEY || !API_SECRET) return;
  // 2. savunma: senkron in-flight kilidi (await'ten ÖNCE damga bas → eşzamanlı POST'ları topla)
  const now = Date.now();
  const last = dispatchInFlight.get(roomName);
  if (last && now - last < DISPATCH_LOCK_MS) return; // yakında zaten denendi → atla
  dispatchInFlight.set(roomName, now);
  try {
    const roomService = new RoomServiceClient(serverUrl, API_KEY, API_SECRET);
    const rooms = await roomService.listRooms([roomName]);
    if (rooms.length === 0) return; // oda yok → gömülü dispatch halleder

    // 1. savunma: AGENT katılımcı odada mı? Tek güvenilir canlılık sinyali bu.
    const participants = await roomService.listParticipants(roomName);
    if (participants.some((p) => p.kind === ParticipantInfo_Kind.AGENT)) return; // agent canlı

    // Agent odada yok. Dispatch kaydı TAZE ise agent yolda olabilir → bekle (çift dispatch yok).
    // Kayıt BAYATSA job ölmüştür (durumu güncellenmediği için "RUNNING" görünse bile) → yeniden çağır.
    const dispatchClient = new AgentDispatchClient(serverUrl, API_KEY, API_SECRET);
    const dispatches = await dispatchClient.listDispatch(roomName);
    const newestCreatedAtMs = dispatches
      .filter((d) => d.agentName === AGENT_NAME)
      // state.createdAt NANOSANİYE (bigint) — ms'ye çevir. BigInt literali (0n/1_000_000n)
      // KULLANMIYORUZ: tsconfig target'ı ES2020'nin altında, derleyici reddediyor. Number()'a
      // çevirip bölüyoruz; ns→ms'de çift duyarlık kaybı ~0.0003 ms → karşılaştırma için önemsiz.
      .reduce((newest, d) => {
        const createdAtMs = Number(d.state?.createdAt ?? 0) / 1e6;
        return createdAtMs > newest ? createdAtMs : newest;
      }, 0);
    if (newestCreatedAtMs > 0 && now - newestCreatedAtMs < DISPATCH_GRACE_MS) return; // agent yolda

    await dispatchClient.createDispatch(roomName, AGENT_NAME, metadata ? { metadata } : undefined);
    console.log(
      `[token] var olan oda ${roomName} için agent dispatch edildi (${AGENT_NAME}` +
        `${metadata ? `, ${metadata}` : ''}` +
        `${newestCreatedAtMs > 0 ? ', bayat kayıt vardı → worker restart' : ''})`
    );
  } catch (error) {
    console.error('[token] ensureAgentDispatch başarısız (token yine dönüyor):', error);
  }
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
