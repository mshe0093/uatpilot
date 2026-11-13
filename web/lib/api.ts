import type { StreamEvent } from "./types";

// 127.0.0.1 rather than localhost: browsers may resolve localhost to ::1 while uvicorn binds IPv4.
// In the Docker image the UI is served by the API itself, so NEXT_PUBLIC_API_URL is "".
export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";

export async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return (await res.json()) as T;
}

export async function putJSON<T>(path: string, body: unknown, token?: string): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${API_URL}${path}`, { method: "PUT", headers, body: JSON.stringify(body) });
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const json = await res.json();
      if (typeof json.detail === "string") detail = json.detail;
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

/**
 * POST a JSON body and stream Server-Sent Events back. EventSource only supports GET, so this
 * parses the `data:` frames from a fetch() body stream instead (same as scam-checker-agent).
 */
export async function streamEvents(
  path: string,
  body: unknown,
  onEvent: (ev: StreamEvent) => void,
  token?: string,
): Promise<void> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok || !res.body) {
    let detail = `Request failed (${res.status})`;
    try {
      const json = await res.json();
      if (typeof json.detail === "string") detail = json.detail;
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const data = frame
        .split("\n")
        .filter((l) => l.startsWith("data: "))
        .map((l) => l.slice(6))
        .join("\n");
      if (data) onEvent(JSON.parse(data) as StreamEvent);
    }
  }
}
