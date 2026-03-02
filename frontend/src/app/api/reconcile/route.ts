import { NextRequest, NextResponse } from "next/server";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";

export async function POST(req: NextRequest) {
  const body = await req.text();
  const requestId = req.headers.get("X-Request-ID") ?? crypto.randomUUID();

  let upstream: Response;
  try {
    upstream = await fetch(`${API_BASE}/reconcile`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Request-ID": requestId,
      },
      body,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Backend unreachable";
    return NextResponse.json(
      { error: `Failed to reach backend: ${message}` },
      { status: 502, headers: { "X-Request-ID": requestId } },
    );
  }

  const data = await upstream.json();
  return NextResponse.json(data, {
    status: upstream.status,
    headers: { "X-Request-ID": requestId },
  });
}
