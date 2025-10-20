import asyncio
import aiohttp
import time
from collections import deque, defaultdict
from functools import lru_cache

# ————— Configuration —————

TMDB_API_KEY = "7d7100c63fb20b92caefad128c8d6b4c"
TMDB_BASE = "https://api.themoviedb.org/3"

# Limits / tuning parameters
MAX_STEPS = 50
MAX_MOVIES_PER_ACTOR = 25    # when expanding actor → movies, only take top N
MAX_CAST_PER_MOVIE = 100     # when expanding movie → cast, skip or sample if cast is huge
CONCURRENT_REQUESTS = 10     # max number of simultaneous HTTP requests
REQUEST_DELAY = 0.2           # minimal delay between batches to avoid rate limits

# ————— Async HTTP + caching layer —————

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

    # caching wrappers (in-memory)
    @lru_cache(maxsize=5000)
    def _cache_actor_movies(self, actor_id):
        # placeholder; will be awaited via wrapper
        raise RuntimeError("Should not call directly")

    async def get_actor_movies(self, actor_id):
        # Use caching via lru_cache on underlying coroutine
        key = actor_id
        # wrapper to wrap coroutine with cache
        # Trick: use a separate map
        return await self._get_actor_movies_uncached(actor_id)

    async def _get_actor_movies_uncached(self, actor_id):
        data = await self._get(f"/person/{actor_id}/movie_credits")
        cast = data.get("cast", [])
        # sort by popularity or vote count if available (descending) so first are more “important”
        cast_sorted = sorted(cast, key=lambda m: m.get("popularity", 0), reverse=True)
        return [m["id"] for m in cast_sorted]

    @lru_cache(maxsize=5000)
    def _cache_movie_cast(self, movie_id):
        # placeholder
        raise RuntimeError("Should not call directly")

    async def get_movie_cast(self, movie_id):
        return await self._get_movie_cast_uncached(movie_id)

    async def _get_movie_cast_uncached(self, movie_id):
        data = await self._get(f"/movie/{movie_id}/credits")
        cast = data.get("cast", [])
        # optionally sort by popularity too
        cast_sorted = sorted(cast, key=lambda c: c.get("popularity", 0), reverse=True)
        return [c["id"] for c in cast_sorted]

    @lru_cache(maxsize=5000)
    def _cache_actor_name(self, actor_id):
        raise RuntimeError("")

    async def get_actor_name(self, actor_id):
        data = await self._get(f"/person/{actor_id}")
        return data.get("name")

    @lru_cache(maxsize=5000)
    def _cache_movie_title(self, movie_id):
        raise RuntimeError("")

    async def get_movie_title(self, movie_id):
        data = await self._get(f"/movie/{movie_id}")
        return data.get("title")

    async def search_person(self, name, max_results=5):
        data = await self._get("/search/person", {"query": name})
        out = []
        for r in data.get("results", [])[:max_results]:
            known_for = r.get("known_for", [])
            titles = [x.get("title") or x.get("name") for x in known_for]
            out.append((r["id"], r["name"], titles))
        return out

    async def search_movie(self, title, max_results=5):
        data = await self._get("/search/movie", {"query": title})
        out = []
        for r in data.get("results", [])[:max_results]:
            out.append((r["id"], r["title"], r.get("release_date")))
        return out

# ————— Bidirectional BFS with pruning & async expansion —————

class Frontier:
    def __init__(self):
        self.visited = {}  # map (type, id) -> path list
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
                         goal_movie_ids: set[int]):
    """
    Bidirectional from actor side and goal movies side.
    Returns path when frontiers meet, else None.
    """

    # Initialize frontiers
    frontA = Frontier()
    frontB = Frontier()

    frontA.add("actor", actor_id)
    for mid in goal_movie_ids:
        frontB.add("movie", mid)

    # Preload goal movie casts so backward frontier is warmed
    # That is, fetch casts of all goal movies concurrently
    tasks = [client.get_movie_cast(mid) for mid in goal_movie_ids]
    _ = await asyncio.gather(*tasks, return_exceptions=True)

    for step in range(MAX_STEPS):
        # Expand the smaller frontier
        if len(frontA.queue) <= len(frontB.queue):
            meet = await expand_frontier(client, frontA, frontB, direction="forward")
        else:
            meet = await expand_frontier(client, frontB, frontA, direction="backward")

        if meet:
            # meet is the meeting (ntype, nid) node
            (mtype, mid) = meet
            pathA = frontA.path(mtype, mid)
            pathB = frontB.path(mtype, mid)
            # reverse pathB (exclude meeting point to combine)
            revB = list(reversed(pathB))
            return pathA + revB[1:]
    return None

async def expand_frontier(client: TMDbClient,
                          frontier: Frontier,
                          other: Frontier,
                          direction: str):
    """
    Expand one layer from frontier.
    direction = "forward": frontier is actor → movie → actor expansion
    direction = "backward": frontier is movie → actor → movie expansion
    Returns meeting (ntype, id) if found, else None.
    """

    size = len(frontier.queue)
    # for each node in this layer, expand
    for _ in range(size):
        ntype, nid = frontier.queue.popleft()
        path = frontier.visited[(ntype, nid)]

        if direction == "forward":
            # actor → movies, then movie → actors
            if ntype == "actor":
                movies = await client.get_actor_movies(nid)
                # prune to top N
                movies = movies[:MAX_MOVIES_PER_ACTOR]
                for m in movies:
                    if frontier.has_visited("movie", m):
                        continue
                    newp = path + [("movie", m)]
                    frontier.extend_node("movie", m, newp)
                    if other.has_visited("movie", m):
                        return ("movie", m)
            else:  # ntype == "movie"
                cast = await client.get_movie_cast(nid)
                # prune large casts
                if len(cast) > MAX_CAST_PER_MOVIE:
                    cast = cast[:MAX_CAST_PER_MOVIE]
                for a in cast:
                    if frontier.has_visited("actor", a):
                        continue
                    newp = path + [("actor", a)]
                    frontier.extend_node("actor", a, newp)
                    if other.has_visited("actor", a):
                        return ("actor", a)

        else:  # direction == "backward"
            # Here frontier is from goal movie side, so we invert expansion
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
            else:  # ntype == "actor"
                movies = await client.get_actor_movies(nid)
                movies = movies[:MAX_MOVIES_PER_ACTOR]
                for m in movies:
                    if frontier.has_visited("movie", m):
                        continue
                    newp = path + [("movie", m)]
                    frontier.extend_node("movie", m, newp)
                    if other.has_visited("movie", m):
                        return ("movie", m)

    return None

# ————— Top-level interface & path printing —————

async def find_actor_to_any_goal_async(actor_name: str, goal_movie_titles: list[str]):
    async with aiohttp.ClientSession() as sess:
        client = TMDbClient(sess)
        # resolve actor
        persons = await client.search_person(actor_name)
        if not persons:
            print("Actor not found.")
            return
        actor_id, actor_name_clean, _ = persons[0]

        # resolve goals
        goal_ids = set()
        for g in goal_movie_titles:
            movies = await client.search_movie(g)
            if movies:
                mid, title, _ = movies[0]
                goal_ids.add(mid)

        path = await meet_in_middle(client, actor_id, goal_ids)
        if path:
            # convert to names and print
            names = []
            for (ntype, nid) in path:
                if ntype == "actor":
                    nm = await client.get_actor_name(nid)
                else:
                    nm = await client.get_movie_title(nid)
                names.append(f"{ntype.upper()}: {nm}")
            print(" → ".join(names))
        else:
            print("No connection found within limits.")


def find_actor_to_any_goal(actor_name: str, goal_movie_titles: list[str]):
    asyncio.run(find_actor_to_any_goal_async(actor_name, goal_movie_titles))


if __name__ == "__main__":
    actor = input("Actor name: ")
    goals = ["The Bad Guys 2", "Frozen Empire"]
    print("Searching …")
    find_actor_to_any_goal(actor, goals)
