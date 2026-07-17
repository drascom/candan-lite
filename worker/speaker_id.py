"""Speaker-ID (voice-ID): sherpa-onnx CAM++ ile ses parmak-izinden kişi tanıma.

`hermes-livekit/voice/speaker.py` (SpeakerID) + `voice/speaker_store.py`
(SpeakerStore) portu — tek dosyada, candan-lite worker'ı için. Faz 2'yi
BOZMADAN additive: sherpa-onnx kurulu değilse / model yoksa / SPEAKER_ID_ENABLED
kapalıysa `build_speaker_id()` None döner ve çağıran taraf speaker-ID'yi atlar.

Embedding'ler DB'de HAM float32 little-endian BLOB olarak saklanır; normalize
etme bellekte (centroid kurulumu + sorgu anında) yapılır.

Yollar worker/ köküne göre relative (agent.py cwd = worker/): env
SPEAKER_MODEL_PATH=models/campplus.onnx, SPEAKER_DB=data/speakers.db.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

import numpy as np

from log_utils import DedupeFilter

log = logging.getLogger("worker.speaker_id")
log.addFilter(DedupeFilter())

# Bu dosyanın dizini = worker/. Relative env yollarını buna göre çöz.
WORKER_DIR = Path(__file__).resolve().parent

# speaker_samples.source ayrımı: makinenin kendi kendine eklediği örnekler. Geri
# kalan her şey ('voice-enroll', 'voice-enroll-merge', NULL) İNSAN onaylı kabul
# edilir = kimlik çapası. Beyaz liste değil kara liste: yeni bir enroll kaynağı
# eklenirse yanlışlıkla auto sayılıp ağırlığını kaybetmesin.
_AUTO_SOURCES = frozenset({"auto-learn"})


# ---------------------------------------------------------------------------
# yardımcılar
# ---------------------------------------------------------------------------
def _l2(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def _topk_stats(scores: np.ndarray, k: int) -> tuple[float, float]:
    """AS-norm için: skorların EN YÜKSEK k tanesinin ort/std. std çok küçükse (aynı
    gömme cohort'ta varsa) 0'a bölmeyi önlemek için tabana bastır. score.py'deki
    referans uygulamayla BİRE BİR aynı (deneyde kanıtlanan davranış korunsun)."""
    k = max(2, min(k, scores.size))
    top = np.sort(scores)[-k:]
    mu = float(np.mean(top))
    sd = float(np.std(top))
    return mu, (sd if sd > 1e-6 else 1e-6)


def pcm_to_f32(pcm: bytes, width: int, channels: int) -> np.ndarray:
    """Ham PCM baytlarını [-1,1] float32 mono diziye çevir (s16le veya f32le)."""
    if width == 2:
        a = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        a = np.frombuffer(pcm, dtype="<f4").astype(np.float32)
    else:
        raise ValueError(f"desteklenmeyen örnek genişliği: {width}")
    if channels > 1:
        a = a.reshape(-1, channels).mean(axis=1)
    return np.ascontiguousarray(a)


def emb_to_bytes(emb: np.ndarray) -> bytes:
    return emb.astype("<f4").tobytes()


def _resolve(path: str) -> str:
    """Relative yolu worker/ köküne göre mutlaklaştır (agent.py cwd'sinden bağımsız)."""
    p = Path(path)
    return str(p if p.is_absolute() else (WORKER_DIR / p))


# ---------------------------------------------------------------------------
# SpeakerID — sherpa-onnx embedding + kosinüs eşleştirme (eşik + marj)
# ---------------------------------------------------------------------------
class SpeakerID:
    """Tek bir embedding modelini sarar; enroll edilmiş kişilere karşı tanır."""

    def __init__(
        self,
        model_path: str,
        model_id: str,
        threshold: float = 0.45,
        margin: float = 0.05,
        num_threads: int = 1,
        merge_low: float = 0.35,
        enroll_weight: float = 0.7,
        drift_warn_frac: float = 0.10,
        asnorm_enabled: bool = False,
        asnorm_cohort_path: str | None = None,
        asnorm_k: int = 40,
        asnorm_threshold: float = -1.0,
        asnorm_margin: float = 1.0,
    ):
        import sherpa_onnx

        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=model_path, num_threads=num_threads, provider="cpu"
        )
        self._ex = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
        self.dim: int = self._ex.dim
        self.model_id = model_id
        # Ham-kosinüs karar parametreleri (AS-norm kapalı/uyumsuz iken geri-düşüş yolu).
        self.threshold = threshold
        self.margin = margin
        # Enroll koruması: bu skorun ALTI "gerçekten yeni kişi", arası belirsiz bant
        # (kullanıcıya "Sen X misin?" diye sorulur), threshold üstü = zaten kayıtlı.
        self.merge_low = merge_low
        # Centroid'de enroll grubunun payı (auto-learn grubu 1-w). Örnek sayısından
        # bağımsız → 109 auto-learn bile enroll'ü boğamaz.
        self.enroll_weight = min(1.0, max(0.0, enroll_weight))
        # Çapadan `threshold` kadar uzak auto-learn oranı bunu AŞARSA WARNING.
        self.drift_warn_frac = drift_warn_frac
        self._lock = threading.Lock()  # extractor stream'i seri kullanılsın
        self._names: list[str] = []
        self._centroids = np.zeros((0, self.dim), dtype=np.float32)  # L2-normalize
        self._name_to_id: dict[str, int] = {}

        # ---- AS-norm skor kalibrasyonu (opsiyonel) ----
        # Offline ölçümde ham-kosinüs + sabit-eşik ev sahibi ile eşini AYIRAMADI
        # (marj +0.14, gürültüde eksiye döndü). AS-norm ile marj +2.0 oldu, çapraz-kişi
        # belirgin NEGATİF → ayrım çalıştı. Cohort'a göre skor normalize edilir:
        # herkese benzeyen ses cezalandırılır, kişiler-arası karşılaştırılabilir olur.
        self.asnorm_k = int(asnorm_k)
        self.asnorm_threshold = asnorm_threshold
        self.asnorm_margin = asnorm_margin
        self._cohort: np.ndarray | None = None  # (N, dim) L2-normalize yabancı gömme
        # Her enrolled centroid için (μ_e, σ_e) — reload/enroll'de BİR KEZ hesaplanır,
        # her identify()'de yeniden hesaplanmaz (centroid↔cohort skorları sabit).
        self._cent_asnorm_stats = np.zeros((0, 2), dtype=np.float32)
        # AS-norm ancak: açık + cohort yüklendi + cohort dim == encoder dim iken aktif.
        # Aksi halde GÜVENLİ GERİ-DÜŞÜŞ: ham-kosinüs davranışı, çökme yok.
        self._asnorm_active = False
        if asnorm_enabled:
            self._load_cohort(asnorm_cohort_path)

    def _load_cohort(self, path: str | None) -> None:
        """Cohort .npy'yi yükle + L2-normalize et. Yoksa / dim uyuşmazsa AS-norm'u
        kapat (bir kez uyar) ve ham-kosinüs geri-düşüşünde kal — mevcut sistemi BOZMA."""
        if not path:
            log.warning("AS-norm açık ama cohort yolu boş — ham-kosinüs geri-düşüşü")
            return
        cohort_path = _resolve(path)
        if not os.path.isfile(cohort_path):
            log.warning("AS-norm cohort yok: %s — ham-kosinüs geri-düşüşü", cohort_path)
            return
        try:
            raw = np.load(cohort_path).astype(np.float32)
        except Exception as e:  # noqa: BLE001
            log.warning("AS-norm cohort yüklenemedi (%s) — ham-kosinüs geri-düşüşü", e)
            return
        if raw.ndim != 2 or raw.shape[1] != self.dim:
            # KRİTİK: cohort WeSpeaker (256) ise ve canlı encoder CAM++ (192) ise burada
            # yakalanır → AS-norm sessizce kapanır, sistem ham-kosinüsle çalışmaya devam eder.
            log.warning(
                "AS-norm cohort dim uyuşmuyor (cohort=%s, encoder dim=%d) — AS-norm KAPALI,"
                " ham-kosinüs geri-düşüşü. Cohort ve canlı encoder AYNI model olmalı.",
                raw.shape, self.dim,
            )
            return
        # Satırları L2-normalize (zaten normal ama garanti; ham gömme gelirse de çalışsın).
        norms = np.linalg.norm(raw, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self._cohort = np.ascontiguousarray(raw / norms, dtype=np.float32)
        self._asnorm_active = True
        log.info(
            "AS-norm etkin: cohort N=%d dim=%d, K=%d, eşik=%.2f, marj=%.2f",
            self._cohort.shape[0], self._cohort.shape[1], self.asnorm_k,
            self.asnorm_threshold, self.asnorm_margin,
        )

    def _recompute_centroid_asnorm_stats(self) -> None:
        """Her enrolled centroid için cohort'a göre (μ_e, σ_e) önbelleğini yenile.
        reload()/enroll SONRASI çağrılır — centroid değişince μ_e,σ_e değişir."""
        n = self._centroids.shape[0]
        if not self._asnorm_active or self._cohort is None or n == 0:
            self._cent_asnorm_stats = np.zeros((0, 2), dtype=np.float32)
            return
        # se[i] = centroid_i ↔ tüm cohort kosinüs (ikisi de L2-norm).
        se_all = self._centroids @ self._cohort.T  # (n_centroid, N_cohort)
        stats = np.empty((n, 2), dtype=np.float32)
        for i in range(n):
            mu_e, sd_e = _topk_stats(se_all[i], self.asnorm_k)
            stats[i, 0] = mu_e
            stats[i, 1] = sd_e
        self._cent_asnorm_stats = stats

    # ---- embedding ----

    def embed_samples(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        """float32 mono dalga → ham embedding (normalize edilmemiş). sherpa,
        sample_rate 16k değilse içeride resample eder."""
        samples = np.ascontiguousarray(np.asarray(samples, dtype=np.float32))
        with self._lock:
            stream = self._ex.create_stream()
            stream.accept_waveform(sample_rate=sample_rate, waveform=samples)
            stream.input_finished()
            return np.array(self._ex.compute(stream), dtype=np.float32)

    def embed_pcm(self, pcm: bytes, sample_rate: int, width: int, channels: int) -> np.ndarray:
        return self.embed_samples(pcm_to_f32(pcm, width, channels), sample_rate)

    # geriye-uyum takma ad (referansta bazı yerlerde `embed` bekleniyor)
    embed = embed_samples

    # ---- tanıma ----

    def identify(self, emb: np.ndarray) -> tuple[str | None, float]:
        """En iyi eşleşmeyi döndür. Eşik altı VEYA 2.'yi marj kadar geçmiyorsa
        (None, skor) = unknown."""
        if self._centroids.shape[0] == 0:
            return None, 0.0
        q = _l2(np.asarray(emb, dtype=np.float32))
        raw_sims = self._centroids @ q  # ham kosinüs (centroid'ler L2-normalize)
        if self._asnorm_active and self._cohort is not None:
            # AS-norm: her identify()'de SADECE test gömmenin cohort istatistiği
            # hesaplanır (N nokta-çarpım, ucuz); centroid'lerin (μ_e,σ_e) önbellekten.
            # s_norm = 0.5*((s-μ_t)/σ_t + (s-μ_e)/σ_e) — deneyde kanıtlanan simetrik form.
            st = self._cohort @ q  # test ↔ cohort
            mu_t, sd_t = _topk_stats(st, self.asnorm_k)
            mu_e = self._cent_asnorm_stats[:, 0]
            sd_e = self._cent_asnorm_stats[:, 1]
            scores = 0.5 * ((raw_sims - mu_t) / sd_t + (raw_sims - mu_e) / sd_e)
            thr, margin, scale = self.asnorm_threshold, self.asnorm_margin, "asnorm"
        else:
            # Geri-düşüş: ham kosinüs + eski eşik/marj (mevcut davranış).
            scores = raw_sims
            thr, margin, scale = self.threshold, self.margin, "ham"
        order = np.argsort(scores)[::-1]
        ranking = [(self._names[i], float(scores[i])) for i in order]
        best = ranking[0][1]
        second = ranking[1][1] if len(ranking) > 1 else -1e9
        # Her çağrıda (saniyede bir) INFO basmak log'u boğuyordu; skor dökümü
        # sadece hata ayıklarken lazım, o yüzden unknown'da DEBUG'a indi.
        # NOT: dönen skor `scale`'e göre (asnorm ölçeği ham 0-1 DEĞİL, ~[-14,+8]).
        if best < thr or (best - second) < margin:
            log.debug(
                "speaker-ID skorlar [%s]: %s (eşik=%.2f marj=%.2f)",
                scale, ", ".join(f"{n}={s:.3f}" for n, s in ranking), thr, margin,
            )
            return None, best
        log.info("speaker-ID tanındı [%s]: %s (skor=%.3f)", scale, ranking[0][0], best)
        return ranking[0][0], best

    def best_match(self, emb: np.ndarray) -> tuple[str | None, float]:
        """HAM en-yakın centroid (eşik/marj UYGULANMAZ). Enroll öncesi "bu ses
        zaten kayıtlı birine benziyor mu?" kontrolü için. Kimse yoksa (None, 0.0)."""
        if self._centroids.shape[0] == 0:
            return None, 0.0
        q = _l2(np.asarray(emb, dtype=np.float32))
        sims = self._centroids @ q
        i = int(np.argmax(sims))
        return self._names[i], float(sims[i])

    def num_speakers(self) -> int:
        return len(self._names)

    def id_for(self, name: str | None) -> int | None:
        return self._name_to_id.get(name) if name else None

    def names(self) -> list[str]:
        """Kayıtlı kişi isimleri (rol komutunda ismi eşlemek için)."""
        return list(self._names)

    def reload(self, speakers: list[dict]) -> None:
        """DB'deki kişileri belleğe al: örnek embedding'leri normalize et, ortala,
        normalize et = centroid. model_id/dim uyuşmayanı atla (tutarlılık kilidi).

        Centroid DÜZ ortalama DEĞİL: enroll örnekleri ile auto-learn örnekleri ayrı
        ortalanıp `enroll_weight` ile harmanlanır. Neden: düz ortalamada ağırlık örnek
        SAYISINA gider; canlı DB'de 2 enroll vs 109 auto-learn = gerçek kimliğin sözü
        %1.8'e düşmüştü ve geri-besleme döngüsü (tanı → örnek ekle → centroid kay)
        centroid'i "duyulan her şeyin ortalaması"na çevirmişti. Grup ağırlığıyla enroll'ün
        payı örnek sayısından BAĞIMSIZ sabit kalır → kayma matematiksel olarak sınırlı.
        """
        names: list[str] = []
        cents: list[np.ndarray] = []
        name_to_id: dict[str, int] = {}
        for sp in speakers:
            if sp.get("model_id") and sp["model_id"] != self.model_id:
                log.warning(
                    "speaker %r model_id uyuşmuyor (%s != %s) — atlanıyor",
                    sp.get("name"), sp["model_id"], self.model_id,
                )
                continue
            sources = sp.get("sources") or []
            enroll: list[np.ndarray] = []
            auto: list[np.ndarray] = []
            for i, b in enumerate(sp.get("embeddings", [])):
                v = np.frombuffer(b, dtype="<f4").astype(np.float32)
                if v.shape[0] != self.dim:
                    continue
                src = sources[i] if i < len(sources) else None
                # `sources` yoksa (eski çağıran) hepsi enroll sayılır → eski düz-ortalama
                # davranışı; sessizce auto muamelesi yapıp ağırlığı bozmaktan iyi.
                (auto if src in _AUTO_SOURCES else enroll).append(_l2(v))
            if not enroll and not auto:
                continue
            if enroll and auto:
                w = self.enroll_weight
                mean = w * np.mean(np.stack(enroll), axis=0) + (1.0 - w) * np.mean(
                    np.stack(auto), axis=0
                )
            else:
                mean = np.mean(np.stack(enroll or auto), axis=0)
            cent = _l2(mean)
            # Kaçış tespiti. Ölçülen şey: auto-learn örneklerinin KAÇTA KAÇI enroll
            # çapasına `threshold`'dan uzak — yani "bu kişi değil" diyeceğimiz kadar.
            #
            # Neden bu, "centroid çapadan ne kadar saptı" DEĞİL: (a) grup ağırlığı o
            # mesafeyi matematiksel olarak yukarı kilitler → metrik ölü doğar;
            # (b) meşru uyum (nezle/mikrofon) ile kirlenmeyi AYIRAMAZ — ikisi de
            # "çapadan uzaklaşma"dır, hatta ölçümde nezle kirlenmeden daha uzak çıktı.
            # Kümenin BÖLÜNMESİ ayırt edici: aynı kişinin sesi kaysa da örnekleri
            # birlikte taşınır (hepsi çapaya makul yakın kalır); yabancı girdiğinde
            # örneklerin bir kısmı çapadan tamamen kopar. Canlı kirlenmiş DB'de bu oran
            # %14 (109 örneğin 15'i < 0.45), sağlıklı/uyum simülasyonunda %0.
            if enroll and auto:
                anchor = _l2(np.mean(np.stack(enroll), axis=0))
                sims = np.stack(auto) @ anchor
                frac = float(np.mean(sims < self.threshold))
                if frac > self.drift_warn_frac:
                    log.warning(
                        "speaker %r auto-learn kirlenmiş olabilir: %d/%d örnek (%.0f%%)"
                        " enroll çapasına %.2f'den uzak (min=%.3f, ort=%.3f) — bu örnekler"
                        " %r değil. auto-learn örneklerini temizleyip yeniden enroll düşünün.",
                        sp.get("name"), int((sims < self.threshold).sum()), len(auto),
                        frac * 100, self.threshold, float(sims.min()), float(sims.mean()),
                        sp.get("name"),
                    )
            cents.append(cent)
            names.append(sp["name"])
            if sp.get("id") is not None:
                name_to_id[sp["name"]] = sp["id"]
        self._names = names
        self._name_to_id = name_to_id
        self._centroids = (
            np.stack(cents) if cents else np.zeros((0, self.dim), dtype=np.float32)
        )
        # Centroid'ler değişti → AS-norm (μ_e,σ_e) önbelleğini yenile. enroll/auto-learn
        # de reload() üzerinden geçtiği için önbellek otomatik güncel kalır.
        self._recompute_centroid_asnorm_stats()
        log.info("speaker-ID: %d kişi yüklendi (%s)", len(names), ", ".join(names) or "—")


# ---------------------------------------------------------------------------
# SpeakerStore — stdlib sqlite3 (speakers + speaker_samples), boş başlar
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS speakers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    user_id      INTEGER,
    dim          INTEGER,
    model_id     TEXT,
    sample_count INTEGER DEFAULT 0,
    enrolled_at  REAL,
    updated_at   REAL
);
CREATE TABLE IF NOT EXISTS speaker_samples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    speaker_id  INTEGER NOT NULL,
    embedding   BLOB NOT NULL,
    source      TEXT,
    created_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_samples_speaker ON speaker_samples(speaker_id);
"""


def _default_db_path() -> str:
    return _resolve(os.getenv("SPEAKER_DB", "data/speakers.db"))


def name_key(name: str) -> str:
    return " ".join((name or "").split()).casefold()


_name_key = name_key  # geriye-uyum takma ad


class SpeakerStore:
    """Senkron çekirdek + async sarmalayıcı. Boş DB'yle başlar (dizini oluşturur)."""

    def __init__(self, path: str | None = None):
        self.path = path or _default_db_path()
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._init_sync()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_sync(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _create_speaker(self, name: str, user_id: int | None) -> dict:
        conn = self._connect()
        try:
            key = _name_key(name)
            if user_id is None:
                cur = conn.execute(
                    "SELECT id, name, user_id FROM speakers WHERE user_id IS NULL ORDER BY id"
                )
            else:
                cur = conn.execute(
                    "SELECT id, name, user_id FROM speakers WHERE user_id = ? ORDER BY id",
                    (user_id,),
                )
            for row in cur.fetchall():
                if _name_key(row["name"]) == key:
                    return dict(row)
            cur = conn.execute(
                "INSERT INTO speakers (name, user_id, sample_count, enrolled_at)"
                " VALUES (?, ?, 0, ?)",
                (name, user_id, time.time()),
            )
            conn.commit()
            return {"id": cur.lastrowid, "name": name, "user_id": user_id}
        finally:
            conn.close()

    def _add_sample(self, speaker_id: int, embedding: bytes, dim: int,
                    model_id: str, source: str | None) -> int:
        now = time.time()
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO speaker_samples (speaker_id, embedding, source, created_at)"
                " VALUES (?, ?, ?, ?)",
                (speaker_id, embedding, source, now),
            )
            conn.execute(
                "UPDATE speakers SET"
                "  sample_count = (SELECT COUNT(*) FROM speaker_samples WHERE speaker_id = ?),"
                "  dim = COALESCE(dim, ?),"
                "  model_id = COALESCE(model_id, ?),"
                "  updated_at = ?"
                " WHERE id = ?",
                (speaker_id, dim, model_id, now, speaker_id),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def _list_speakers(self) -> list[dict]:
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT id, name, user_id, dim, model_id, sample_count, enrolled_at, updated_at"
                " FROM speakers ORDER BY id"
            )
            return [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def _add_auto_sample(self, speaker_id: int, embedding: bytes, dim: int,
                         model_id: str, max_total: int) -> tuple[int, int]:
        """auto-learn örneği ekle + kişi başına KÜRESEL (kalıcı) tavanı uygula.

        Tavan neden burada, çağıranda değil: LiveKit her oda oturumu için yeni bir
        job süreci açar → süreç-içi sayaç sıfırlanır ve tavan hiç dolmaz (canlı DB'de
        ~55 oturum × 2 = 109 örnek böyle birikti). Tek güvenilir sayaç DB'nin kendisi,
        ve insert+budama tek transaction'da olmalı ki eşzamanlı job'lar tavanı aşmasın.

        FIFO: tavan dolunca en ESKİ auto-learn örneği düşer. `source` filtresi sayesinde
        'voice-enroll'/'voice-enroll-merge' örnekleri ASLA silinmez — onlar kimlik çapası.
        Döner: (eklenen_satır_id, atılan_örnek_sayısı).
        """
        now = time.time()
        keep = max(0, int(max_total))
        conn = self._connect()
        try:
            with conn:  # tek transaction: insert + budama atomik
                cur = conn.execute(
                    "INSERT INTO speaker_samples (speaker_id, embedding, source, created_at)"
                    " VALUES (?, ?, 'auto-learn', ?)",
                    (speaker_id, embedding, now),
                )
                new_id = cur.lastrowid
                # En yeni `keep` tanesini tut, geri kalan auto-learn'leri at.
                # LIMIT -1 OFFSET n = "ilk n satırdan sonrasının tamamı" (sqlite).
                dropped = conn.execute(
                    "DELETE FROM speaker_samples WHERE id IN ("
                    "  SELECT id FROM speaker_samples"
                    "   WHERE speaker_id = ? AND source = 'auto-learn'"
                    "   ORDER BY id DESC LIMIT -1 OFFSET ?"
                    ")",
                    (speaker_id, keep),
                ).rowcount
                conn.execute(
                    "UPDATE speakers SET"
                    "  sample_count = (SELECT COUNT(*) FROM speaker_samples WHERE speaker_id = ?),"
                    "  dim = COALESCE(dim, ?),"
                    "  model_id = COALESCE(model_id, ?),"
                    "  updated_at = ?"
                    " WHERE id = ?",
                    (speaker_id, dim, model_id, now, speaker_id),
                )
            return int(new_id), int(max(0, dropped))
        finally:
            conn.close()

    def _embeddings(self, speaker_id: int) -> list[tuple[bytes, str | None]]:
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT embedding, source FROM speaker_samples WHERE speaker_id = ? ORDER BY id",
                (speaker_id,),
            )
            return [(r["embedding"], r["source"]) for r in cur.fetchall()]
        finally:
            conn.close()

    def _all_with_embeddings(self) -> list[dict]:
        out = []
        for sp in self._list_speakers():
            sp = dict(sp)
            rows = self._embeddings(sp["id"])
            # `embeddings` şekli değişmedi (eski çağıranlar bozulmaz); `sources` ek
            # bilgi — reload() enroll/auto ayrımını buradan yapıyor.
            sp["embeddings"] = [b for b, _ in rows]
            sp["sources"] = [s for _, s in rows]
            out.append(sp)
        return out

    def _delete_speaker(self, speaker_id: int) -> None:
        conn = self._connect()
        try:
            conn.execute("DELETE FROM speaker_samples WHERE speaker_id = ?", (speaker_id,))
            conn.execute("DELETE FROM speakers WHERE id = ?", (speaker_id,))
            conn.commit()
        finally:
            conn.close()

    # ---- sync API (enroll CLI kullanır) ----

    def create_speaker_sync(self, name: str, user_id: int | None = None) -> dict:
        return self._create_speaker(name, user_id)

    def add_sample_sync(self, speaker_id: int, embedding: bytes, dim: int,
                        model_id: str, source: str | None = None) -> int:
        return self._add_sample(speaker_id, embedding, dim, model_id, source)

    def list_speakers_sync(self) -> list[dict]:
        return self._list_speakers()

    def all_speaker_embeddings_sync(self) -> list[dict]:
        return self._all_with_embeddings()

    # ---- async sarmalayıcı (worker event loop'unu bloklamaz) ----

    async def create_speaker(self, name: str, user_id: int | None = None) -> dict:
        return await asyncio.to_thread(self._create_speaker, name, user_id)

    async def add_speaker_sample(self, speaker_id: int, embedding: bytes, dim: int,
                                 model_id: str, source: str | None = None) -> int:
        return await asyncio.to_thread(self._add_sample, speaker_id, embedding, dim, model_id, source)

    async def add_auto_learn_sample(self, speaker_id: int, embedding: bytes, dim: int,
                                    model_id: str, max_total: int) -> tuple[int, int]:
        """auto-learn örneği ekle, kişi başına küresel tavanı FIFO ile uygula."""
        return await asyncio.to_thread(
            self._add_auto_sample, speaker_id, embedding, dim, model_id, max_total
        )

    async def list_speakers(self) -> list[dict]:
        return await asyncio.to_thread(self._list_speakers)

    async def all_speaker_embeddings(self) -> list[dict]:
        """SpeakerID.reload(...) formatı: her kişi + tüm örnek embedding'leri."""
        return await asyncio.to_thread(self._all_with_embeddings)

    async def delete_speaker(self, speaker_id: int) -> None:
        return await asyncio.to_thread(self._delete_speaker, speaker_id)


# ---------------------------------------------------------------------------
# fabrika — kapalı/eksikse None (graceful degrade)
# ---------------------------------------------------------------------------
def _b(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def build_speaker_id() -> "SpeakerID | None":
    """Env'e göre SpeakerID kur; SPEAKER_ID_ENABLED kapalı / model yok /
    sherpa-onnx yok ise None döner (Faz 2 aynen çalışsın)."""
    if not _b("SPEAKER_ID_ENABLED", False):
        return None
    model_path = _resolve(os.getenv("SPEAKER_MODEL_PATH", "models/campplus.onnx"))
    if not os.path.isfile(model_path):
        log.warning("SPEAKER_ID_ENABLED açık ama model yok: %s — kapalı", model_path)
        return None
    model_id = os.getenv("SPEAKER_MODEL_ID", "campplus_zh_en_advanced_v1")
    try:
        sp = SpeakerID(
            model_path,
            model_id,
            _f("SPEAKER_THRESHOLD", 0.45),
            _f("SPEAKER_MARGIN", 0.05),
            merge_low=_f("SPEAKER_MERGE_LOW", 0.35),
            enroll_weight=_f("SPEAKER_ENROLL_WEIGHT", 0.7),
            drift_warn_frac=_f("SPEAKER_DRIFT_WARN_FRAC", 0.10),
            asnorm_enabled=_b("SPEAKER_ASNORM_ENABLED", True),
            asnorm_cohort_path=os.getenv("SPEAKER_ASNORM_COHORT", "models/asnorm_cohort.npy"),
            asnorm_k=int(_f("SPEAKER_ASNORM_K", 40)),
            asnorm_threshold=_f("SPEAKER_ASNORM_THRESHOLD", -1.0),
            asnorm_margin=_f("SPEAKER_ASNORM_MARGIN", 1.0),
        )
        log.info(
            "speaker-ID etkin: %s (dim=%d, eşik=%.2f, marj=%.2f, merge_low=%.2f,"
            " enroll_w=%.2f, drift_warn_frac=%.2f, asnorm=%s)",
            model_path, sp.dim, sp.threshold, sp.margin, sp.merge_low,
            sp.enroll_weight, sp.drift_warn_frac, sp._asnorm_active,
        )
        return sp
    except Exception as e:  # noqa: BLE001
        log.warning("speaker-ID başlatılamadı (%s) — kapalı", e)
        return None
