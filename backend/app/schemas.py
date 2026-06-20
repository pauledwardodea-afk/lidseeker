"""Pydantic models for the lidseeker API."""
from typing import Literal, Optional

from pydantic import BaseModel


class LoginIn(BaseModel):
    username: str
    password: str


class TokenOut(BaseModel):
    token: str


class SearchResult(BaseModel):
    type: Literal["artist", "album", "track"]
    foreignId: str                  # artist/album MBID, or recording MBID for tracks
    title: str                      # artist name, album title, or track title
    artist: Optional[str] = None    # for albums/tracks: the artist name
    year: Optional[int] = None
    albumType: Optional[str] = None
    imageUrl: Optional[str] = None
    inLibrary: bool = False
    requested: bool = False         # already has a live request (lock in UI)
    # Track-only: the parent album, so requesting a song can add its album.
    albumForeignId: Optional[str] = None
    albumTitle: Optional[str] = None


class RequestIn(BaseModel):
    type: Literal["artist", "album", "track"]
    foreignId: str
    # Track-only: which release the song belongs to. A track request currently
    # adds this whole album (the proven album pipeline). `mode` is reserved for
    # the upcoming single-track path; for now any track request is album-scoped.
    albumForeignId: Optional[str] = None
    mode: Optional[Literal["album", "track"]] = None


class Pipeline(BaseModel):
    stage: str                    # requested | searching | downloading | importing | available
    stageIndex: int               # position in `stages`
    stages: list[str]             # ordered stage keys for the stepper UI
    percent: float                # overall album completion (percentOfTracks)
    trackFiles: int               # imported track files
    trackCount: int               # total tracks expected
    detail: str                   # human-readable current-stage line
    failed: bool = False          # a download/import failed — needs retry
    stuck: bool = False           # no source found for a long time — offer retry


class RequestOut(BaseModel):
    id: int
    type: str
    foreignId: str
    title: str
    artist: Optional[str] = None
    imageUrl: Optional[str] = None
    status: str          # pending | downloading | available | failed | error
    createdAt: str
    pipeline: Optional[Pipeline] = None
    requestedBy: Optional[str] = None   # username; only populated for admins


class Me(BaseModel):
    username: str
    role: str            # admin | user


class User(BaseModel):
    id: int
    username: str
    role: str
    createdAt: str


class UserCreate(BaseModel):
    username: str
    password: str
    role: Literal["admin", "user"] = "user"


class PasswordChange(BaseModel):
    currentPassword: str
    newPassword: str


class Track(BaseModel):
    position: int                   # track number within its disc
    title: str
    durationMs: Optional[int] = None
    mediumNumber: int = 1           # disc number (1 unless a multi-disc release)


class DiscoverCategories(BaseModel):
    genres: list[str] = []      # most common genres in the unowned pool
    decades: list[int] = []     # decades present, newest first (e.g. 2020, 2010)


class ServiceLink(BaseModel):
    name: str
    url: str


class ActionResult(BaseModel):
    ok: bool = True
    message: Optional[str] = None


class Settings(BaseModel):
    quality: Optional[str] = None   # "mp3" | "flac"; null when the Soularr adapter is off
    ntfyTopic: Optional[str] = None
    ntfyUrl: Optional[str] = None


class SettingsIn(BaseModel):
    quality: Literal["mp3", "flac"]
