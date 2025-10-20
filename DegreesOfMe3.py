import sys
import asyncio
import aiohttp
from collections import deque
from functools import lru_cache

from PyQt5.QtCore import Qt

from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit,
    QPushButton, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QTextEdit, QMessageBox
)

TMDB_API_KEY = "7d7100c63fb20b92caefad128c8d6b4c"
TMDB_BASE = "https://api.themoviedb.org/3"

MAX_STEPS = 100
MAX_MOVIES_PER_ACTOR = 25
MAX_CAST_PER_MOVIE = 25

class TMDbClient:
    def __init__(self, session):
        self.session = session

    async def _get(self, path, params=None):
        if params is None:
            params = {}
        params["api_key"] = TMDB_API_KEY
        url = f"{TMDB_BASE}{path}"
        async with self.session.get(url, params=params) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"TMDb error {resp.status}: {text}")
            return await resp.json()

    @lru_cache(maxsize=1000)
    async def get_movie_genres(self):
        data = await self._get("/genre/movie/list")
        return { g["name"].lower(): g["id"] for g in data.get("genres", []) }

    @lru_cache(maxsize=1000)
    async def get_documentary_genre_id(self):
        genres = await self.get_movie_genres()
        return genres.get("documentary", None)

    @lru_cache(maxsize=5000)
    async def get_actor_movies(self, actor_id):
        data = await self._get(f"/person/{actor_id}/movie_credits")
        cast = data.get("cast", [])
        cast_sorted = sorted(cast, key=lambda m: m.get("popularity", 0), reverse=True)
        return [ m["id"] for m in cast_sorted ]

    @lru_cache(maxsize=5000)
    async def get_movie_cast(self, movie_id):
        data = await self._get(f"/movie/{movie_id}/credits")
        cast = data.get("cast", [])
        cast_sorted = sorted(cast, key=lambda c: c.get("popularity", 0), reverse=True)
        return [ c["id"] for c in cast_sorted ]

    @lru_cache(maxsize=5000)
    async def get_actor_name(self, actor_id):
        data = await self._get(f"/person/{actor_id}")
        return data.get("name")

    @lru_cache(maxsize=5000)
    async def get_movie_title(self, movie_id):
        data = await self._get(f"/movie/{movie_id}")
        return data.get("title")

    async def search_person(self, name, max_results=5):
        data = await self._get("/search/person", {"query": name})
        out = []
        for r in data.get("results", [])[:max_results]:
            known_for = r.get("known_for", [])
            titles = [ x.get("title") or x.get("name") for x in known_for ]
            out.append((r["id"], r["name"], titles))
        return out

    async def search_movie(self, title, max_results=5):
        data = await self._get("/search/movie", {"query": title})
        out = []
        for r in data.get("results", [])[:max_results]:
            out.append((r["id"], r["title"], r.get("release_date")))
        return out

class Frontier:
    def __init__(self):
        self.visited = {}  # key: (node_type, id) -> path list of (type,id)
        self.queue = deque()

    def add(self, ntype, nid):
        self.visited[(ntype, nid)] = [(ntype, nid)]
        self.queue.append((ntype, nid))

    def extend_node(self, ntype, nid, path):
        self.visited[(ntype, nid)] = path
        self.queue.append((ntype, nid))

    def has_visited(self, ntype, nid):
        return (ntype, nid) in self.visited

    def path(self, ntype, nid):
        return self.visited.get((ntype, nid))

async def meet_in_middle(client: TMDbClient,
                         actor_id: int,
                         goal_movie_ids: set,
                         excluded_movie_ids: set):
    frontA = Frontier()
    frontB = Frontier()
    frontA.add("actor", actor_id)
    for mid in goal_movie_ids:
        frontB.add("movie", mid)

    doc_genre_id = await client.get_documentary_genre_id()

    for step in range(MAX_STEPS):
        # pick smaller frontier to expand
        if len(frontA.queue) <= len(frontB.queue):
            meet = await expand_frontier(client, frontA, frontB, excluded_movie_ids, doc_genre_id, direction="forward")
        else:
            meet = await expand_frontier(client, frontB, frontA, excluded_movie_ids, doc_genre_id, direction="backward")

        if meet:
            (mtype, mid) = meet
            pathA = frontA.path(mtype, mid)
            pathB = frontB.path(mtype, mid)
            revB = list(reversed(pathB))
            full_path = pathA + revB[1:]
            return full_path

    return None

async def expand_frontier(client: TMDbClient,
                          frontier: Frontier,
                          other: Frontier,
                          excluded_movie_ids: set,
                          doc_genre_id: int,
                          direction: str):
    size = len(frontier.queue)
    for _ in range(size):
        ntype, nid = frontier.queue.popleft()
        path = frontier.visited[(ntype, nid)]

        if direction == "forward":
            if ntype == "actor":
                movies = await client.get_actor_movies(nid)
                movies = movies[:MAX_MOVIES_PER_ACTOR]
                filtered = []
                for m in movies:
                    if m in excluded_movie_ids:
                        continue
                    # check documentary
                    details = await client._get(f"/movie/{m}")
                    genres = details.get("genres", [])
                    genre_ids = { g["id"] for g in genres }
                    if doc_genre_id is not None and doc_genre_id in genre_ids:
                        continue
                    filtered.append(m)

                for m in filtered:
                    if frontier.has_visited("movie", m):
                        continue
                    newp = path + [("movie", m)]
                    frontier.extend_node("movie", m, newp)
                    if other.has_visited("movie", m):
                        return ("movie", m)
            else:
                # movie → actor expansion
                cast = await client.get_movie_cast(nid)
                if len(cast) > MAX_CAST_PER_MOVIE:
                    cast = cast[:MAX_CAST_PER_MOVIE]
                for a in cast:
                    if frontier.has_visited("actor", a):
                        continue
                    newp = path + [("actor", a)]
                    frontier.extend_node("actor", a, newp)
                    if other.has_visited("actor", a):
                        return ("actor", a)

        else:  # backward direction
            if ntype == "movie":
                cast = await client.get_movie_cast(nid)
                if len(cast) > MAX_CAST_PER_MOVIE:
                    cast = cast[:MAX_CAST_PER_MOVIE]
                for a in cast:
                    if frontier.has_visited("actor", a):
                        continue
                    newp = path + [("actor", a)]
                    frontier.extend_node("actor", a, newp)
                    if other.has_visited("actor", a):
                        return ("actor", a)
            else:
                # actor → movie expansion from backward side
                movies = await client.get_actor_movies(nid)
                movies = movies[:MAX_MOVIES_PER_ACTOR]
                filtered = []
                for m in movies:
                    if m in excluded_movie_ids:
                        continue
                    details = await client._get(f"/movie/{m}")
                    genres = details.get("genres", [])
                    genre_ids = { g["id"] for g in genres }
                    if doc_genre_id is not None and doc_genre_id in genre_ids:
                        continue
                    filtered.append(m)

                for m in filtered:
                    if frontier.has_visited("movie", m):
                        continue
                    newp = path + [("movie", m)]
                    frontier.extend_node("movie", m, newp)
                    if other.has_visited("movie", m):
                        return ("movie", m)

    return None

async def find_actor_to_any_goal_async(actor_name: str,
                                        goal_movie_titles: list[str],
                                        excluded_movie_ids: set):
    async with aiohttp.ClientSession() as session:
        client = TMDbClient(session)
        # resolve actor
        persons = await client.search_person(actor_name)
        if not persons:
            return None, actor_name
        actor_id, actor_clean, _ = persons[0]

        # resolve goals
        goal_ids = set()
        for title in goal_movie_titles:
            movies = await client.search_movie(title)
            if movies:
                mid, mtitle, _ = movies[0]
                goal_ids.add(mid)

        path = await meet_in_middle(client, actor_id, goal_ids, excluded_movie_ids)
        if path:
            # convert path nodes to (type,name,id)
            named_path = []
            for (ntype, nid) in path:
                if ntype == "actor":
                    nm = await client.get_actor_name(nid)
                    named_path.append(("actor", nm, nid))
                else:
                    nm = await client.get_movie_title(nid)
                    named_path.append(("movie", nm, nid))
            return named_path, actor_clean

        return None, actor_clean

def find_actor_to_any_goal(actor_name: str, goal_movie_titles: list[str], excluded_movie_ids: set):
    return asyncio.run(find_actor_to_any_goal_async(actor_name, goal_movie_titles, excluded_movie_ids))
