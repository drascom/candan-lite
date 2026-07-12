/**
 * Explicit agent dispatch — TEK KAYNAK (single source of truth).
 *
 * Worker `WorkerOptions(agent_name=...)` ile kayıt olur (worker/agent.py, env:
 * LIVEKIT_AGENT_NAME / AGENT_NAME). agent_name verilen bir worker LiveKit'in
 * OTOMATİK dispatch'ini ALMAZ — sadece token'ın `roomConfig.agents` alanında
 * açıkça çağrıldığında iş alır. Bu yüzden web tarafı token'ı MUTLAKA bu adla
 * dispatch istemeli; ad uyuşmazsa agent odaya HİÇ girmez.
 *
 * Not: bu değer server-side env'den gelir (NEXT_PUBLIC_* DEĞİL). Token route'u
 * server'da çalıştığı için gerçek env'i görür; client bundle'da ise fallback
 * 'candan' geçerli olur (aynı ad).
 */
export const AGENT_NAME = process.env.AGENT_NAME || 'candan';
