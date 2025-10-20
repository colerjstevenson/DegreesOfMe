// TMDb Actor → Movie Connection Finder (browser JS)
// Configure your API key here (visible in client-side code when hosted)
const TMDB_API_KEY = "7d7100c63fb20b92caefad128c8d6b4c"; // <-- replace with your key
const TMDB_BASE = "https://api.themoviedb.org/3";

const MAX_STEPS = 100;
const MAX_MOVIES_PER_ACTOR = 25;
const MAX_CAST_PER_MOVIE = 25;

// Simple in-memory cache to avoid duplicate network calls during a search
const cache = new Map();
function cacheKey(prefix, id) { return `${prefix}:${id}`; }

async function tmdbGet(path, params = {}){
  params.api_key = TMDB_API_KEY;
  const url = new URL(TMDB_BASE + path);
  Object.keys(params).forEach(k => url.searchParams.append(k, params[k]));
  const key = url.toString();
  if (cache.has(key)) return cache.get(key);
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`TMDb error ${resp.status}`);
  const json = await resp.json();
  cache.set(key, json);
  return json;
}

async function searchPerson(name, max_results = 5){
  const data = await tmdbGet('/search/person', { query: name });
  const out = [];
  for (const r of (data.results || []).slice(0, max_results)){
    const known_for = r.known_for || [];
    const titles = known_for.map(x => x.title || x.name);
    out.push({ id: r.id, name: r.name, titles });
  }
  return out;
}

async function searchMovie(title, max_results = 5){
  const data = await tmdbGet('/search/movie', { query: title });
  const out = [];
  for (const r of (data.results || []).slice(0, max_results)){
    out.push({ id: r.id, title: r.title, release_date: r.release_date });
  }
  return out;
}

async function getActorMovies(actor_id){
  const data = await tmdbGet(`/person/${actor_id}/movie_credits`);
  const cast = data.cast || [];
  cast.sort((a,b) => (b.popularity||0)-(a.popularity||0));
  return cast.map(m => m.id);
}

async function getMovieCast(movie_id){
  const data = await tmdbGet(`/movie/${movie_id}/credits`);
  const cast = data.cast || [];
  cast.sort((a,b) => (b.popularity||0)-(a.popularity||0));
  return cast.map(c => c.id);
}

async function getActorName(actor_id){
  const data = await tmdbGet(`/person/${actor_id}`);
  return data.name;
}

async function getMovieTitle(movie_id){
  const data = await tmdbGet(`/movie/${movie_id}`);
  return data.title;
}

async function getDocumentaryGenreId(){
  const data = await tmdbGet('/genre/movie/list');
  const map = new Map((data.genres||[]).map(g => [g.name.toLowerCase(), g.id]));
  return map.get('documentary') || null;
}

// Frontier class like Python version
class Frontier{
  constructor(){
    this.visited = new Map(); // key: `${type}:${id}` -> path array of [type,id]
    this.queue = [];
  }
  key(ntype, nid){ return `${ntype}:${nid}`; }
  add(ntype, nid){ this.visited.set(this.key(ntype,nid), [[ntype,nid]]); this.queue.push([ntype,nid]); }
  extend_node(ntype, nid, path){ this.visited.set(this.key(ntype,nid), path); this.queue.push([ntype,nid]); }
  has_visited(ntype, nid){ return this.visited.has(this.key(ntype,nid)); }
  path(ntype, nid){ return this.visited.get(this.key(ntype,nid)); }
}

async function meetInMiddle(actor_id, goal_movie_ids, excluded_movie_ids){
  const frontA = new Frontier();
  const frontB = new Frontier();
  frontA.add('actor', actor_id);
  for (const mid of goal_movie_ids) frontB.add('movie', mid);

  const doc_genre_id = await getDocumentaryGenreId();

  for (let step=0; step<MAX_STEPS; step++){
    let frontierToExpand, otherFrontier, direction;
    if (frontA.queue.length <= frontB.queue.length){
      frontierToExpand = frontA; otherFrontier = frontB; direction = 'forward';
    } else {
      frontierToExpand = frontB; otherFrontier = frontA; direction = 'backward';
    }
    const meet = await expandFrontierStep(frontierToExpand, otherFrontier, excluded_movie_ids, doc_genre_id, direction);
    if (meet){
      const [mtype, mid] = meet;
      const pathA = frontA.path(mtype, mid);
      const pathB = frontB.path(mtype, mid);
      const revB = Array.from(pathB).reverse();
      const full = pathA.concat(revB.slice(1));
      return full;
    }
  }
  return null;
}

async function expandFrontierStep(frontier, other, excluded_movie_ids, doc_genre_id, direction){
  const size = frontier.queue.length;
  for (let i=0;i<size;i++){
    const [ntype, nid] = frontier.queue.shift();
    const path = frontier.visited.get(frontier.key(ntype,nid));

    if (direction === 'forward'){
      if (ntype === 'actor'){
        let movies = await getActorMovies(nid);
        movies = movies.slice(0, MAX_MOVIES_PER_ACTOR);
        const filtered = [];
        for (const m of movies){
          if (excluded_movie_ids.has(m)) continue;
          const details = await tmdbGet(`/movie/${m}`);
          const genres = details.genres || [];
          const genre_ids = new Set(genres.map(g => g.id));
          if (doc_genre_id !== null && genre_ids.has(doc_genre_id)) continue;
          filtered.push(m);
        }
        for (const m of filtered){
          if (frontier.has_visited('movie', m)) continue;
          const newp = path.concat([['movie', m]]);
          frontier.extend_node('movie', m, newp);
          if (other.has_visited('movie', m)) return ['movie', m];
        }
      } else {
        // movie -> actor
        let cast = await getMovieCast(nid);
        if (cast.length > MAX_CAST_PER_MOVIE) cast = cast.slice(0, MAX_CAST_PER_MOVIE);
        for (const a of cast){
          if (frontier.has_visited('actor', a)) continue;
          const newp = path.concat([['actor', a]]);
          frontier.extend_node('actor', a, newp);
          if (other.has_visited('actor', a)) return ['actor', a];
        }
      }
    } else {
      // backward
      if (ntype === 'movie'){
        let cast = await getMovieCast(nid);
        if (cast.length > MAX_CAST_PER_MOVIE) cast = cast.slice(0, MAX_CAST_PER_MOVIE);
        for (const a of cast){
          if (frontier.has_visited('actor', a)) continue;
          const newp = path.concat([['actor', a]]);
          frontier.extend_node('actor', a, newp);
          if (other.has_visited('actor', a)) return ['actor', a];
        }
      } else {
        let movies = await getActorMovies(nid);
        movies = movies.slice(0, MAX_MOVIES_PER_ACTOR);
        const filtered = [];
        for (const m of movies){
          if (excluded_movie_ids.has(m)) continue;
          const details = await tmdbGet(`/movie/${m}`);
          const genres = details.genres || [];
          const genre_ids = new Set(genres.map(g => g.id));
          if (doc_genre_id !== null && genre_ids.has(doc_genre_id)) continue;
          filtered.push(m);
        }
        for (const m of filtered){
          if (frontier.has_visited('movie', m)) continue;
          const newp = path.concat([['movie', m]]);
          frontier.extend_node('movie', m, newp);
          if (other.has_visited('movie', m)) return ['movie', m];
        }
      }
    }
  }
  return null;
}

async function findActorToAnyGoal(actor_name, goal_titles, excluded_ids){
  cache.clear();
  const persons = await searchPerson(actor_name);
  if (!persons || persons.length === 0) return [null, actor_name];
  const actor_id = persons[0].id;
  const actor_clean = persons[0].name;

  const goal_ids = new Set();
  for (const title of goal_titles){
    const movies = await searchMovie(title);
    if (movies && movies.length>0) goal_ids.add(movies[0].id);
  }

  const path = await meetInMiddle(actor_id, goal_ids, excluded_ids);
  if (path){
    const named = [];
    for (const [ntype, nid] of path){
      if (ntype === 'actor'){
        const nm = await getActorName(nid);
        named.push(['actor', nm, nid]);
      } else {
        const nm = await getMovieTitle(nid);
        named.push(['movie', nm, nid]);
      }
    }
    return [named, actor_clean];
  }
  return [null, actor_clean];
}

// UI wiring
const actorInput = document.getElementById('actorName');
const findBtn = document.getElementById('findBtn');
const pathList = document.getElementById('pathList');
const status = document.getElementById('status');
const excludedList = document.getElementById('excludedList');
const clearExcl = document.getElementById('clearExcl');
const goalInput = document.getElementById('goalTitles');

let excludedIds = new Set();
let excludedTitlesById = new Map();
let currentActor = null;

function updateExcludedDisplay(){
  const lines = Array.from(Array.from(excludedIds).sort()).map(mid => `${mid} — ${excludedTitlesById.get(mid) || '(unknown title)'}`);
  excludedList.value = lines.join('\n');
}

clearExcl.addEventListener('click', () =>{
  excludedIds.clear(); excludedTitlesById.clear(); updateExcludedDisplay(); status.textContent = 'Exclude list cleared.'; pathList.innerHTML = '';
});

findBtn.addEventListener('click', async () =>{
  const actor = actorInput.value.trim();
  if (!actor){ status.textContent = 'Please enter an actor name.'; return; }
  const goal_titles = goalInput.value.split(',').map(s => s.trim()).filter(Boolean);

  currentActor = actor;
  status.textContent = 'Searching… please wait.';
  findBtn.disabled = true;
  pathList.innerHTML = '';
  updateExcludedDisplay();

  try{
    const [namedPath, actor_clean] = await findActorToAnyGoal(actor, goal_titles, excludedIds);
    findBtn.disabled = false;
    if (namedPath){
      status.textContent = `Found path for actor ${actor_clean}. Click a movie to exclude and retry.`;
      populatePathList(namedPath);
    } else {
      status.textContent = `No connection found for actor ${actor_clean} (with exclusions).`;
    }
  } catch (err){
    findBtn.disabled = false;
    status.textContent = `Error: ${err.message}`;
    console.error(err);
  }
});

function populatePathList(namedPath){
  pathList.innerHTML = '';
  for (const [ntype, nm, nid] of namedPath){
    const li = document.createElement('li');
    const left = document.createElement('div');
    left.textContent = `${ntype.toUpperCase()}: ${nm} (ID ${nid})`;
    const right = document.createElement('div');
    right.className = 'meta';
    if (ntype === 'movie' && !goalInput.value.split(',').map(s => s.trim()).includes(nm)){
      const btn = document.createElement('button');
      btn.textContent = 'Exclude & Retry';
      btn.addEventListener('click', async () =>{
        excludedIds.add(nid);
        excludedTitlesById.set(nid, nm);
        updateExcludedDisplay();
        status.textContent = `Excluding ID ${nid} — ${nm}, re-searching…`;
        findBtn.disabled = true; pathList.innerHTML = '';
        try{
          const [namedPath2, actor_clean] = await findActorToAnyGoal(currentActor, goalInput.value.split(',').map(s=>s.trim()).filter(Boolean), excludedIds);
          findBtn.disabled = false;
          if (namedPath2){ status.textContent = 'Found new path after exclusion.'; populatePathList(namedPath2); }
          else { status.textContent = `No connection found with exclusions: ${Array.from(excludedIds).join(',')}`; }
        } catch (err){ findBtn.disabled = false; status.textContent = `Error: ${err.message}`; }
      });
      right.appendChild(btn);
    }
    li.appendChild(left);
    li.appendChild(right);
    pathList.appendChild(li);
  }
}

// small helper to warn about missing API key
if (!TMDB_API_KEY || TMDB_API_KEY.includes('YOUR_TMDB')){
  status.textContent = 'Warning: set your TMDb API key at the top of app.js to use the app.';
}
