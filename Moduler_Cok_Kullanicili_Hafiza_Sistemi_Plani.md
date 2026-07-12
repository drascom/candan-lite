# Modüler Çok Kullanıcılı Hafıza Sistemi Planı

## 1. Amaç

Hermes Agent, pi.dev Agent ve gelecekte eklenecek diğer agent’ların ortak kullanabileceği, agent’tan bağımsız, modüler ve çok kullanıcılı bir hafıza sistemi oluşturmak.

Sistem aşağıdaki ihtiyaçları karşılamalıdır:

- Her kullanıcı için ayrı özel hafıza
- Her konuşma için ayrı oturum hafızası
- Aile üyeleri için ortak paylaşımlı hafıza
- Proje ve grup bazlı hafıza alanları
- Kullanıcılar ve agent’lar arasında yetki kontrolü
- Hermes’in güçlü hafıza yaklaşımının korunması
- Hazır açık kaynak bileşenlerden yararlanılması
- İleride hafıza motorunun değiştirilebilmesi

## 2. Temel Mimari

```text
Hermes Agent ─┐
pi.dev Agent ─┤
Web App ──────┤
Mobile App ───┼──► Memory Gateway
Other Agents ─┘           │
                          ├── Identity Resolver
                          ├── Scope Resolver
                          ├── Permission Engine
                          ├── Memory Router
                          ├── Privacy Filter
                          └── Audit Logger
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
                  Mem0         PostgreSQL       Qdrant
                    │
                    └──── Optional: Graphiti
```

Agent’lar veritabanlarına doğrudan bağlanmayacak. Tüm hafıza işlemleri `Memory Gateway` üzerinden geçecek.

## 3. Önerilen Teknoloji Yığını

### İlk sürüm

- **Memory Gateway:** FastAPI veya Node.js
- **Ana hafıza motoru:** Mem0
- **Yapılandırılmış veri ve yetkiler:** PostgreSQL
- **Vektör arama:** Qdrant
- **Geçici oturum verisi:** Redis, gerekirse
- **Kimlik doğrulama:** JWT veya mevcut kullanıcı sistemi
- **Deployment:** Docker Compose

### İkinci aşama

- **Temporal ve ilişkisel hafıza:** Graphiti
- **Graph database:** FalkorDB veya Neo4j
- **Skill Registry:** PostgreSQL veya Git tabanlı yapı

## 4. Kimlik Modeli

Her istek şu kimlik bilgilerini taşımalıdır:

```json
{
  "tenant_id": "family_001",
  "user_id": "user_ayhan",
  "session_id": "sess_01JXYZ",
  "agent_id": "hermes_main",
  "device_id": "macbook_ayhan"
}
```

| Alan | Açıklama |
|---|---|
| `tenant_id` | Aileyi, şirketi veya organizasyonu temsil eder |
| `user_id` | Kalıcı kullanıcı kimliğidir |
| `session_id` | Tek bir konuşma veya görev oturumudur |
| `agent_id` | Hermes, pi.dev veya başka agent’ı belirtir |
| `device_id` | İsteğe bağlı cihaz ayrımı sağlar |

`session_id`, kullanıcı kimliği yerine kullanılmamalıdır. Oturum geçici, kullanıcı kimliği kalıcıdır.

## 5. Hafıza Kapsamları

### 5.1 Session Memory

Sadece aktif konuşma veya görev içinde geçerlidir.

### 5.2 Private User Memory

Sadece ilgili kullanıcıya ait özel hafızadır.

### 5.3 Shared Family Memory

Aile üyelerinin ortak erişebileceği hafızadır.

### 5.4 Project Memory

Belirli bir projeye erişimi olan kullanıcılar tarafından paylaşılır.

### Sonraki aşamada eklenebilecek kapsamlar

- Group Memory
- Company Memory
- Department Memory
- System Memory
- Guest Memory

## 6. Varsayılan Gizlilik Kuralı

Yeni bilgiler varsayılan olarak özel hafızaya yazılmalıdır.

```text
Yeni bilgi
   ↓
Private User Memory
```

Bir bilgi yalnızca şu durumlarda ortak hafızaya yazılmalıdır:

1. Kullanıcı açıkça ortak hafızaya kaydedilmesini isterse
2. Konuşma zaten ortak bir proje veya aile oturumu içindeyse
3. Bilgi türü önceden ortak olarak tanımlanmışsa
4. Kullanıcı paylaşımı onaylamışsa

Agent özel bir bilgiyi kendi kararıyla aile hafızasına taşıyamamalıdır.

## 7. Hafıza Türleri

- Working Memory
- Episodic Memory
- Semantic Memory
- User Profile Memory
- Project Memory
- Procedural Memory / Skills
- Artifact Memory

## 8. Hermes’ten Alınacak Temel Fikirler

- Küçük ve yüksek kaliteli persistent memory
- Geçmiş konuşmaların tamamını yüklemek yerine session search
- Skill tabanlı procedural memory
- Progressive loading
- Agent-managed memory
- Başarılı görevlerden skill üretmeye dayalı learning loop

## 9. Hazır Sistemlerin Kullanımı

### Mem0

Ana kullanıcı ve session hafıza motoru olarak kullanılacak.

### Qdrant

Vektör arama ve metadata filtreleme için kullanılacak.

### PostgreSQL

Kullanıcılar, tenant’lar, oturumlar, yetkiler, metadata ve audit kayıtları için otoriter veri kaynağı olacaktır.

### Graphiti

İlk sürümde zorunlu değildir. Zamana göre değişen bilgiler ve ilişki grafı gerektiğinde eklenecektir.

## 10. Memory Gateway Görevleri

- Kimlik doğrulama
- Tenant doğrulama
- Kullanıcının tenant üyeliğini kontrol etme
- İzin verilen hafıza kapsamlarını hesaplama
- Hafıza motoruna sorgu gönderme
- Sonuçları yetkilere göre filtreleme
- Hafıza yazma kararını uygulama
- Gizli bilgileri maskeleme
- Audit log oluşturma
- Agent’lara ortak API sunma

### Örnek API uçları

```http
POST /v1/memories/search
POST /v1/memories
PATCH /v1/memories/{memory_id}
DELETE /v1/memories/{memory_id}
POST /v1/sessions
POST /v1/sessions/{session_id}/finalize
GET /v1/users/{user_id}/profile
PATCH /v1/users/{user_id}/profile
POST /v1/skills/search
POST /v1/skills
```

## 11. Hafıza Yazma Akışı

```text
Conversation / Tool Result
           ↓
Candidate Memory Extraction
           ↓
Memory Type Classification
           ↓
Privacy and Scope Check
           ↓
Duplicate Detection
           ↓
Conflict Detection
           ↓
Importance and Confidence Scoring
           ↓
Save / Merge / Reject / Ask Approval
```

## 12. Hafıza Okuma Akışı

```text
User Request
     ↓
Identity Validation
     ↓
Allowed Scopes Calculation
     ↓
Session Memory Search
     ↓
Private Memory Search
     ↓
Project / Group Search
     ↓
Family Shared Memory Search
     ↓
Permission Filter
     ↓
Ranking and Deduplication
     ↓
Context Injection
```

Önerilen öncelik sırası:

1. Session Memory
2. Private User Memory
3. Active Project Memory
4. Group Memory
5. Shared Family Memory
6. System Memory

## 13. Veritabanı Taslağı

Temel tablolar:

- `tenants`
- `users`
- `sessions`
- `projects`
- `project_members`
- `groups`
- `group_members`
- `memories`
- `memory_permissions`
- `memory_sources`
- `audit_logs`

## 14. Yetki Modeli

Önerilen roller:

| Rol | Yetki |
|---|---|
| `family_admin` | Kullanıcı ve ortak hafıza yönetimi |
| `adult` | Genel aile hafızasına erişim |
| `child` | Sınırlı aile hafızası erişimi |
| `guest` | Sadece açıkça paylaşılan içerikler |
| `service_agent` | Politika kapsamında okuma ve yazma |
| `system_admin` | Teknik yönetim |

Her erişimde şu kombinasyon doğrulanmalıdır:

```text
Authenticated User
+
Tenant Membership
+
Allowed Scope
+
Resource Permission
```

## 15. Agent Entegrasyonu

### Hermes Adapter

- Kullanıcı mesajından önce memory search
- İlgili hafızayı prompt’a ekleme
- Konuşma sonunda memory candidate üretme
- Skill arama
- Session finalize

### pi.dev Adapter

- Extension veya middleware kullanımı
- Mesaj öncesi hafıza arama
- Tool result sonrası hafıza adayı üretme
- Session ve user kimliğini Memory Gateway’e gönderme
- Ortak ve özel hafıza kapsamlarını ayırma

Her iki adapter aynı API’yi kullanmalıdır.

## 16. Uygulama Aşamaları

### Faz 1 — Temel Çok Kullanıcılı Hafıza

- Memory Gateway
- PostgreSQL
- Qdrant
- Mem0
- JWT kimlik doğrulama
- Tenant ve user ayrımı
- Session Memory
- Private Memory
- Shared Family Memory
- Project Memory

### Faz 2 — Hafıza Kalitesi

- Duplicate detection
- Conflict detection
- Importance scoring
- Confidence scoring
- Memory consolidation
- Session summarization
- User profile summary
- Progressive loading
- Hafıza yönetim arayüzü

### Faz 3 — Skill ve Öğrenme Sistemi

- Skill Registry
- Skill versioning
- Başarılı görevlerden skill üretme
- Skill onay süreci
- Agent bazlı skill erişimi
- Hata ve çözüm prosedürleri

### Faz 4 — Temporal Graph Memory

- Graphiti
- Neo4j veya FalkorDB
- Entity relationships
- Temporal facts
- Eski ve yeni gerçeklerin takibi

### Faz 5 — Yönetim ve Gözlemlenebilirlik

- Admin panel
- Hafıza görüntüleme ve düzenleme
- Hafıza silme
- Veri export
- Audit log ekranı
- Kullanım istatistikleri
- Hafıza doğruluk değerlendirmesi

## 17. İlk Prototip

```text
services:
  memory-gateway
  postgres
  qdrant
  mem0
  redis
```

İlk prototipte Graphiti ve skill engine zorunlu değildir.

### Test senaryoları

1. Bir kullanıcı özel bilgi kaydeder
2. Diğer kullanıcı bu bilgiyi göremez
3. Bir bilgi aile hafızasına kaydedilir
4. Yetkili aile üyeleri bu bilgiyi görebilir
5. İki session birbirinden izole edilir
6. Hermes’in yazdığı hafıza pi.dev tarafından bulunur
7. pi.dev’in yazdığı ortak hafıza Hermes tarafından bulunur
8. Hafıza güncellenir ve değişiklik audit log’a yazılır
9. Hafıza silinir veya export edilir

## 18. Ana Tasarım Kararları

- Hafıza agent’tan bağımsız olacaktır
- Agent’lar veritabanlarına doğrudan erişmeyecektir
- Tüm erişim Memory Gateway üzerinden yapılacaktır
- PostgreSQL otoriter veri kaynağı olacaktır
- Qdrant arama indeksi olarak kullanılacaktır
- Mem0 ilk hafıza motoru olacaktır
- Graphiti yalnızca ihtiyaç oluştuğunda eklenecektir
- Yeni hafızalar varsayılan olarak private olacaktır
- Aile hafızasına yazma açık paylaşım veya politika gerektirecektir
- Her hafıza kaydı kaynak ve oluşturan bilgisi taşıyacaktır
- Her kullanıcı ve session ayrı kimliğe sahip olacaktır
- Hermes’in küçük persistent memory, session search, skill ve progressive loading yaklaşımı korunacaktır
- Hafıza motoru ileride değiştirilebilir olmalıdır

## 19. Son Hedef

```text
Universal Memory Platform
    ├── Multi-tenant
    ├── Multi-user
    ├── Multi-agent
    ├── Private Memory
    ├── Family Shared Memory
    ├── Project Memory
    ├── Session Memory
    ├── Permission Engine
    ├── Skill Registry
    ├── Temporal Graph
    ├── Audit and Privacy
    └── Agent-independent API
```

Bu yapı Hermes’in güçlü hafıza prensiplerini korurken, bunları çok kullanıcılı aile ve ileride şirket ortamına uyarlayacaktır.
