# Arquitectura Técnica de LegalBot (para desarrolladores mid-level)

Este documento asume que ya entiendes conceptos como REST APIs, bases de datos relacionales, colas de tareas y modelos de lenguaje (LLMs). Aquí profundizamos en **cómo se conectan las piezas**, las decisiones de diseño y los mecanismos que permiten debuggear, escalar y extender el sistema.

---

## Stack y dependencias clave

| Capa | Tecnología | ¿Por qué se eligió? |
|------|-----------|---------------------|
| API web | FastAPI + Uvicorn | Tipado automático con Pydantic, async nativo, OpenAPI gratis. |
| ORM / DB | SQLAlchemy 2.x (async) + asyncpg + PostgreSQL 15+ | Soporte robusto de `FOR UPDATE SKIP LOCKED` y JSONB. |
| Agentes | LangGraph + `deepagents` (wrapper propio) | Checkpointing nativo en Postgres, graphs composables, middleware interceptable. |
| LLM | `langchain.chat_models.init_chat_model` | Abstracción unificada: hoy Anthropic Claude, mañana cualquier proveedor. |
| Embeddings | OpenAI `text-embedding-3-small` (1536d) + pgvector | Indexación semántica de memoria a largo plazo en la misma base de datos. |
| Tareas background | Celery + Redis (broker, backend, redbeat) | Redbeat permite schedulers persistentes en Redis; Celery maneja el resto. |
| Blob storage | `BlobStore` (pluggable): `LocalBlobStore` o `S3BlobStore` | Local para dev/test; S3 para producción sin cambiar código de negocio. |
| Métricas | Prometheus (tiempo de procesamiento, conteos de estado) + OpenTelemetry opcional. | Observabilidad operativa sin vendor lock-in. |

Todo se configura mediante variables de entorno (ver `src/legalbot/core/config.py`). No hay archivos de config estáticos.

---

## Flujo de datos end-to-end (versión técnica)

```
PostgreSQL (datos de negocio + checkpoints LangGraph)
        ▲                                      │
        │    (1) poll_mailboxes (redbeat)      │
        │    (2) ingest_message (Celery task) │
        │         INSERT ingestion_item        │
        │         ON CONFLICT DO NOTHING       │
        │         → promote_to_ready(job)      │
        │                                      │
   Redis ◄────── (3) dispatch_ready_jobs ─────┘
   (broker +   SELECT ... FOR UPDATE SKIP LOCKED
    redbeat +   LIMIT = MAX_CONCURRENT_RUNS - SCARD(in_flight)
    budget)     UPDATE state = 'dispatched'
        │
        │    (4) run_pipeline.delay(job_id)
        │
        ▼
   Celery Worker
        │
        ├── Crea Session (kind=primary, thread_id=uuid)
        ├── Crea Run (kind=pipeline, trigger=ingestion)
        ├── Prepara AgentState:
        │      messages=[HumanMessage(content=system_prompt)]
        │      has_attachments=(len(attachments) > 0)
        │      job_id, email_id, session_id, run_id, user_id
        │
        └── Invoca compiled_agent.ainvoke(state, config)
                 │
                 ▼
          ┌──────────────────────────────────────────┐
          │  deepagents create_agent pipeline        │
          │  Middleware stack (orden estricto):      │
          │  1. SummarizationMiddleware              │
          │  2. TodoListMiddleware                   │
          │  3. FilesystemMiddleware                 │
          │  4. SubAgentMiddleware                   │
          │  5. LLMToolSelectorMiddleware (opt)        │
          │  6. MemoryMiddleware                     │
          │  7. InterruptCaptureMiddleware           │
          │  8. HumanInTheLoopMiddleware             │
          │                                          │
          │  SubAgentMiddleware enruta a:             │
          │  • extract / analyze / act / reflect       │
          │    (checkpoint_ns = etapa:run_id:intento)  │
          └──────────────────────────────────────────┘
                 │
                 ▼
          Output state con messages actualizados + stage_result
                 │
                 ▼
          Proyección a tablas:
          - session_message (assistant responses)
          - artifact (versionado)
          - draft (si se escribió uno)
          - interrupt_request (si hubo HITL)
                 │
                 ▼
          Session status: idle / awaiting_human / failed
```

### Puntos clave del flujo

- **Idempotencia de ingesta**: `ingestion_item` tiene un índice único `(source, external_id)`. Si el mismo correo llega dos veces, el segundo `ON CONFLICT DO NOTHING` no crea un nuevo job. El cursor del buzón avanza igual.
- **Transacción atómica**: `ingest_message` crea `IngestionItem`, `EmailMetadata`, `IngestionAttachment` y `ProcessingJob` en una sola transacción async. Si falla el attachment, nada queda guardado.
- **Budget de concurrencia**: No usamos semáforos en Python. Usamos un Redis set (`legalbot:in_flight_jobs`) y `SCARD`. Esto permite múltiples workers distribuidos sin race conditions.
- **Checkpointing en Postgres**: LangGraph serializa el `AgentState` completo (mensajes, archivos, índice de artifacts) en la tabla `checkpoint` + `checkpoint_blobs`. El `thread_id` es el `session.thread_id`; cada invocación ``task("extract",…)`` (y análogos) usa un `checkpoint_ns` distinto: `{etapa}:{run_id}:{uuid}` para no mezclar intentos fallidos con el siguiente.

---

## Modelo de base de datos (resumen relacional)

```
mailbox 1--* ingestion_item 1--1 processing_job 1--* session
                                     │
                                     ├── 1--* run
                                     │       ├── 1--* run_step
                                     │       └── 1--* scheduled_job
                                     │
                                     ├── 1--* artifact (versionado por key)
                                     ├── 1--* draft
                                     ├── 1--* interrupt_request
                                     └── 1--* session_message

email_metadata 1--1 ingestion_item (source-specific, polimórfico)
ingestion_attachment 1--* ingestion_item
```

### Diseño de claves y estados

- **`processing_job.state`**: `intake → ready → dispatched → processing → completed | failed | cancelled | archived`. Las transiciones están **guardadas**: `JobService.transition()` hace `UPDATE ... WHERE state = :from_state`, por lo que dos workers no pueden mover el mismo job a `processing` simultáneamente.
- **`session.kind`**: `primary` (única por job) o `replay` (múltiples, hijas de una primary).
- **`run.kind`**: `pipeline` (ejecución principal), `user_followup` (mensaje del usuario), `scheduled` (disparo de redbeat).
- **`run_step`**: Cada etapa del subagente se registra como un paso con `step_name='extract|analyze|act|reflect'`, `checkpoint_id` y opcionalmente `checkpoint_ns` (el namespace exacto del intento). Esto permite auditoría y replay.

---

## El agente: un grafo principal + 4 subgrafos

### Grafo principal (`build_agent`)

No es un solo `StateGraph` monolítico. Es un agente compilado por `deepagents.create_agent` que:

1. Recibe el `AgentState` (alias `LegalEmailState`).
2. Pasa por la pila de middleware (cada middleware puede transformar el request antes de llegar al LLM).
3. El `SubAgentMiddleware` detecta qué etapa debe ejecutarse y enruta al subgrafo correspondiente.
4. Los subgrafos comparten **el mismo** `AsyncPostgresSaver` (mismo pool de conexiones, mismas tablas de checkpoint).

### ¿Por qué subgrafos en lugar de un solo grafo?

- **Aislamiento de checkpoints**: Cada etapa usa un `checkpoint_ns` por intento (`extract:{run_id}:{uuid}`). Si `act` falla, un nuevo `task("act",…)` arranca stream limpio; el intento anterior queda auditable en Postgres.
- **Aislamiento de herramientas**: Cada etapa expone solo las tools que necesita. `extract` no ve `write_draft`; `act` no ve `analyze_image`.
- **Prompts distintos**: Cada etapa tiene su propio system prompt (`EXTRACT_PROMPT`, `ANALYZE_PROMPT`, `ACT_PROMPT`, `REFLECTION_PROMPT`).

### Patrón común de cada subgrafo

```python
def build_X_graph(checkpointer):
    builder = StateGraph(LegalEmailState, input=InputState, output=XOutputState)
    builder.add_node("X", X_node)       # LLM + tools bind
    builder.add_node("tools", ToolNode(tool_list))
    builder.add_node("finalize", finalize_node)  # build_stage_result()
    builder.add_edge(START, "X")
    builder.add_conditional_edges("X", should_continue, {"continue": "tools", "end": "finalize"})
    builder.add_edge("tools", "X")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)
```

La función `should_continue` mira si el último mensaje tiene `tool_calls`. Si sí, va al nodo `tools`; si no, va a `finalize`.

---

## Estado compartido: `LegalEmailState`

Es un `TypedDict` con campos anotados con **reducers** (funciones que determinan cómo se fusionan los valores cuando el grafo se reanuda o cuando varios nodas escriben al mismo campo).

| Campo | Reducer | Propósito |
|-------|---------|-----------|
| `messages` | `add_messages` | Conversación completa con el LLM (system, human, ai, tool). Se trunca por `SummarizationMiddleware`. |
| `plan` | `operator.add` | Lista de tareas abiertas. |
| `files` | dict merge | Sistema de archivos virtual en memoria (`FilesystemMiddleware`). |
| `artifact_index` | `artifact_index_reducer` | Mapa `key → ArtifactRef`. Cada nodo puede añadir/actualizar refs sin borrar las anteriores. |
| `has_attachments` | `replace` (último gana) | Booleano pre-computado para evitar queries a la DB en cada etapa. |
| `stage_result` | `replace` | Envelope `StageResult` que la etapa actual devuelve al orquestador. |
| `job_id`, `session_id`, `run_id`, `user_id`, `graph_thread_id`, `email_id` | `replace` | IDs de negocio para que las tools no tengan que inferir contexto. |

### `InputState` vs `OutputState`

Cada subgrafo declara un `input_schema` y un `output_schema`:
- `InputState` solo requiere `messages` y los IDs. Esto permite invocar un subgrafo en cualquier momento sin arrastrar todo el historial de otras etapas.
- `XOutputState` (ej. `ExtractOutputState`) normalmente solo define `stage_result` y quizás `messages`. Esto evita que un subgrafo devuelva datos irrelevantes al grafo principal.

---

## Middleware: orden y responsabilidades

La pila de middleware se ejecuta en cada llamada al modelo (cada "turno" del agente). El orden importa porque cada middleware ve el request modificado por el anterior.

| # | Middleware | ¿Qué hace técnicamente? |
|---|-----------|------------------------|
| 1 | **SummarizationMiddleware** | Si `messages` supera 60k tokens, resume los mensajes antiguos usando `SUMMARIZATION_MODEL` y los reemplaza por un `SystemMessage` de resumen. Mantiene los últimos 30 mensajes intactos. |
| 2 | **TodoListMiddleware** | Inyecta la lista de tareas pendientes (`state["plan"]`) en el system prompt para que el LLM sepa qué debe hacer. |
| 3 | **FilesystemMiddleware** | Expone un sistema de archivos virtual (`state["files"]`) que el agente puede leer/escribir con tools. Útil para pasar datos entre turns sin contaminar `messages`. |
| 4 | **SubAgentMiddleware** | Lee `state["stage_result"]` o el plan para decidir qué subgrafo invocar a continuación. Construye el `config` con `checkpoint_ns` correcto y llama `subagent.ainvoke()`. |
| 5 | **LLMToolSelectorMiddleware** (opt) | Si `BIGTOOL_ENABLED=True`, filtra las tools visibles al LLM a un "resident set" pequeño + tools previamente reveladas vía `retrieve_tools`. Reduce el consumo de tokens en el system prompt de tools. |
| 6 | **MemoryMiddleware** | Consulta `AsyncPostgresStore` (pgvector) por `user_id` para traer: (a) hechos del usuario (`users/{id}/facts`) y (b) episodios pasados similares (`agent_episodes`). Inyecta ambos como bloques de texto en el system prompt. |
| 7 | **InterruptCaptureMiddleware** | Intercepta mensajes con `__interrupt__` (emitidos por `ask_human` / `request_human_approval`). Crea un `InterruptRequest` en la base de datos y cambia `session.status = 'awaiting_human'`. NO detiene el grafo; eso lo hace `HumanInTheLoopMiddleware`. |
| 8 | **HumanInTheLoopMiddleware** | Detiene el grafo LangGraph nativamente antes de tools de alto riesgo (`write_draft`, `send_draft`, `schedule_followup`). Presenta al usuario una UI de aprobación con opciones `approve`, `edit`, `reject`. Si el usuario edita, la edición se aplica como `artifact_edit` antes de reanudar. |

### Diferencia crítica entre los dos HITL

- **`HumanInTheLoopMiddleware`**: Interrupt **síncrono** del grafo de LangGraph. El grafo se pausa literalmente; el worker de Celery espera (o mejor dicho, el grafo queda en estado `interrupt`). La resolución requiere `Command(resume=...)`.
- **`InterruptCaptureMiddleware`**: Interrupt **asíncrono** / "fire-and-persist". El grafo puede seguir corriendo, pero el middleware escribe en la DB para que la API lo muestre. Esto permite HITL sin bloquear el worker de Celery indefinidamente (útil para timeouts largos).

En la práctica, la mayoría de los flujos usan ambos: `HumanInTheLoopMiddleware` para acciones peligrosas (enviar correos) y `InterruptCaptureMiddleware` para preguntas de clarificación (`ask_human`).

---

## Artifacts: el contrato entre etapas

Los subgrafos no se pasan datos por `messages` (que es volátil y se trunca). Se pasan datos por **artifacts** versionados en la base de datos.

### Política de almacenamiento

```
si content es JSON AND len(content) <= ARTIFACT_INLINE_MAX_BYTES (32 KB):
    guardar en artifact.content_inline (JSONB)
sino:
    guardar en BlobStore -> obtener blob_ref
    guardar blob_ref + mime_type en artifact.blob_ref
```

Ventaja: queries de listado y metadatos no requieren ir al blob store. Solo la lectura del contenido sí.

### Versionado

```sql
-- Índice parcial único: solo una fila con is_latest = true por (session_id, key)
CREATE UNIQUE INDEX idx_artifact_latest ON artifact (session_id, key) WHERE is_latest = true;
```

Cada `ArtifactService.write()` inserta una nueva fila, flips `is_latest` de la anterior a `false`, y borra versiones antiguas si superan `ARTIFACT_VERSION_CAP` (default 20).

### Actualización con merge

`ArtifactService.update(key, patch, merge='json_merge_patch')` implementa RFC 7396:
- `null` en el patch elimina la clave.
- Arrays se reemplazan completamente (no se hace merge profundo de arrays).
- Objetos anidados se fusionan recursivamente.

Esto permite al agente decir "cambia solo el campo `status` a `approved`" sin re-escribir todo el artifact.

---

## Interrupciones: resolución técnica

### Estados de una interrupción

```
interrupt_request.status: pending → resolved | expired
session.status:           running → awaiting_human → running | failed
```

### Flujo de resolución (API + backend)

1. **Cliente** hace `GET /sessions/{id}/interrupt` → devuelve el payload + schema JSON de la interrupción pendiente.
2. **Cliente** hace `POST /sessions/{id}/interrupt` con cuerpo discriminado:
   - `tool_approval`: `{decision: "approve" | "reject", edited_args?: {...}}`
   - `information_request`: `{answers: {...}}`
   - `review_draft`: `{decision: "approve" | "edit" | "reject", artifact_edits?: [{key, patch, merge}]} `
3. **`InterruptService.resolve()`**:
   - Valida el payload contra el JSON Schema almacenado en `interrupt_request.payload_schema`.
   - Si hay `artifact_edits`, aplica cada uno vía `ArtifactService.update()` dentro de la misma transacción SQL.
   - Crea una fila `user_intervention` (auditoría).
   - Cambia `session.status = 'running'`.
   - Si la interrupción estaba ligada a un `run_id`, encola `resume_run.delay(run_id, resume_value)`.
4. **Worker** `resume_run` ejecuta `graph.ainvoke(..., Command(resume=resume_value))`. LangGraph reanuda exactamente en el nodo donde se detuvo.

### TTL y limpieza

`expire_stalled_interrupts` (redbeat) marca como `expired` las interrupciones pendientes más viejas que `INTERRUPT_TTL_SEC` (default 72h). Si expiran, el `ProcessingJob` se marca como `failed` para que un operador humano lo revise.

---

## Concurrencia y tolerancia a fallos

### Dispatcher (`dispatch_ready_jobs`)

```python
# Pseudocódigo de dispatch_once
async def dispatch_once(db, budget: ConcurrencyBudget):
    limit = await budget.current()  # MAX_CONCURRENT_RUNS - redis.scard(IN_FLIGHT_KEY)
    jobs = await pick_ready_jobs(db, limit=limit)
    # Dentro de la misma transacción:
    for job in jobs:
        await JobService.transition(job.id, from_state='ready', to_state='dispatched')
    await db.commit()
    for job_id in dispatched:
        await budget.acquire(job_id)
        run_pipeline.delay(job_id)
```

- **`FOR UPDATE SKIP LOCKED`**: Si hay múltiples workers corriendo `dispatch_ready_jobs` al mismo tiempo, cada uno "salta" los jobs que otro worker ya está procesando. No hay deadlocks ni duplicados.
- **Budget Redis set**: `legalbot:in_flight_jobs` es un `Redis Set`. `SCARD` es O(1). Cuando `run_pipeline` termina (éxito o fallo), el worker hace `budget.release(job_id)`.

### Sweeper (`reap_stuck_jobs`)

```python
# Corre cada 5 minutos
# 1) Revierte 'dispatched' con lease expirado (> DISPATCH_LEASE_SEC) -> 'ready'
# 2) Falla 'processing' con lease expirado (> PROCESSING_LEASE_SEC) -> 'failed'
```

Esto evita que un worker muerto deje jobs bloqueados para siempre.

---

## Scheduling: redbeat y reconciliación

LegalBot usa **redbeat** (scheduler de Celery basado en Redis) para tareas periódicas, pero la fuente de verdad es la tabla `scheduled_job` en Postgres.

### Por qué dos fuentes

- **Postgres**: Durable, auditable, consultable por la API. La UI lista los jobs desde aquí.
- **Redis + redbeat**: El scheduler activo que dispara realmente las tareas. Puede perderse si Redis se reinicia.

### Reconciliación

`rebuild_redbeat_from_db` (opcional, ejecutar al inicio):
1. Lee todas las filas `scheduled_job` con estado `active` o `paused`.
2. Re-registra cada una en redbeat.
3. Elimina entradas redbeat huérfanas (que no tienen fila en Postgres).

### Tipos de schedule

- **One-shot**: Fires once en `run_at`. Tras ejecutarse, ` SchedulingService` marca estado `completed` y elimina la entrada redbeat.
- **Recurring**: Especificado con `ScheduleSpec` (cron o interval). Corre indefinidamente hasta `pause` o `cancel`.
- **Cascade cancel**: Al cancelar un `Run` o un `Session`, `SchedulingService.cascade_cancel(run_id=...)` marca todas las filas ligadas como `cancelled` y remueve las entradas redbeat.

---

## Replay: branching de checkpoints

Replay es una de las características más potentes del sistema. Técnicamente es un **branch** en el historial de checkpoints de LangGraph.

### Secuencia exacta

1. **API** recibe `POST /sessions/{parent_id}/replay` con `from_run_id` + `from_step`.
2. **`ReplayService.replay()`**:
   - Valida que `from_step.checkpoint_id` existe en `checkpoint` (Postgres).
   - Crea `Session(kind='replay', parent_session_id=parent_id, branch_from_checkpoint_id=checkpoint_id)`.
   - Genera un **nuevo** `thread_id` para la sesión hija.
   - Llama `graph.aupdate_state(config={"configurable": {"thread_id": new_thread_id, "checkpoint_ns": from_step.step_name}}, values=state_snapshot, checkpoint_id=from_step.checkpoint_id)`.
   - Esto "clona" el estado del checkpoint en un nuevo hilo de ejecución.
   - Cancela en cascada los `scheduled_job` de la sesión padre.
   - Crea un nuevo `Run(kind='pipeline', trigger='replay')`.
   - Encola `resume_run.delay(new_run_id)`.
3. **Worker** ejecuta el grafo desde el checkpoint clonado. El agente re-ejecuta la etapa deseada.
4. **Artifacts**: las nuevas versiones escritas durante el replay mueven `is_latest` hacia adelante. Las versiones anteriores siguen existiendo; simplemente ya no son `latest`.

### Analogía con Git

- `thread_id` ≈ rama Git.
- `checkpoint_id` ≈ commit hash.
- Replay ≈ `git checkout -b nueva-rama commit-antiguo`.
- La sesión padre sigue intacta; la hija puede divergir.

---

## Cómo extender el sistema (patrones técnicos)

### 1. Agregar un nuevo subagente (etapa)

Ejemplo: etapa de `validate` antes de enviar.

```python
# src/legalbot/agents/validate_graph.py
from langgraph.graph import StateGraph, START, END
from legalbot.agents.state import LegalEmailState, ValidateOutputState

def build_validate_graph(checkpointer=None):
    builder = StateGraph(LegalEmailState, output=ValidateOutputState)
    builder.add_node("validate", validate_node)
    builder.add_node("tools", ToolNode([read_artifact, list_artifacts]))
    builder.add_node("finalize", finalize_node)
    builder.add_edge(START, "validate")
    builder.add_conditional_edges("validate", should_continue, {"continue": "tools", "end": "finalize"})
    builder.add_edge("tools", "validate")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)
```

Luego en `subagents.py`:
```python
VALIDATE = CompiledSubAgent(
    name="validate",
    build_graph=build_validate_graph,
    checkpoint_ns="validate",
)
```

Y en el grafo principal (o en `SubAgentMiddleware`), añades la transición `act → validate → reflect`.

### 2. Agregar una tool nativa

```python
# src/legalbot/agents/tools.py
from langchain_core.tools import tool

@tool
def search_case_law(query: str, jurisdiction: str) -> str:
    """Busca jurisprudencia en una base externa."""
    # implementación
    return "Resultado..."
```

En `graph.py`, agrégala a `native_tools`:
```python
native_tools = [
    ...,
    search_case_law,
]
```

Si requiere aprobación humana, añádela a la lista del `HumanInTheLoopMiddleware`.

### 3. Agregar un nuevo `source` de ingesta

La tabla `ingestion_item` ya tiene `source` (string). Solo necesitas:

1. Un mapper: función que convierta el payload del nuevo source a `MappedItem` (igual que `map_email`).
2. Un worker: Celery task que escuche el source y llame a `IngestionItemService.upsert(mapped=...)`. El resto del pipeline es idéntico.

### 4. Agregar un nuevo tipo de scheduled job

En `scheduling/service.py`:

```python
JOB_KIND_REGISTRY = {
    "reminder": handle_reminder,
    "replay_trigger": handle_replay_trigger,
    "my_new_kind": handle_my_new_kind,
}
```

`handle_my_new_kind(payload, session, db)` recibe el payload del schedule y la sesión asociada. Puede crear mensajes, artifacts o incluso encolar un `continue_session`.

---

## Diccionario técnico rápido

| Término | Definición precisa |
|---------|-------------------|
| **Checkpoint** | Snapshot serializado del `AgentState` en un momento dado de la ejecución del grafo. Identificado por `(thread_id, checkpoint_ns, checkpoint_id)`. |
| **Checkpoint namespace (`checkpoint_ns`)** | Segmenta checkpoints dentro de un mismo `thread_id`. Cada llamada ``task(etapa,…)`` usa `{etapa}:{run_id}:{uuid}` para aislar intentos. |
| **Thread ID (`thread_id`)** | Identificador de la conversación/sesión para LangGraph. En LegalBot, equivale al `session.id`. |
| **Reducer** | Función que determina cómo se combinan los valores de un campo del state cuando múltiples nodos escriben sobre él. |
| **ArtifactRef** | Pydantic model que apunta a un artifact específico. Se guarda en `state["artifact_index"]` para que tools lo encuentren sin hacer queries ad hoc. |
| **Interrupt envelope** | Mensaje especial (`__interrupt__`) que LangGraph usa para pausar la ejecución y esperar input externo. |
| **Command(resume=...)** | Objeto de LangGraph que se pasa a `ainvoke` para reanudar un grafo interrumpido. |
| **Redbeat** | Scheduler persistente de Celery que guarda las tareas periódicas en Redis en lugar de solo en memoria. |
| **ON CONFLICT DO NOTHING** | Patrón SQL idempotente: si la fila ya existe (por índice único), la operación no falla ni duplica. |

---

## Mapa de lectura recomendado

Si necesitas debuggear o modificar algo específico:

| Síntoma / Tarea | Archivo(s) clave |
|-----------------|------------------|
| "El agente no reanuda después de una interrupción" | `interrupts/service.py` (resolución) + `workers/runs.py` (`resume_run`) |
| "Quiero cambiar el prompt del agente" | `agents/prompts.py` |
| "Necesito añadir una tool" | `agents/tools.py` + `agents/graph.py` (registro) |
| "El job se quedó stuck en dispatched" | `jobs/sweeper.py` + `jobs/dispatcher.py` |
| "El replay no funciona" | `services/replay.py` + `workers/runs.py` |
| "Los artifacts crecen sin control" | `artifacts/service.py` (version cap) |
| "Quiero entender la DB" | `db/models.py` + `db/types.py` (enums) |
| "Cómo se configura todo" | `core/config.py` |
| "Cómo se arma el grafo" | `agents/graph.py` + `agents/subagents.py` |
| "Cómo llega un correo" | `workers/ingest.py` + `ingestion/items.py` |
