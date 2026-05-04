# Arquitectura de LegalBot (Explicación para desarrolladores junior)

## Qué hace este sistema (en una frase)

LegalBot es un asistente automatizado que **lee correos electrónicos, entiende qué piden, redacta borradores de respuesta y los envía sólo cuando un humano aprueba lo necesario**. Todo queda guardado en una base de datos para que puedas auditarlo después.

---

## El flujo general (modo historia)

Imagina que eres el dueño de un bufete de abogados. Cada mañana llegan decenas de correos:

- Un cliente pide una revisión urgente de un contrato (adjunta un PDF).
- Un juez confirma una fecha de audiencia.
- Un estudiante pide información general.

LegalBot hace lo siguiente **automáticamente**:

1. **Revisa los buzones** cada cierto tiempo (como un correo de Outlook o Gmail).
2. **Baja los mensajes nuevos** y los guarda en la base de datos.
3. **Crea un "trabajo"** (`processing_job`) para cada correo nuevo.
4. **Espera su turno**: no procesa todo a la vez; hay un límite de trabajos simultáneos.
5. **Piensa en 4 etapas**:
   - **Extraer** datos del correo y los adjuntos.
   - **Analizar** qué se necesita hacer y con qué urgencia.
   - **Actuar**: redactar una respuesta, agendar un seguimiento, etc.
   - **Reflexionar**: guardar en memoria lo aprendido para la próxima vez.
6. **Si no está seguro**, le pide ayuda a un humano por medio de la API.
7. **Guarda todo**: correos, borradores, decisiones, errores. Si algo falla, puedes **reintentar** o **reproducir** desde un punto anterior.

---

## Las 4 etapas del "cerebro" (agente)

El sistema usa un **agente de inteligencia artificial** que trabaja por etapas. No es una sola llamada a ChatGPT; es un pipeline controlado.

| Etapa | ¿Qué hace? | Ejemplo concreto |
|-------|---------------|-----------------|
| **Extraer** | Lee el correo, abre adjuntos (PDF, imágenes, Excel) y saca datos estructurados. | "Este correo viene de Juan Pérez, pide revisar un contrato de arrendamiento. El PDF tiene 12 páginas y menciona una cláusula de rescisión." |
| **Analizar** | Clasifica la intención, detecta urgencia y riesgo legal. | "Es una revisión de contrato. Riesgo: medio. Urgencia: alta (deadline en 3 días). Necesita respuesta formal." |
| **Actuar** | Redacta el borrador, agenda seguimientos o decide que no hace falta nada. | "Redactar correo de respuesta al cliente confirmando recepción y plazo de 48 h para entregar opinión." |
| **Reflexionar** | Resume la sesión y guarda lecciones para futuros correos similares. | "La próxima vez que Juan Pérez envíe un contrato de arrendamiento, recordar que prefiere comentarios en Word." |

Cada etapa es **independiente**. Si la etapa de "Actuar" falla, puedes volver a correr solo esa etapa sin perder lo que ya extrajiste y analizaste.

---

## Cómo se guarda la información (el pizarrón compartido)

Las etapas no se pasan la información gritando; la dejan escrita en un **pizarrón** que todos leen.

En el sistema ese pizarrón se llama **artifact** (artefacto). Es como un archivo versionado:

- La etapa de extracción escribe: `analysis/extracted`
- La etapa de análisis lee `analysis/extracted` y escribe: `analysis/summary`
- La etapa de actuar lee `analysis/summary` y escribe: `drafts/reply`

Cada vez que se sobreescribe, se crea una **nueva versión**. Nunca pierdes lo anterior. Esto permite reproducir ("replay") una etapa desde un punto anterior sin borrar el trabajo previo.

### Ejemplo visual

```
Correo de entrada
        |
        v
+-------------------+    +-------------------+    +-------------------+    +-------------------+
|   Extraer         | -> |   Analizar        | -> |   Actuar          | -> |   Reflexionar     |
| analysis/extracted|    | analysis/summary |    | drafts/reply      |    | (memoria largo    |
| v1                |    | v1                 |    | v1                |    |  plazo)           |
+-------------------+    +-------------------+    +-------------------+    +-------------------+
        |                         |                         |
        |   (si falla Actuar,     |                         |
        |    puedes repetir        |                         |
        |    solo esa etapa)      v                         v
        |                 +-------------------+    +-------------------+
        |                 | drafts/reply v2   |    | (no afecta lo     |
        |                 | (corregido)       |    |  demás)          |
        |                 +-------------------+    +-------------------+
```

---

## ¿Y si el agente no sabe qué hacer? (Human-in-the-loop)

El sistema no decide solo cuando hay riesgo. Hay dos mecanismos para pedir ayuda humana:

1. **El agente pregunta explícitamente**: usa una herramienta llamada `ask_human`.
   - *Ejemplo*: "Necesito saber si este contrato es para el cliente A o el cliente B. ¿Me lo confirmas?"

2. **Se detiene antes de acciones peligrosas**: antes de `send_draft` (enviar un borrador) o `schedule_followup` (agendar algo), se crea automáticamente una **interrupción**.
   - *Ejemplo*: "Redacté este borrador de respuesta. ¿Lo apruebas, lo editas o lo rechazas?"

Cuando hay una interrupción:
- La sesión cambia a estado `awaiting_human` (esperando humano).
- Aparece un registro en la tabla `interrupt_request`.
- El humano responde por la API (`POST /sessions/{id}/interrupt`).
- El sistema reanuda el trabajo exactamente donde se quedó.

---

## Reproducir (Replay): la función "ctrl+z inteligente"

Imagina que la etapa de "Actuar" redactó un borrador con un error grave. No quieres volver a correr todo desde cero (extraer y analizar ya estaban bien).

El **replay** te permite:
1. Crear una sesión hija (`kind=replay`).
2. Copiar el estado de una etapa anterior (por ejemplo, después de "Analizar").
3. Ejecutar solo "Actuar" de nuevo, esta vez con una instrucción extra del humano.

Es como hacer un **checkout de una rama en Git** a partir de un commit anterior, sin perder la rama original.

---

## Cómo funciona la concurrencia (no colapsar el servidor)

El sistema no procesa todos los correos a la vez. Hay un "guardia" (`ConcurrencyBudget`) que:

- Mira cuántos trabajos están corriendo en Redis.
- Si ya hay 10 (`MAX_CONCURRENT_RUNS`), deja los demás en cola.
- Revisa cada 10 segundos (`DISPATCH_INTERVAL_SEC`) si hay cupo.

Además, hay un **limpiador** (`reap_stuck_jobs`) que corre cada 5 minutos. Si un trabajo se quedó "colgado" por más de 30 minutos, lo marca como fallido para que no bloquee el sistema.

---

## Ejemplo completo de flujo (de punta a punta)

**Contexto**: María es abogada. Su buzon `maria@bufete.com` está conectado a LegalBot.

### Paso 1: Llega un correo

> De: `cliente@empresa.com`
> Asunto: "Urgente: revisión contrato de confidencialidad"
> Adjunto: `NDA_v3.pdf`

### Paso 2: Ingesta

LegalBot (tarea `poll_mailboxes`) lo detecta, baja el correo y el PDF, y crea:
- `IngestionItem` (el correo en sí).
- `EmailMetadata` (de, para, asunto, etc.).
- `IngestionAttachment` (el PDF).
- `ProcessingJob` en estado `ready` (listo para procesar).

### Paso 3: Espera su turno

`dispatch_ready_jobs` revisa si hay cupo. Hay cupo. El job pasa a `dispatched` y luego a `processing`.

### Paso 4: Se crea la sesión

El sistema crea una `Session` (sesión) y un `Run` (ejecución) para poder seguirle la pista.

### Paso 5: Etapa Extraer

El agente:
- Lee el correo.
- Abre el PDF y saca texto.
- Escribe el artifact `analysis/extracted`:
  ```json
  {
    "remitente": "cliente@empresa.com",
    "tipo": "revisión de contrato",
    "adjunto": "NDA_v3.pdf",
    "páginas": 5,
    "cláusulas_clave": ["confidencialidad", "penalización por divulgación"]
  }
  ```

### Paso 6: Etapa Analizar

El agente lee `analysis/extracted` y escribe `analysis/summary`:
  ```json
  {
    "intención": "revisión_legal",
    "urgencia": "alta",
    "riesgo": "medio",
    "acción_recomendada": "redactar_opinión_en_48h"
  }
  ```

### Paso 7: Etapa Actuar

El agente redacta una respuesta y escribe el artifact `drafts/reply`:
  ```
  Estimado cliente:

  Confirmamos recepción del contrato de confidencialidad.
  Le haremos llegar nuestra opinión en un plazo máximo de 48 horas.

  Atentamente,
  Bufete de María
  ```

Antes de enviar, el sistema se **interrumpe** y espera la aprobación de María.

### Paso 8: Aprobación humana

María abre la app, ve el borrador, le cambia una frase, y aprueba.

- La interrupción se marca como `resolved`.
- El sistema reanuda (`resume_run`) y envía el correo.

### Paso 9: Reflexionar

El agente guarda en memoria:
  ```
  "cliente@empresa.com suele enviar NDAs de 5 páginas.
  Próxima vez: proponer revisión rápida de 24h si es similar."
  ```

### Paso 10: Cierre

El `ProcessingJob` pasa a `completed`. La `Session` queda en `idle` (lista por si llega un mensaje de seguimiento).

---

## Cómo se puede expandir este sistema

### 1. Agregar más fuentes de entrada (no solo correo)

Hoy el sistema lee buzones de email (Gmail, Outlook). Pero la tabla `IngestionItem` tiene una columna `source` que acepta:
- `email` (ya implementado)
- `slack` (reservado)
- `webhook` (reservado)
- `upload` (reservado)

Para agregar Slack, necesitarías:
- Un worker que escuche mensajes de un canal de Slack.
- Mapear el mensaje a `MappedItem` (igual que hace `map_email` hoy).
- El resto del pipeline es **exactamente igual**.

### 2. Agregar más etapas al agente

Hoy hay 4 etapas: extraer, analizar, actuar, reflexionar.

Podrías agregar una quinta etapa, por ejemplo:
- **"Validar"**: un abogado senior revisa la opinión del agente antes de enviarla al cliente.

Solo necesitarías:
- Crear `validate_graph.py` (igual que `act_graph.py`).
- Registrarlo en `subagents.py`.
- Definir su `checkpoint_ns` (por ejemplo, `validation`).

### 3. Agregar más herramientas (tools)

Hoy el agente puede leer correos, extraer PDFs, redactar borradores, etc.

Podrías agregar herramientas como:
- `search_case_law`: buscar jurisprudencia en una base de datos externa.
- `query_billing_system`: verificar si el cliente tiene facturas pendientes antes de responder.

Solo necesitarías:
- Implementar la función Python con el decorador `@tool`.
- Agregarla a la lista `native_tools` en `graph.py`.
- (Opcional) Agregarla al middleware de interrupciones si requiere aprobación humana.

### 4. Cambiar el modelo de lenguaje

Todo se configura en variables de entorno:
- `AGENT_MODEL`: el modelo principal (hoy `anthropic:claude-sonnet-4-5`).
- `VISION_MODEL`: para analizar imágenes (hoy `anthropic:claude-opus-4-6`).
- `SUMMARIZATION_MODEL`: para resumir cuando el historial es muy largo.

Si mañana quieres usar GPT-5 o un modelo local (Ollama), solo cambias la variable. El código usa `init_chat_model`, que es genérico.

### 5. Más usuarios (multi-tenancy)

Cada fila del sistema tiene `owner_user_id`. Hoy asume un solo usuario por instancia, pero la base de datos ya está preparada para:
- Que María vea solo sus correos.
- Que Pedro vea solo los suyos.

Solo faltaría agregar autenticación (OAuth2/JWT) en la capa de API.

---

## Diccionario rápido de términos

| Término | ¿Qué es (en plano)? |
|-----------|--------------------------|
| **IngestionItem** | Un correo (o mensaje) que entró al sistema. |
| **ProcessingJob** | La orden de procesar un correo. Tiene estados: listo -> despachado -> procesando -> terminado/fallado. |
| **Session** | La conversación completa con el agente sobre un correo. |
| **Run** | Una ejecución puntual del agente dentro de una sesión. |
| **RunStep** | Un paso dentro de una ejecución (por ejemplo, "etapa de análisis"). |
| **Artifact** | Un documento versionado que escribe el agente (extracción, análisis, borrador...). |
| **Interrupt** | Una pausa donde el agente pide ayuda o aprobación humana. |
| **Redbeat** | El programador de tareas periódicas dentro de Celery (como un cron, pero en Redis). |
| **Celery** | La cola de tareas que ejecuta cosas en segundo plano. |
| **Checkpoint** | Un punto de guardado del agente. Permite reanudar o reproducir desde ahí. |
| **Replay** | Volver a correr una etapa desde un punto anterior sin perder el trabajo previo. |

---

## ¿Dónde está el código que hace cada cosa?

| ¿Qué quieres entender? | ¿Dónde buscar? |
|--------------------------|-------------------|
| Cómo llegan los correos | `src/legalbot/workers/ingest.py` |
| Cómo se despachan los trabajos | `src/legalbot/workers/janitors.py` + `src/legalbot/jobs/dispatcher.py` |
| Cómo se construye el agente | `src/legalbot/agents/graph.py` |
| Las 4 etapas del agente | `src/legalbot/agents/extract_graph.py`, `analyze_graph.py`, `act_graph.py`, `reflect_graph.py` |
| Cómo se pasan datos entre etapas | `src/legalbot/agents/state.py` + `src/legalbot/agents/stage_result.py` |
| Cómo se guardan los artifacts | `src/legalbot/artifacts/service.py` |
| Cómo funciona HITL (interrupciones) | `src/legalbot/interrupts/service.py` |
| Cómo se hace replay | `src/legalbot/services/replay.py` |
| La API REST | `src/legalbot/api/` |
| Los modelos de base de datos | `src/legalbot/db/models.py` |
| La configuración | `src/legalbot/core/config.py` |

---

## Consejo final

Si te sientes abrumado, empieza por aquí:

1. Lee `src/legalbot/db/models.py` para entender las "tablas" del sistema.
2. Lee `src/legalbot/workers/ingest.py` para ver cómo entra un correo.
3. Lee `src/legalbot/agents/graph.py` para ver cómo se arma el agente.
4. Usa la API (`docs/getting-started.md`) para crear un buzon y ver cómo fluye un correo de prueba.

El resto es refinamiento de estas mismas piezas.
