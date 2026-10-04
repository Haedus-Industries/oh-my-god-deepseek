import { env } from "cloudflare:workers";
import { benchApi, type Bindings } from "../../../../lib/bench-api";
export const dynamic = "force-dynamic";
export function GET(request: Request) { return benchApi(request, env as unknown as Bindings); }
export function POST(request: Request) { return benchApi(request, env as unknown as Bindings); }
