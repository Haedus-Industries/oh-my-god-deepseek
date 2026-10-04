/** Public reads and experiment-scoped writes. Large text stays in R2. */
export interface Bindings {
  DB: D1Database;
  BUCKET: R2Bucket;
  DSBENCH_UPLOAD_HASHES?: string; // JSON: experiment_id -> sha256(token), secret
  DSBENCH_SITE_VERSION?: string;
}
type Snapshot = { schema_version: number; experiment_id: string; version: number; source_at: string; [key: string]: unknown };
type Chunk = { schema_version: number; experiment_id: string; id: string; seq: number; attempt: string; slot: string; channel: string; stream: string; source_at: string; sha256: string; content: string; [key: string]: unknown };
const encoder = new TextEncoder();
const channels = new Set(["reasoning", "answer", "tool_call", "tool_result", "artifact", "verifier"]);
const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" };
const json = (value: unknown, status = 200) => Response.json(value, { status, headers });
export async function hash(text: string) {
  const value = await crypto.subtle.digest("SHA-256", encoder.encode(text));
  return Array.from(new Uint8Array(value), b => b.toString(16).padStart(2, "0")).join("");
}
function equal(a: string, b: string) {
  if (a.length !== b.length) return false;
  let difference = 0;
  for (let i = 0; i < a.length; i++) difference |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return difference === 0;
}
async function identity(request: Request, env: Bindings) {
  const match = /^Bearer (\S+)$/.exec(request.headers.get("authorization") ?? "");
  if (!match || !env.DSBENCH_UPLOAD_HASHES) return null;
  const provided = await hash(match[1]);
  const grants: Record<string, string> = JSON.parse(env.DSBENCH_UPLOAD_HASHES);
  return Object.entries(grants).find(([, expected]) => equal(expected, provided))?.[0] ?? null;
}
function key(experiment: string, id: string, hash: string) { return `${experiment}/${id}/${hash}`; }

export async function benchApi(request: Request, env: Bindings): Promise<Response> {
  const url = new URL(request.url);
  const route = url.pathname.replace(/^\/api\/v1\/?/, "");
  try {
    if (request.method === "POST") {
      const owner = await identity(request, env);
      if (!owner) return json({ error: "unauthorized" }, 401);
      const raw = await request.text();
      if (encoder.encode(raw).length > 262144) return json({ error: "batch_too_large" }, 413);
      const body = JSON.parse(raw);
      if (body.schema_version !== 1) return json({ error: "unsupported_schema" }, 400);
      if (route === "check") return json({ ok: true, schema_version: 1, experiment_id: owner });
      if (body.experiment_id !== owner) return json({ error: "experiment_scope" }, 403);
      if (route === "snapshot") return await snapshot(env, body as Snapshot, raw);
      if (route === "chunks") return await ingestChunks(env, owner, body.chunks);
      return json({ error: "not_found" }, 404);
    }
    if (request.method !== "GET") return json({ error: "method_not_allowed" }, 405);
    if (route === "health") {
      await env.DB.prepare("SELECT id FROM experiments LIMIT 1").all();
      // R2 head also verifies the platform binding without writing data.
      await env.BUCKET.head("health-binding-probe");
      return json({ ok: true, schema_version: 1, site_version: env.DSBENCH_SITE_VERSION ?? null });
    }
    if (route === "experiments") {
      const result = await env.DB.prepare("SELECT id,version,payload,updated_at FROM experiments ORDER BY updated_at DESC LIMIT 50").all<{ id: string; version: number; payload: string; updated_at: string }>();
      return json({ experiments: result.results.map(row => { const value = JSON.parse(row.payload); return { id: row.id, version: row.version, status: value.status, simulation: value.simulation, task: value.task, updated_at: row.updated_at }; }) });
    }
    const experiment = url.searchParams.get("experiment") ?? "";
    if (route === "snapshot") {
      const row = await env.DB.prepare("SELECT version,payload FROM experiments WHERE id=?").bind(experiment).first<{ version: number; payload: string }>();
      if (!row) return json({ error: "not_found" }, 404);
      const tail = await env.DB.prepare("SELECT COALESCE(MAX(rowid),0) AS seq FROM chunks WHERE experiment=?").bind(experiment).first<{ seq: number }>();
      return json({ snapshot: JSON.parse(row.payload), revision: `${row.version}:${tail?.seq ?? 0}`, site_version: env.DSBENCH_SITE_VERSION ?? null });
    }
    if (route === "events") {
      const cursor = Number(url.searchParams.get("cursor") ?? 0);
      if (!Number.isSafeInteger(cursor) || cursor < 0) return json({ error: "invalid_cursor" }, 400);
      const attempt = url.searchParams.get("attempt") ?? "";
      const channel = url.searchParams.get("channel") ?? "";
      const clauses = ["experiment=?", "rowid>?"];
      const values: (string | number)[] = [experiment, cursor];
      if(url.searchParams.has("until")) {
        const until=Number(url.searchParams.get("until"));
        if(!Number.isSafeInteger(until)||until<0)return json({error:"invalid_cursor"},400);
        clauses.push("rowid<=?");values.push(until);
      }
      if (attempt) { clauses.push("attempt=?"); values.push(attempt); }
      if (channel) { clauses.push("channel=?"); values.push(channel); }
      else clauses.push("channel<>'artifact'");
      const rows = await env.DB.prepare(`SELECT id,rowid AS arrival,metadata FROM chunks WHERE ${clauses.join(" AND ")} ORDER BY rowid LIMIT 40`).bind(...values).all<{ id: string; arrival: number; metadata: string }>();
      const records = [];
      // Read sequentially and return at most 512KiB in one response.
      let bytes = 0;
      for (const row of rows.results) {
        const object = await env.BUCKET.get(key(experiment, row.id, JSON.parse(row.metadata).sha256));
        if (!object) return json({ error: "attachment_unavailable" }, 503);
        if (bytes + object.size > 524288 && records.length) break;
        bytes += object.size;
        records.push({ ...JSON.parse(row.metadata), arrival_cursor: row.arrival, content: await object.text() });
      }
      return json({ records, next_cursor: records.at(-1)?.arrival_cursor ?? cursor, more: records.length < rows.results.length || rows.results.length === 40 });
    }
    if (route === "artifacts") {
      const rows = await env.DB.prepare("SELECT stream,MIN(seq) AS first_seq,MAX(seq) AS last_seq,SUM(bytes) AS bytes,MAX(attempt) AS attempt FROM chunks WHERE experiment=? AND channel='artifact' GROUP BY stream ORDER BY MAX(seq) DESC").bind(experiment).all();
      return json({ artifacts: rows.results });
    }
    if (route === "download") {
      const stream = url.searchParams.get("stream") ?? "";
      const channel = url.searchParams.get("channel") ?? "artifact";
      const attempt = url.searchParams.get("attempt") ?? "";
      if (!channels.has(channel)) return json({ error: "invalid_channel" }, 400);
      const query = stream ? "SELECT id,seq,hash FROM chunks WHERE experiment=? AND stream=? AND channel=? AND seq>? ORDER BY seq LIMIT 200" : "SELECT id,seq,hash FROM chunks WHERE experiment=? AND attempt=? AND channel=? AND seq>? ORDER BY seq LIMIT 200";
      // Keyset pagination bounds metadata as well as payload memory.
      let cursor = 0, finalPage = false;
      let rows: { id: string; seq: number; hash: string }[] = [];
      let body: ReadableStreamDefaultReader<Uint8Array> | undefined;
      const output = new ReadableStream<Uint8Array>({ async pull(controller) {
        try {
          while (true) {
            if (body) {
              const next = await body.read();
              if (!next.done) { controller.enqueue(next.value); return; }
              body = undefined;
            }
            if (!rows.length) {
              if (finalPage) { controller.close(); return; }
              const page = await env.DB.prepare(query).bind(experiment, stream || attempt, channel, cursor).all<{ id: string; seq: number; hash: string }>();
              rows = page.results;
              finalPage = rows.length < 200;
              if (!rows.length) { controller.close(); return; }
            }
            const row = rows.shift()!;
            const object = await env.BUCKET.get(key(experiment, row.id, row.hash));
            if (!object) throw new Error("attachment_missing");
            cursor = row.seq;
            body = object.body.getReader();
          }
        } catch { controller.error(new Error("download_unavailable")); }
      }, async cancel() { await body?.cancel(); } });
      const filename = (stream.split("/").at(-1) || `${attempt}-${channel}.txt`).replace(/[^a-zA-Z0-9._-]/g, "_");
      return new Response(output, { headers: { ...headers, "Content-Type": "text/plain; charset=utf-8", "Content-Disposition": `attachment; filename="${filename}"` } });
    }
    return json({ error: "not_found" }, 404);
  } catch (error) {
    console.error("benchmark_api", error instanceof Error ? error.name : "error");
    return json({ error: "storage_or_protocol_unavailable" }, 503);
  }
}

async function snapshot(env: Bindings, body: Snapshot, raw: string) {
  if (!Number.isSafeInteger(body.version) || body.version < 1 || typeof body.source_at !== "string") return json({ error: "invalid_snapshot" }, 400);
  const contentHash = await hash(raw);
  const existing = await env.DB.prepare("SELECT version,sha256 FROM experiments WHERE id=?").bind(body.experiment_id).first<{ version: number; sha256: string }>();
  if (existing?.version === body.version && existing.sha256 !== contentHash) return json({ error: "snapshot_conflict" }, 409);
  await env.DB.prepare("INSERT INTO experiments (id,version,payload,sha256,updated_at) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version,payload=excluded.payload,sha256=excluded.sha256,updated_at=excluded.updated_at WHERE excluded.version>experiments.version")
    .bind(body.experiment_id, body.version, raw, contentHash, body.source_at).run();
  const stored = await env.DB.prepare("SELECT version,sha256 FROM experiments WHERE id=?").bind(body.experiment_id).first<{version:number;sha256:string}>();
  if (stored?.version===body.version && stored.sha256!==contentHash) return json({error:"snapshot_conflict"},409);
  return json({ ok: true, version: body.version });
}

async function ingestChunks(env: Bindings, owner: string, chunks: Chunk[]) {
  if (!Array.isArray(chunks)) return json({ error: "invalid_chunks" }, 400);
  for (const chunk of chunks) {
    if (chunk.experiment_id !== owner || chunk.schema_version !== 1 || !channels.has(chunk.channel) || typeof chunk.content !== "string" || typeof chunk.stream !== "string" || typeof chunk.attempt !== "string" || !/^[a-f0-9]{64}$/.test(chunk.id) || !Number.isSafeInteger(chunk.seq) || chunk.seq < 1) return json({ error: "invalid_chunk" }, 400);
    const bytes = encoder.encode(chunk.content).length;
    if (bytes > 65536 || await hash(chunk.content) !== chunk.sha256) return json({ error: "content_hash_or_size" }, 400);
    const existing = await env.DB.prepare("SELECT id,hash,metadata FROM chunks WHERE experiment=? AND (id=? OR seq=?)").bind(owner, chunk.id, chunk.seq).first<{ id: string; hash: string; metadata: string }>();
    const metadata = { ...chunk } as Partial<Chunk>;
    delete metadata.content;
    const meta = JSON.stringify(metadata);
    if (existing) {
      if (existing.id !== chunk.id || existing.hash !== chunk.sha256 || existing.metadata !== meta) return json({ error: "chunk_conflict" }, 409);
      continue;
    }
    const storageKey = key(owner, chunk.id, chunk.sha256);
    // Write R2 first. A retry after D1 failure reuses the same verified content.
    await env.BUCKET.put(storageKey, chunk.content, { httpMetadata: { contentType: "text/plain; charset=utf-8" } });
    await env.DB.prepare("INSERT INTO chunks(id,experiment,seq,attempt,channel,stream,source_at,hash,bytes,metadata) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING")
      .bind(chunk.id, owner, chunk.seq, chunk.attempt, chunk.channel, chunk.stream, chunk.source_at, chunk.sha256, bytes, meta).run();
    const stored = await env.DB.prepare("SELECT id,hash,metadata FROM chunks WHERE experiment=? AND (id=? OR seq=?)").bind(owner,chunk.id,chunk.seq).first<{id:string;hash:string;metadata:string}>();
    if (stored?.id!==chunk.id || stored.hash!==chunk.sha256 || stored.metadata!==meta) return json({error:"chunk_conflict"},409);
  }
  return json({ ok: true, accepted: chunks.length });
}
