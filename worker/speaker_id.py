"""Candan kapalı-grup konuşmacı kimliği: ReDimNet2 + kosinüs eşleştirme.

LiveKit ses pencerelerini ``speaker_tap`` üretir. Bu modül aynı pencereleri
ReDimNet2-B6 ile 192 boyutlu, L2-normalize gömmelere çevirir; kayıtlı ev halkı
merkezleriyle karşılaştırır ve eşik + ikinci adaya marj koşuluyla güvenli biçimde
isim ya da ``None`` döndürür.

Embedding'ler modele özel SQLite veritabanında float32 little-endian BLOB olarak
saklanır. Eski embedding uzaylarıyla ortak veritabanı kullanılmaz.
"""

from __future__ import annotations

import asyncio
import logging
import math
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
# SpeakerID — ReDimNet2 embedding + kosinüs eşleştirme (eşik + marj)
# ---------------------------------------------------------------------------
class SpeakerID:
    """ReDimNet2-B6'yı sarar; kayıtlı ev halkına karşı güvenli tanıma yapar."""

    def __init__(
        self,
        *,
        device: str = "cpu",
        model_repo: str = "PalabraAI/redimnet2",
        model_repo_dir: str | None = None,
        model_name: str = "b6",
        dataset: str = "vb2+vox2_v0",
        train_type: str = "lm",
        model_id: str = "redimnet2-b6-vb2+vox2_v0-lm",
        dim: int = 192,
        threshold: float = 0.5683009803,
        margin: float = 0.2146433070,
        merge_low: float = 0.50,
        enroll_weight: float = 0.7,
        drift_warn_frac: float = 0.10,
        min_profiles_for_auto_match: int = 2,
        window_seconds: float = 3.0,
        hop_seconds: float = 1.5,
        min_seconds: float = 1.5,
        rms_threshold: float = 0.008,
        batch_size: int = 16,
        num_threads: int = 2,
        model=None,
        torch_module=None,
    ):
        if torch_module is None:
            import torch

            torch_module = torch
        self._torch = torch_module
        if device == "mps" and not self._torch.backends.mps.is_available():
            raise RuntimeError("MPS istendi fakat PyTorch MPS kullanamıyor")
        if device.startswith("cuda") and not self._torch.cuda.is_available():
            raise RuntimeError("CUDA istendi fakat PyTorch CUDA kullanamıyor")
        self.device = self._torch.device(device)
        if device == "cpu" and num_threads > 0:
            self._torch.set_num_threads(max(1, int(num_threads)))
        if model is None:
            load_from = _resolve(model_repo_dir) if model_repo_dir else model_repo
            kwargs = {
                "model_name": model_name,
                "train_type": train_type,
                "dataset": dataset,
                "pretrained": True,
            }
            if model_repo_dir:
                model = self._torch.hub.load(load_from, "redimnet2", source="local", **kwargs)
            else:
                model = self._torch.hub.load(
                    load_from, "redimnet2", trust_repo=True, **kwargs
                )
        self._model = model.eval().to(self.device)
        self.dim = max(1, int(dim))
        self.model_id = model_id
        self.threshold = threshold
        self.margin = margin
        self.merge_low = merge_low
        self.enroll_weight = min(1.0, max(0.0, enroll_weight))
        self.drift_warn_frac = drift_warn_frac
        self.window_seconds = max(0.5, float(window_seconds))
        self.hop_seconds = max(0.1, float(hop_seconds))
        self.min_seconds = max(0.5, float(min_seconds))
        self.rms_threshold = max(0.0, float(rms_threshold))
        self.batch_size = max(1, int(batch_size))
        # Aynı job'da birden fazla LiveKit track'i paralel pencere üretebilir.
        self._lock = threading.Lock()
        self._names: list[str] = []
        self._centroids = np.zeros((0, self.dim), dtype=np.float32)  # L2-normalize
        self._name_to_id: dict[str, int] = {}
        self.last_ranking_top2: list[tuple[str, float]] = []
        self.min_profiles_for_auto_match = max(2, int(min_profiles_for_auto_match))

    # ---- embedding ----

    def embed_samples(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        """Mono float32 sesi pencerele, ReDimNet2 gömmelerini normalize merkezle birleştir."""
        return _l2(np.mean(self.embed_samples_many(samples, sample_rate), axis=0))

    def embed_samples_many(self, samples: np.ndarray, sample_rate: int) -> np.ndarray:
        """Her faydalı ReDimNet2 penceresinin normalize gömmesini ayrı döndür."""
        windows = self._speech_windows(samples, sample_rate)
        chunks: list[np.ndarray] = []
        with self._lock:
            for start in range(0, len(windows), self.batch_size):
                batch_np = np.stack(windows[start : start + self.batch_size])
                batch = self._torch.from_numpy(batch_np).to(self.device)
                with self._torch.inference_mode():
                    output = self._model(batch)
                if isinstance(output, (tuple, list)):
                    output = output[0]
                values = output.detach().float().cpu().numpy().astype(np.float32)
                if values.ndim == 1:
                    values = values[None, :]
                chunks.extend(_l2(row) for row in values)
        if not chunks:
            raise ValueError("konuşma içeren yeterli ses penceresi bulunamadı")
        if chunks[0].shape[0] != self.dim:
            raise ValueError(
                f"ReDimNet2 gömme boyutu uyumsuz: {chunks[0].shape[0]} != {self.dim}"
            )
        return np.stack(chunks)

    def _speech_windows(self, samples: np.ndarray, sample_rate: int) -> list[np.ndarray]:
        samples = np.asarray(samples, dtype=np.float32)
        if samples.ndim > 1:
            samples = samples.mean(axis=-1)
        samples = np.ascontiguousarray(np.clip(samples.reshape(-1), -1.0, 1.0))
        if sample_rate <= 0:
            raise ValueError(f"geçersiz örnekleme hızı: {sample_rate}")
        if sample_rate != 16000:
            from scipy.signal import resample_poly

            divisor = math.gcd(int(sample_rate), 16000)
            samples = np.ascontiguousarray(
                resample_poly(samples, 16000 // divisor, int(sample_rate) // divisor),
                dtype=np.float32,
            )
        window = max(1, round(self.window_seconds * 16000))
        hop = max(1, round(self.hop_seconds * 16000))
        minimum = max(1, round(self.min_seconds * 16000))
        if samples.size < minimum:
            raise ValueError(
                f"ses çok kısa ({samples.size / 16000:.2f}sn < {self.min_seconds:.2f}sn)"
            )
        starts = range(0, max(1, samples.size - minimum + 1), hop)
        windows: list[np.ndarray] = []
        for start in starts:
            chunk = samples[start : start + window]
            if chunk.size < minimum:
                continue
            rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
            if rms < self.rms_threshold:
                continue
            if chunk.size < window:
                chunk = np.pad(chunk, (0, window - chunk.size))
            windows.append(np.ascontiguousarray(chunk, dtype=np.float32))
        if not windows:
            raise ValueError("konuşma içeren yeterli ses penceresi bulunamadı")
        return windows

    def embed_pcm(self, pcm: bytes, sample_rate: int, width: int, channels: int) -> np.ndarray:
        return self.embed_samples(pcm_to_f32(pcm, width, channels), sample_rate)

    # geriye-uyum takma ad (referansta bazı yerlerde `embed` bekleniyor)
    embed = embed_samples

    # ---- tanıma ----

    def identify(self, emb: np.ndarray) -> tuple[str | None, float]:
        """En iyi eşleşmeyi döndür. Eşik altı VEYA 2.'yi marj kadar geçmiyorsa
        (None, skor) = unknown."""
        self.last_ranking_top2 = []  # bayat sıralama kaydedilmesin
        if self._centroids.shape[0] == 0:
            return None, 0.0
        if self._centroids.shape[0] < self.min_profiles_for_auto_match:
            log.debug(
                "speaker-ID otomatik eşleşme kapalı: %d profil < gereken %d",
                self._centroids.shape[0], self.min_profiles_for_auto_match,
            )
            return None, 0.0
        q = _l2(np.asarray(emb, dtype=np.float32))
        scores = self._centroids @ q
        order = np.argsort(scores)[::-1]
        ranking = [(self._names[i], float(scores[i])) for i in order]
        self.last_ranking_top2 = ranking[:2]
        best = ranking[0][1]
        second = ranking[1][1] if len(ranking) > 1 else -1e9
        if best < self.threshold or (best - second) < self.margin:
            log.debug(
                "speaker-ID skorlar: %s (eşik=%.3f marj=%.3f)",
                ", ".join(f"{n}={s:.3f}" for n, s in ranking),
                self.threshold, self.margin,
            )
            return None, best
        log.info(
            "speaker-ID tanındı: %s (skor=%.3f, marj=%.3f)",
            ranking[0][0], best, best - second,
        )
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
CREATE TABLE IF NOT EXISTS speaker_expression_samples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    speaker_id  INTEGER NOT NULL,
    emotion     TEXT NOT NULL,
    prompt      TEXT NOT NULL,
    embedding   BLOB NOT NULL,
    audio_path  TEXT NOT NULL,
    duration_s  REAL NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_expression_speaker ON speaker_expression_samples(speaker_id, emotion);
"""


def _default_db_path() -> str:
    raw = os.getenv("SPEAKER_DB", "data/speakers-redimnet2.db")
    if Path(raw).name == "speakers.db":
        raise RuntimeError(
            "eski speakers.db ReDimNet2 ile kullanılamaz; "
            "SPEAKER_DB=data/speakers-redimnet2.db ayarlayın"
        )
    return _resolve(raw)


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
            self._validate_sample(conn, speaker_id, embedding, dim, model_id)
            cur = conn.execute(
                "INSERT INTO speaker_samples (speaker_id, embedding, source, created_at)"
                " VALUES (?, ?, ?, ?)",
                (speaker_id, embedding, source, now),
            )
            conn.execute(
                "UPDATE speakers SET"
                "  sample_count = (SELECT COUNT(*) FROM speaker_samples WHERE speaker_id = ?),"
                "  dim = ?,"
                "  model_id = ?,"
                "  updated_at = ?"
                " WHERE id = ?",
                (speaker_id, dim, model_id, now, speaker_id),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    @staticmethod
    def _validate_sample(
        conn: sqlite3.Connection,
        speaker_id: int,
        embedding: bytes,
        dim: int,
        model_id: str,
    ) -> None:
        """Farklı embedding uzaylarının aynı profile karışmasını engelle."""
        row = conn.execute(
            "SELECT dim, model_id FROM speakers WHERE id = ?", (speaker_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"konuşmacı bulunamadı: {speaker_id}")
        if dim <= 0 or len(embedding) != dim * 4:
            raise ValueError(
                f"geçersiz embedding: {len(embedding)} bayt, beklenen {dim * 4}"
            )
        if row["dim"] is not None and int(row["dim"]) != int(dim):
            raise ValueError(
                f"embedding boyutu profile uymuyor: {dim} != {row['dim']}"
            )
        if row["model_id"] and str(row["model_id"]) != str(model_id):
            raise ValueError(
                f"embedding modeli profile uymuyor: {model_id} != {row['model_id']}"
            )

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
                self._validate_sample(conn, speaker_id, embedding, dim, model_id)
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
                    "  dim = ?,"
                    "  model_id = ?,"
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

    def _has_sample_source(self, speaker_id: int, source: str) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM speaker_samples WHERE speaker_id = ? AND source = ? LIMIT 1",
                (speaker_id, source),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def _add_expression_sample(
        self, speaker_id: int, emotion: str, prompt: str, embedding: bytes,
        audio_path: str, duration_s: float,
    ) -> int:
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO speaker_expression_samples "
                "(speaker_id, emotion, prompt, embedding, audio_path, duration_s, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (speaker_id, emotion, prompt, embedding, audio_path, duration_s, time.time()),
            )
            conn.commit()
            return int(cur.lastrowid)
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

    def has_sample_source_sync(self, speaker_id: int, source: str) -> bool:
        return self._has_sample_source(speaker_id, source)

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

    async def add_expression_sample(
        self, speaker_id: int, emotion: str, prompt: str, embedding: bytes,
        audio_path: str, duration_s: float,
    ) -> int:
        return await asyncio.to_thread(
            self._add_expression_sample, speaker_id, emotion, prompt, embedding,
            audio_path, duration_s,
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


def _i(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, "") or default))
    except (TypeError, ValueError):
        return default


def create_speaker_id() -> SpeakerID:
    """Ortak ReDimNet2 yapılandırmasını CLI ve LiveKit Agent için kur."""
    model_id = os.getenv(
        "SPEAKER_MODEL_ID", "redimnet2-b6-vb2+vox2_v0-lm"
    )
    if not model_id.startswith("redimnet2-"):
        raise RuntimeError(
            f"eski/uyumsuz SPEAKER_MODEL_ID={model_id!r}; ReDimNet2 profili gerekli"
        )
    return SpeakerID(
        device=os.getenv("SPEAKER_DEVICE", "cpu"),
        model_repo=os.getenv("SPEAKER_MODEL_REPO", "PalabraAI/redimnet2"),
        model_repo_dir=(os.getenv("SPEAKER_MODEL_REPO_DIR") or None),
        model_name=os.getenv("SPEAKER_MODEL_NAME", "b6"),
        dataset=os.getenv("SPEAKER_MODEL_DATASET", "vb2+vox2_v0"),
        train_type=os.getenv("SPEAKER_MODEL_TRAIN_TYPE", "lm"),
        model_id=model_id,
        dim=_i("SPEAKER_EMBEDDING_DIM", 192),
        threshold=_f("SPEAKER_THRESHOLD", 0.5683009803),
        margin=_f("SPEAKER_MARGIN", 0.2146433070),
        merge_low=_f("SPEAKER_MERGE_LOW", 0.50),
        enroll_weight=_f("SPEAKER_ENROLL_WEIGHT", 0.7),
        drift_warn_frac=_f("SPEAKER_DRIFT_WARN_FRAC", 0.10),
        min_profiles_for_auto_match=_i("SPEAKER_MIN_PROFILES_FOR_AUTO_MATCH", 2),
        window_seconds=_f("SPEAKER_WINDOW_SECONDS", 3.0),
        hop_seconds=_f("SPEAKER_MIN_SECONDS", 1.5),
        min_seconds=_f("SPEAKER_MIN_SECONDS", 1.5),
        rms_threshold=_f("SPEAKER_VAD_RMS", 0.008),
        batch_size=_i("SPEAKER_BATCH_SIZE", 16),
        num_threads=_i("SPEAKER_NUM_THREADS", 2),
    )


def build_speaker_id() -> "SpeakerID | None":
    """Etkinse ReDimNet2'yi kur; hata halinde Agent'ı speaker-ID'siz bırak."""
    if not _b("SPEAKER_ID_ENABLED", False):
        return None
    try:
        sp = create_speaker_id()
        log.info(
            "speaker-ID etkin: %s/%s (cihaz=%s, dim=%d, eşik=%.3f, marj=%.3f,"
            " merge_low=%.2f, auto_min_profiles=%d)",
            os.getenv("SPEAKER_MODEL_REPO", "PalabraAI/redimnet2"),
            sp.model_id,
            sp.device,
            sp.dim,
            sp.threshold,
            sp.margin,
            sp.merge_low,
            sp.min_profiles_for_auto_match,
        )
        return sp
    except Exception as e:  # noqa: BLE001
        log.warning("speaker-ID başlatılamadı (%s) — kapalı", e)
        return None
