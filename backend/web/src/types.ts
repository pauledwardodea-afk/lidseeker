// Mirrors backend/app/schemas.py

export type SearchResult = {
  type: "artist" | "album" | "track";
  foreignId: string;
  title: string;
  artist?: string | null;
  year?: number | null;
  albumType?: string | null;
  imageUrl?: string | null;
  inLibrary: boolean;
  requested: boolean;
  albumForeignId?: string | null;
  albumTitle?: string | null;
};

export type Pipeline = {
  stage: string;
  stageIndex: number;
  stages: string[];
  percent: number;
  trackFiles: number;
  trackCount: number;
  detail: string;
  failed: boolean;
  stuck: boolean;
};

export type MusicRequest = {
  id: number;
  type: string;
  foreignId: string;
  title: string;
  artist?: string | null;
  imageUrl?: string | null;
  status: string; // pending | downloading | available | failed | error
  createdAt: string;
  pipeline?: Pipeline | null;
  requestedBy?: string | null; // username; only sent to admins
};

export type Me = { username: string; role: string };

export type User = { id: number; username: string; role: string; createdAt: string };

export type Track = {
  position: number;
  title: string;
  durationMs?: number | null;
  mediumNumber: number;
};

export type DiscoverCategories = {
  genres: string[];
  decades: number[];
};

export type AppSettings = {
  quality?: string | null; // "mp3" | "flac"; null when the Soularr adapter is off
  ntfyTopic?: string | null;
  ntfyUrl?: string | null;
};

export type ServiceLink = { name: string; url: string };

export type ActionResult = { ok: boolean; message?: string | null };
