import requests
import time
from collections import deque, defaultdict
from functools import lru_cache

# ————— Configuration —————
TMDB_API_KEY = "7d7100c63fb20b92caefad128c8d6b4c"
TMDB_BASE = "https://api.themoviedb.org/3"

# Throttle settings
MIN_REQUEST_INTERVAL = 0.25  # seconds between API calls

_last_request_time = 0.0
def tmdb_get(path, params=None):
    global _last_request_time
    if params is None:
        params = {}
    params["api_key"] = TMDB_API_KEY
    # throttle
    elapsed = time.time() - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        time.sleep(MIN_REQUEST_INTERVAL - elapsed)
    url = f"{TMDB_BASE}{path}"
    resp = requests.get(url, params=params)
    _last_request_time = time.time()
    if resp.status_code != 200:
        raise RuntimeError(f"TMDb API error {resp.status_code}: {resp.text}")
    return resp.json()


# ————— Search & disambiguation —————

def search_persons(name, max_results=5):
    """Return list of (id, name, known_for_titles) matching that person name."""
    data = tmdb_get("/search/person", {"query": name})
    out = []
    for r in data.get("results", [])[:max_results]:
        known_for = r.get("known_for", [])
        titles = [x.get("title") or x.get("name") for x in known_for]
        out.append((r["id"], r["name"], titles))
    return out

def search_movies(title, max_results=5):
    """Return list of (id, title, release_date) matching that movie title."""
    data = tmdb_get("/search/movie", {"query": title})
    out = []
    for r in data.get("results", [])[:max_results]:
        out.append((r["id"], r["title"], r.get("release_date")))
    return out

# ————— Caching credits & details —————

@lru_cache(maxsize=10000)
def get_actor_movies(actor_id):
    """Return list of movie IDs the actor has acted in (cast)"""
    data = tmdb_get(f"/person/{actor_id}/movie_credits")
    cast = data.get("cast", [])
    return [m["id"] for m in cast]

@lru_cache(maxsize=10000)
def get_movie_cast(movie_id):
    """Return list of actor IDs in that movie's cast"""
    data = tmdb_get(f"/movie/{movie_id}/credits")
    cast = data.get("cast", [])
    return [c["id"] for c in cast]

@lru_cache(maxsize=10000)
def get_actor_name(actor_id):
    """Return the name of the actor given ID"""
    data = tmdb_get(f"/person/{actor_id}")
    return data.get("name")

@lru_cache(maxsize=10000)
def get_movie_title(movie_id):
    """Return the title of the movie given ID"""
    data = tmdb_get(f"/movie/{movie_id}")
    return data.get("title")

# ————— Bidirectional BFS —————

class SearchFrontier:
    """
    Maintains frontier, visited map, and paths.
    We'll keep track of ("actor", id) and ("movie", id) nodes.
    """

    def __init__(self):
        # visited: map from (type, id) to path from source
        self.visited = {}  # key: (node_type, id) -> path list of (node_type, id)
        self.queue = deque()

    def add_start(self, node_type, node_id):
        self.visited[(node_type, node_id)] = [(node_type, node_id)]
        self.queue.append((node_type, node_id))

    def expand_one_layer(self):
        """Expand one node from the queue. Return list of neighbors (type, id) expanded."""
        if not self.queue:
            return []
        current_type, current_id = self.queue.popleft()
        current_path = self.visited[(current_type, current_id)]

        neighbors = []
        if current_type == "actor":
            # actor → movies
            for m in get_actor_movies(current_id):
                neighbors.append(("movie", m))
        else:
            # movie → actors
            for a in get_movie_cast(current_id):
                neighbors.append(("actor", a))

        new_added = []
        for ntype, nid in neighbors:
            if (ntype, nid) not in self.visited:
                # record path
                self.visited[(ntype, nid)] = current_path + [(ntype, nid)]
                self.queue.append((ntype, nid))
                new_added.append((ntype, nid))
        return new_added

def find_connection_bidirectional(actor_id, movie_id, max_steps=100):
    """
    Search from actor side and movie side until they meet.
    max_steps is maximum # of “levels” per side (actor → movie → actor → …).
    """

    frontA = SearchFrontier()
    frontB = SearchFrontier()

    frontA.add_start("actor", actor_id)
    frontB.add_start("movie", movie_id)

    for step in range(max_steps):
        # Expand from the smaller frontier (heuristic)
        if len(frontA.queue) <= len(frontB.queue):
            new_nodes = frontA.expand_one_layer()
            # check for intersection
            for n in new_nodes:
                if n in frontB.visited:
                    # meet point
                    pathA = frontA.visited[n]
                    pathB = frontB.visited[n]
                    # frontB path is from movie → … → meet, we need to reverse and skip duplicate meeting node
                    rev = list(reversed(pathB))
                    merged = pathA + rev[1:]
                    return merged
        else:
            new_nodes = frontB.expand_one_layer()
            for n in new_nodes:
                if n in frontA.visited:
                    pathA = frontA.visited[n]
                    pathB = frontB.visited[n]
                    rev = list(reversed(pathB))
                    merged = pathA + rev[1:]
                    return merged

    return None


# ————— Application-level logic —————

def choose_from_list(options, label):
    """Prompt user to pick one of multiple search results."""
    print(f"Multiple {label} matches found:")
    for idx, opt in enumerate(options):
        print(f"  [{idx}] {opt}")
    sel = input(f"Pick index (0..{len(options)-1}), or 'c' to cancel: ")
    if sel.lower().startswith("c"):
        return None
    i = int(sel)
    return options[i]

def resolve_actor(name):
    options = search_persons(name)
    if not options:
        return None
    if len(options) == 1:
        return options[0][0], options[0][1]
    # else multiple
    display = [f"{nm} (known for: {titles})" for (_id, nm, titles) in options]
    chosen = choose_from_list(display, "actor")
    if chosen is None:
        return None
    idx = display.index(chosen)
    return options[idx][0], options[idx][1]

def resolve_movie(title):
    options = search_movies(title)
    if not options:
        return None
    if len(options) == 1:
        return options[0][0], options[0][1]
    display = [f"{nm} ({reldate})" for (_id, nm, reldate) in options]
    chosen = choose_from_list(display, "movie")
    if chosen is None:
        return None
    idx = display.index(chosen)
    return options[idx][0], options[idx][1]

def pretty_print_path(path):
    parts = []
    for (ntype, nid) in path:
        if ntype == "actor":
            nm = get_actor_name(nid)
        else:
            nm = get_movie_title(nid)
        parts.append(f"{ntype.upper()}: {nm}")
    print(" → ".join(parts))


def find_actor_to_movie_path(actor_name, movie_title, max_steps=6):
    act = resolve_actor(actor_name)
    if act is None:
        print(f"Could not resolve actor '{actor_name}'")
        return
    actor_id, actor_name = act

    mov = resolve_movie(movie_title)
    if mov is None:
        print(f"Could not resolve movie '{movie_title}'")
        return
    movie_id, movie_title = mov

    print(f"Searching connection: Actor {actor_name} (ID {actor_id}) → Movie {movie_title} (ID {movie_id})")
    path = find_connection_bidirectional(actor_id, movie_id, max_steps=max_steps)
    if path:
        print("** Path found **")
        pretty_print_path(path)
    else:
        print(f"No connection found within {max_steps} steps.")


if __name__ == "__main__":
    print("=== Actor-to-Movie Connector ===")
    actor = input("Actor name: ")
    movie = input("Movie title: ")
    find_actor_to_movie_path(actor, movie, max_steps=50)
