# Bulart Coordinator — API Specification v1.0

Все эндпоинты требуют заголовок:
```
X-API-Key: <secret>
```

Base URL (VPS): `http://<tailscale-ip>:8080`

---

## Task Lifecycle

```
new → triage → assigned → processing → done
                                     ↘ failed
```

- **new**: задача создана, ещё не обработана triage
- **triage**: VPS делает предварительную обработку (субтитры YouTube, извлечение статьи)
- **assigned**: выдана worker'у (lease_until установлен)
- **processing**: worker активно работает над задачей (продлевает lease)
- **done**: worker прислал результат, vault_writer записал заметку
- **failed**: исчерпаны попытки (>= max_attempts); задача в dead letter

---

## Lease Mechanism

При `POST /tasks/claim` ставится `lease_until = now + lease_duration_minutes`.

Worker должен продлевать lease через `POST /tasks/{id}/extend` каждые ~3 минуты для долгих задач.

Фоновый процесс Coordinator'а каждую минуту проверяет истёкшие lease:
- если `attempts < max_attempts` → статус `new`, `assigned_to = null`
- если `attempts >= max_attempts` → статус `failed`

---

## Priority Rules

Worker'ы выбираются по приоритету (из конфига):
```
desktop  priority=100
laptop   priority=50
vps      priority=10
```

Задача уходит worker'у с наибольшим приоритетом, у которого:
- `last_seen` < 60 секунд назад (онлайн)
- `active_tasks < max_parallel`
- `queued_tasks < max_queue`
- capabilities worker'а ⊇ required_capabilities задачи

---

## Capabilities

| Capability | Описание |
|---|---|
| `heavy_transcription` | Whisper large/medium, GPU или мощный CPU |
| `light_transcription` | Whisper small/base |
| `summarization` | LLM суммаризация (Ollama) |
| `pdf_ocr` | OCR сканированных PDF (Tesseract) |
| `article_extraction` | Извлечение текста из HTML (VPS) |
| `youtube_triage` | Проверка субтитров YouTube (VPS) |

---

## Endpoints

---

### POST /tasks

Создать новую задачу.

**Request body:**
```json
{
  "type": "youtube",
  "payload": {
    "url": "https://youtube.com/watch?v=xyz",
    "title": "Optional title"
  },
  "priority": 80,
  "source": "telegram"
}
```

**type values:**
- `youtube` — payload: `{url}`
- `article` — payload: `{url}`
- `twitter` — payload: `{url}`
- `reddit` — payload: `{url}`
- `arxiv` — payload: `{url}`
- `voice` — payload: `{file_path}` (путь в media_staging/inbox/)
- `video` — payload: `{file_path}`
- `pdf` — payload: `{file_path}`
- `word` — payload: `{file_path}` (.docx, .doc)
- `powerpoint` — payload: `{file_path}` (.pptx, .ppt)
- `excel` — payload: `{file_path}` (.xlsx, .xls)
- `odf` — payload: `{file_path}` (.odt, .ods, .odp)
- `rtf` — payload: `{file_path}` (.rtf)
- `epub` — payload: `{file_path}` (.epub)
- `text` — payload: `{text, title?}`

**source values:** `telegram`, `obsidian`, `cli`, `web`, `api`

**Response 201:**
```json
{
  "task_id": "20250801_153042_yt_a1b2c3",
  "status": "new"
}
```

**Response 422:** невалидный payload

---

### GET /tasks

Список задач.

**Query params:**
- `status` — фильтр по статусу (опционально)
- `limit` — количество (default: 50, max: 200)
- `offset` — пагинация (default: 0)

**Response 200:**
```json
{
  "tasks": [
    {
      "task_id": "...",
      "type": "youtube",
      "status": "new",
      "priority": 80,
      "created_at": "2025-08-01T15:30:42Z",
      "assigned_to": null,
      "attempts": 0,
      "source": "telegram"
    }
  ],
  "total": 42
}
```

---

### GET /tasks/{task_id}

Детали задачи.

**Response 200:**
```json
{
  "task_id": "20250801_153042_yt_a1b2c3",
  "type": "youtube",
  "status": "done",
  "payload": {"url": "https://youtube.com/watch?v=xyz"},
  "priority": 80,
  "source": "telegram",
  "created_at": "2025-08-01T15:30:42Z",
  "triaged_at": "2025-08-01T15:30:50Z",
  "assigned_to": "desktop",
  "assigned_at": "2025-08-01T15:31:00Z",
  "lease_until": "2025-08-01T15:41:00Z",
  "completed_at": "2025-08-01T15:41:30Z",
  "attempts": 1,
  "last_error": null,
  "result": {
    "title": "...",
    "summary": "...",
    "note_path": "1-Notes/20250801_153042_yt_a1b2c3.md"
  },
  "required_capabilities": ["heavy_transcription", "summarization"],
  "triage_data": {
    "subtitles_available": false,
    "duration_seconds": 3600,
    "language": "ru"
  }
}
```

**Response 404:** задача не найдена

---

### POST /tasks/claim

Worker запрашивает задачу из очереди.

Coordinator выбирает задачу со статусом `new` или `triage` (если triage завершён),
у которой required_capabilities ⊆ capabilities worker'а,
сортируя по priority DESC, created_at ASC.

**Request body:**
```json
{
  "worker_id": "desktop",
  "capabilities": ["heavy_transcription", "light_transcription", "summarization"]
}
```

**Response 200** — задача назначена:
```json
{
  "task_id": "...",
  "type": "voice",
  "payload": {
    "file_path": "media_staging/inbox/voice_a1b2c3.ogg"
  },
  "required_capabilities": ["heavy_transcription"],
  "triage_data": {},
  "lease_until": "2025-08-01T15:41:00Z"
}
```

**Response 204** — нет подходящих задач (тело пустое)

---

### POST /tasks/{task_id}/extend

Продлить lease на задачу (для долгих операций).

**Request body:**
```json
{
  "worker_id": "desktop",
  "lease_minutes": 10
}
```

**Response 200:**
```json
{
  "lease_until": "2025-08-01T15:51:00Z"
}
```

**Response 403:** worker_id не совпадает с assigned_to

**Response 404:** задача не найдена

---

### POST /tasks/{task_id}/done

Worker завершил задачу и передаёт результат.

Coordinator сам вызывает vault_writer для создания Markdown-заметки и git push.

**Request body:**
```json
{
  "worker_id": "desktop",
  "result": {
    "title": "Название заметки",
    "summary": {
      "brief": "Краткое описание в 2–3 предложениях",
      "ideas": ["Идея 1", "Идея 2"],
      "actions": ["[ ] Действие 1"]
    },
    "transcript": "Полный транскрипт...",
    "chapters": [
      {"time": "00:00", "title": "Введение"},
      {"time": "03:10", "title": "Основная часть"}
    ],
    "tags": ["ai", "architecture"],
    "metadata": {
      "method": "whisper:small",
      "language": "ru",
      "duration_seconds": 3600
    }
  }
}
```

Поля `transcript` и `chapters` — опциональны (для статей транскрипта нет).

**Response 200:**
```json
{
  "status": "done",
  "note_path": "1-Notes/20250801_153042_yt_a1b2c3.md"
}
```

**Response 403:** worker_id не совпадает с assigned_to

---

### POST /tasks/{task_id}/fail

Worker сообщает об ошибке.

**Request body:**
```json
{
  "worker_id": "desktop",
  "error": "Whisper OOM: cannot allocate 4GB"
}
```

**Response 200:**
```json
{
  "status": "new",
  "attempts": 2,
  "will_retry": true
}
```

Если `attempts >= max_attempts`:
```json
{
  "status": "failed",
  "attempts": 3,
  "will_retry": false
}
```

---

### POST /tasks/{task_id}/retry

Ручной retry из дашборда (сбросить счётчик попыток).

**Request body:** пустое `{}`

**Response 200:**
```json
{"status": "new", "attempts": 0}
```

---

### POST /workers/heartbeat

Worker регистрирует своё состояние. Вызывать каждые 25 секунд.

**Request body:**
```json
{
  "worker_id": "desktop",
  "cpu_load": 22.5,
  "ram_free_gb": 9.4,
  "disk_free_gb": 150.0,
  "active_tasks": 1,
  "queued_tasks": 0,
  "capabilities": ["heavy_transcription", "light_transcription", "summarization"],
  "on_battery": false,
  "max_parallel": 1,
  "max_queue": 5
}
```

**Response 200:**
```json
{"ok": true}
```

---

### GET /workers

Список worker'ов. Online = last_seen < 60 секунд назад.

**Response 200:**
```json
{
  "workers": [
    {
      "worker_id": "desktop",
      "online": true,
      "last_seen": "2025-08-01T15:40:55Z",
      "cpu_load": 22.5,
      "ram_free_gb": 9.4,
      "active_tasks": 1,
      "capabilities": ["heavy_transcription", "light_transcription", "summarization"]
    }
  ]
}
```

---

### GET /dashboard

HTML дашборд (Jinja2 + HTMX). Требует тот же X-API-Key в query param `?key=<secret>` или cookie.

Показывает:
- очередь задач
- статус worker'ов
- последние ошибки
- кнопки retry/cancel

---

## Error Responses

```json
{
  "detail": "Task not found"
}
```

HTTP коды: 400 (bad request), 401 (no/wrong API key), 403 (wrong worker), 404 (not found), 422 (validation), 500 (internal).
