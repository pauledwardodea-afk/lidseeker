import type {
  ActionResult,
  AppSettings,
  DiscoverCategories,
  MusicRequest,
  SearchResult,
  ServiceLink,
  Track,
} from "./types";

const TOKEN_KEY = "lidseeker_token";

export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const setToken = (t: string) => localStorage.setItem(TOKEN_KEY, t);
export const clearToken = () => localStorage.removeItem(TOKEN_KEY);

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function req<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(opts.headers as Record<string, string>),
  };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch("/api" + path, { ...opts, headers });

  // Session expired / invalid — drop the token and bounce to login,
  // except on the login call itself (a 401 there is just bad credentials).
  if (res.status === 401 && path !== "/auth/login") {
    clearToken();
    if (location.pathname !== "/login") location.assign("/login");
    throw new ApiError(401, "Session expired");
  }

  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (body?.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const qs = (params: Record<string, string | number | undefined | null>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
};

export const api = {
  login: (username: string, password: string) =>
    req<{ token: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),

  search: (term: string, type: "album" | "artist" | "track") =>
    req<SearchResult[]>(`/search${qs({ term, type })}`),

  artistAlbums: (foreignId: string) =>
    req<SearchResult[]>(`/artist/${encodeURIComponent(foreignId)}/albums`),

  albumTracks: (foreignId: string) =>
    req<Track[]>(`/album/${encodeURIComponent(foreignId)}/tracks`),

  request: (body: {
    type: "artist" | "album" | "track";
    foreignId: string;
    albumForeignId?: string;
    mode?: "album" | "track";
  }) => req<MusicRequest>("/request", { method: "POST", body: JSON.stringify(body) }),

  requests: () => req<MusicRequest[]>("/requests"),

  deleteRequest: (id: number) =>
    req<ActionResult>(`/requests/${id}`, { method: "DELETE" }),

  retry: (id: number) =>
    req<ActionResult>(`/requests/${id}/retry`, { method: "POST" }),

  discover: (genre?: string | null, decade?: number | null) =>
    req<SearchResult[]>(`/discover${qs({ genre, decade })}`),

  discoverCategories: (genre?: string | null, decade?: number | null) =>
    req<DiscoverCategories>(`/discover/categories${qs({ genre, decade })}`),

  settings: () => req<AppSettings>("/settings"),

  setQuality: (quality: "mp3" | "flac") =>
    req<ActionResult>("/settings", {
      method: "PUT",
      body: JSON.stringify({ quality }),
    }),

  services: () => req<ServiceLink[]>("/services"),

  searchNow: () => req<ActionResult>("/search-now", { method: "POST" }),
};
