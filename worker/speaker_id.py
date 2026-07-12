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


# ---------------------------------------------------------------------------
# yardımcılar
# ---------------------------------------------------------------------------
def _l2(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


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
    ):
        import sherpa_onnx

        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=model_path, num_threads=num_threads, provider="cpu"
        )
        self._ex = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
        self.dim: int = self._ex.dim
        self.model_id = model_id
        self.threshold = threshold
        self.margin = margin
        # Enroll koruması: bu skorun ALTI "gerçekten yeni kişi", arası belirsiz bant
        # (kullanıcıya "Sen X misin?" diye sorulur), threshold üstü = zaten kayıtlı.
        self.merge_low = merge_low
        self._lock = threading.Lock()  # extractor stream'i seri kullanılsın
        self._names: list[str] = []
        self._centroids = np.zeros((0, self.dim), dtype=np.float32)  # L2-normalize
        self._name_to_id: dict[str, int] = {}

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
        sims = self._centroids @ q  # centroid'ler zaten L2-normalize
        order = np.argsort(sims)[::-1]
        ranking = [(self._names[i], float(sims[i])) for i in order]
        log.info(
            "speaker-ID skorlar: %s (eşik=%.2f marj=%.2f)",
            ", ".join(f"{n}={s:.3f}" for n, s in ranking),
            self.threshold, self.margin,
        )
        best = ranking[0][1]
        second = ranking[1][1] if len(ranking) > 1 else -1.0
        if best < self.threshold or (best - second) < self.margin:
            return None, best
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

    def reload(self, speakers: list[dict]) -> None:
        """DB'deki kişileri belleğe al: örnek embedding'leri normalize et, ortala,
        normalize et = centroid. model_id/dim uyuşmayanı atla (tutarlılık kilidi)."""
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
            embs = []
            for b in sp.get("embeddings", []):
                v = np.frombuffer(b, dtype="<f4").astype(np.float32)
                if v.shape[0] != self.dim:
                    continue
                embs.append(_l2(v))
            if not embs:
                continue
            cents.append(_l2(np.mean(np.stack(embs), axis=0)))
            names.append(sp["name"])
            if sp.get("id") is not None:
                name_to_id[sp["name"]] = sp["id"]
        self._names = names
        self._name_to_id = name_to_id
        self._centroids = (
            np.stack(cents) if cents else np.zeros((0, self.dim), dtype=np.float32)
        )
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

    def _embeddings(self, speaker_id: int) -> list[bytes]:
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT embedding FROM speaker_samples WHERE speaker_id = ? ORDER BY id",
                (speaker_id,),
            )
            return [r["embedding"] for r in cur.fetchall()]
        finally:
            conn.close()

    def _all_with_embeddings(self) -> list[dict]:
        out = []
        for sp in self._list_speakers():
            sp = dict(sp)
            sp["embeddings"] = self._embeddings(sp["id"])
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
        )
        log.info(
            "speaker-ID etkin: %s (dim=%d, eşik=%.2f, marj=%.2f, merge_low=%.2f)",
            model_path, sp.dim, sp.threshold, sp.margin, sp.merge_low,
        )
        return sp
    except Exception as e:  # noqa: BLE001
        log.warning("speaker-ID başlatılamadı (%s) — kapalı", e)
        return None
