/**
 * Server-side proxy to the FastAPI backend.
 * The browser only ever talks to this Next.js app, so the backend URL and
 * API key stay on the server and no CORS setup is needed.
 */
import type { NextRequest } from "next/server";

const API_URL = (process.env.API_URL || "http://localhost:8000").replace(/\/$/, "");
const API_KEY = process.env.API_KEY || "";
const PASS_HEADERS = ["content-type", "content-disposition", "cache-control", "content-length"];

export const dynamic = "force-dynamic";

async function proxy(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const url = `${API_URL}/api/${path.map(encodeURIComponent).join("/")}${req.nextUrl.search}`;
  const headers = new Headers();
  const ct = req.headers.get("content-type");
  if (ct) headers.set("content-type", ct);
  if (API_KEY) headers.set("x-api-key", API_KEY);
  const forwarded = req.headers.get("x-forwarded-for");
  if (forwarded) headers.set("x-forwarded-for", forwarded);

  const hasBody = !["GET", "HEAD"].includes(req.method);
  let upstream: Response;
  try {
    upstream = await fetch(url, {
      method: req.method,
      headers,
      body: hasBody ? req.body : undefined,
      // @ts-expect-error duplex is required by Node fetch when streaming a request body (uploads)
      duplex: hasBody ? "half" : undefined,
      cache: "no-store",
    });
  } catch {
    return Response.json({ detail: "The DocIntel API is not reachable. Is the backend running?" }, { status: 502 });
  }
  const out = new Headers();
  for (const h of PASS_HEADERS) {
    const v = upstream.headers.get(h);
    if (v) out.set(h, v);
  }
  return new Response(upstream.body, { status: upstream.status, headers: out });
}

export { proxy as GET, proxy as POST, proxy as PATCH, proxy as DELETE };
