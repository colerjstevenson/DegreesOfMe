#!/usr/bin/env python3
"""
visualize_paths.py

Read path data (from actor_connections.csv or a JSON file) and render a network where
actors are nodes and films are the labeled edges between them. Goal movie nodes are
placed in the center and connected to the actors that appear next to them in paths.

Usage:
  python visualize_paths.py --csv actor_connections.csv --out graph.png
  python visualize_paths.py --json paths.json --out graph.html

If pyvis is installed and --out ends with .html the script will generate an interactive HTML.
Otherwise it will produce a static PNG via matplotlib.
"""
import argparse
import csv
import json
import ast
from collections import defaultdict
import os

try:
    import networkx as nx
except Exception as e:
    raise SystemExit("networkx is required. Install with: pip install networkx matplotlib pyvis")

HAS_PYVIS = False
try:
    from pyvis.network import Network
    HAS_PYVIS = True
except Exception:
    HAS_PYVIS = False

import matplotlib.pyplot as plt
import math
import textwrap
from matplotlib.patches import Patch
from matplotlib import cm
from matplotlib.colors import to_hex


def parse_csv(csv_path):
    rows = []
    # Try a few common encodings to avoid UnicodeDecodeError on Windows-produced CSVs
    tried_encodings = ['utf-8', 'utf-8-sig', 'cp1252', 'latin-1']
    last_exc = None
    for enc in tried_encodings:
        try:
            with open(csv_path, newline='', encoding=enc) as f:
                reader = csv.DictReader(f)
                for r in reader:
                    # Path field may be a Python-repr of a list/tuple; try ast.literal_eval
                    path_field = r.get('Path') or r.get('Path\n') or r.get('Path ')
                    parsed = None
                    if path_field:
                        try:
                            parsed = ast.literal_eval(path_field)
                        except Exception:
                            # maybe it's JSON
                            try:
                                parsed = json.loads(path_field)
                            except Exception:
                                parsed = None
                    rows.append({'Actor': r.get('Actor'), 'Path Length': r.get('Path Length'), 'Path': parsed[0] if parsed else None})
            return rows
        except UnicodeDecodeError as e:
            last_exc = e
            # try next encoding
            continue
        except Exception as e:
            # other errors should propagate
            raise

    # If we reach here no encoding worked
    msg = f"Failed to read CSV with tried encodings {tried_encodings}. Last error: {last_exc}"
    raise UnicodeDecodeError(msg, b'', 0, 1, "encoding detection failed")


def parse_json(json_path):
    with open(json_path, encoding='utf-8') as f:
        data = json.load(f)
    # expect data to be a list of {actor, path}
    out = []
    for item in data:
        out.append({'Actor': item.get('actor') or item.get('Actor'), 'Path': item.get('path') or item.get('Path')})
    return out


def build_graph(rows):
    # Use MultiGraph because there can be multiple films between same actor pair
    G = nx.MultiGraph()

    # We'll also collect goal movies (movies that end a path)
    goal_movies = set()

    # Map movie id -> title for labeling
    movie_titles = {}

    for r in rows:
        path = r.get('Path')
        if not path:
            continue
        # path expected as sequence of (type, name, id)
        nodes = list(path)
        # find goal movie(s) as last movie nodes in the path
        if nodes[-1][0] == 'movie':
            goal_movies.add((nodes[-1][1], nodes[-1][2]))
        # iterate actor - movie - actor triples
        for i in range(0, len(nodes)-2, 2):
            a1 = nodes[i]
            m = nodes[i+1]
            a2 = nodes[i+2]
            if a1[0] != 'actor' or m[0] != 'movie' or a2[0] != 'actor':
                continue
            a1_name, a1_id = a1[1], a1[2]
            a2_name, a2_id = a2[1], a2[2]
            m_name, m_id = m[1], m[2]
            movie_titles[m_id] = m_name
            # add actor nodes
            G.add_node(a1_name, type='actor', id=a1_id)
            G.add_node(a2_name, type='actor', id=a2_id)
            # add an edge between actors labeled with movie id (store title in attr)
            G.add_edge(a1_name, a2_name, movie_id=m_id, movie_title=m_name)

    # Create a single unlabeled goal node that represents all goal movies, and connect
    # every actor adjacent to any goal movie to this central node.
    goal_nodes = []
    if goal_movies:
        gnode = "GOAL"
        goal_nodes.append(gnode)
        # store all goal ids on the single node for reference
        G.add_node(gnode, type='goal', movie_ids=[mid for (_, mid) in goal_movies])
        # find actors adjacent to any goal movie in rows and connect them to the single goal node
        for r in rows:
            path = r.get('Path')
            if not path:
                continue
            for i in range(0, len(path)-1):
                if path[i][0] == 'actor' and path[i+1][0] == 'movie' and (path[i+1][2],) :
                    mid = path[i+1][2]
                    if any(mid == gm[1] for gm in goal_movies):
                        actor_name = path[i][1]
                        G.add_node(actor_name, type='actor')
                        G.add_edge(actor_name, gnode, movie_id=mid, movie_title=path[i+1][1])
                if path[i][0] == 'movie' and path[i+1][0] == 'actor' and (path[i][0],):
                    mid = path[i][2]
                    if any(mid == gm[1] for gm in goal_movies):
                        actor_name = path[i+1][1]
                        G.add_node(actor_name, type='actor')
                        G.add_edge(actor_name, gnode, movie_id=mid, movie_title=path[i][1])

    return G, goal_nodes


def draw_static(G, goal_nodes, out_path):
    # dynamic layout parameters based on graph size
    n = max(1, G.number_of_nodes())
    # k controls optimal distance between nodes; scale with graph size
    k = 0.7 / math.sqrt(n)
    iterations = 300

    # scale node sizes by degree for readability (compute early for overlap solver)
    deg = dict(G.degree())
    node_sizes = {node: 120 + 40 * deg.get(node, 0) for node in G.nodes()}

    # layout (use Kamada-Kawai as requested)
    # choose a larger scale to spread nodes out; we'll also amplify positions afterwards
    base_scale = max(2.0, math.sqrt(n) * 1.5)
    pos = nx.kamada_kawai_layout(G, scale=base_scale)

    # amplify positions to increase spread (helps for very dense graphs)
    spread_factor = max(1.5, math.sqrt(n) / 2.0)
    for key, (x, y) in list(pos.items()):
        pos[key] = (x * spread_factor, y * spread_factor)

    # post-process positions to reduce overlaps: iterative repulsive adjustments
    def resolve_overlaps(pos_map, node_sizes_map, fixed_nodes=None, max_iter=500):
        if fixed_nodes is None:
            fixed_nodes = set()
        # extract bounding box
        xs = [p[0] for p in pos_map.values()]
        ys = [p[1] for p in pos_map.values()]
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        dx = maxx - minx if maxx != minx else 1.0
        dy = maxy - miny if maxy != miny else 1.0

        # normalize positions to [-1,1]
        norm = {n: [ (p[0]-minx)/dx*2-1, (p[1]-miny)/dy*2-1 ] for n,p in pos_map.items()}

        max_size = max(node_sizes_map.values()) if node_sizes_map else 1
        # radius estimate (make larger to force additional spacing)
        radii = {n: 0.12 * (math.sqrt(s)/math.sqrt(max_size)) + 0.04 for n,s in node_sizes_map.items()}

        nodes = list(norm.keys())
        # allow more iterations for a tighter, overlap-free layout
        for it in range(max_iter):
            moved = 0
            for i in range(len(nodes)):
                ni = nodes[i]
                xi, yi = norm[ni]
                ri = radii.get(ni, 0.05)
                for j in range(i+1, len(nodes)):
                    nj = nodes[j]
                    xj, yj = norm[nj]
                    rj = radii.get(nj, 0.05)
                    min_dist = ri + rj + 0.02
                    dx_ = xi - xj
                    dy_ = yi - yj
                    dist = math.hypot(dx_, dy_)
                    if dist < 1e-6:
                        # small jitter
                        angle = (it + i + j) % 360
                        dxj = 0.01 * math.cos(math.radians(angle))
                        dyj = 0.01 * math.sin(math.radians(angle))
                        if ni not in fixed_nodes:
                            norm[ni][0] += dxj; norm[ni][1] += dyj; moved += 1
                        if nj not in fixed_nodes:
                            norm[nj][0] -= dxj; norm[nj][1] -= dyj; moved += 1
                        continue
                    if dist < min_dist:
                        overlap = (min_dist - dist)
                        ux = dx_/dist
                        uy = dy_/dist
                        shift = overlap * 0.6
                        if ni not in fixed_nodes:
                            norm[ni][0] += ux * shift; norm[ni][1] += uy * shift; moved += 1
                        if nj not in fixed_nodes:
                            norm[nj][0] -= ux * shift; norm[nj][1] -= uy * shift; moved += 1
            if moved == 0:
                break

        # map back
        for n, (nx_, ny_) in norm.items():
            x = minx + ((nx_ + 1)/2.0) * dx
            y = miny + ((ny_ + 1)/2.0) * dy
            pos_map[n] = (x, y)
        return pos_map

    # keep goal nodes fixed (center later)
    pos = resolve_overlaps(pos, node_sizes, fixed_nodes=set(goal_nodes), max_iter=400)

    # pin goal nodes to center coordinates and spread them wider for clarity
    center = (0.0, 0.0)
    offset = max(0.12, 0.25 / max(1, len(goal_nodes)))
    for i, g in enumerate(goal_nodes):
        pos[g] = (center[0] + (i - (len(goal_nodes)-1)/2.0) * offset, center[1])

    # figure size scales with graph size but clamps to reasonable bounds
    width = min(24, max(10, 6 + n * 0.05))
    height = min(18, max(8, 4 + n * 0.03))
    plt.figure(figsize=(width, height))

    actor_nodes = [
        n for n,d in G.nodes(data=True) if d.get('type') == 'actor'
    ]
    goal_nodes_present = [n for n,d in G.nodes(data=True) if d.get('type') == 'goal']

    # scale node sizes by degree for readability
    deg = dict(G.degree())
    node_sizes = {n: 120 + 40 * deg.get(n, 0) for n in G.nodes()}

    # We'll draw nodes after computing distance-based colors below

    # draw edges (for MultiGraph we combine labels between the same pair)
    simpleG = nx.Graph()
    for u,v,key,edata in G.edges(keys=True, data=True):
        if simpleG.has_edge(u,v):
            simpleG[u][v]['labels'].append(edata.get('movie_title'))
        else:
            simpleG.add_edge(u,v, labels=[edata.get('movie_title')])

    # compute shortest-path distances from GOAL (if present) and build color mapping
    distances = {}
    if 'GOAL' in simpleG.nodes():
        try:
            distances = dict(nx.single_source_shortest_path_length(simpleG, 'GOAL'))
        except Exception:
            distances = {}
    # determine max distance (exclude GOAL itself)
    maxd = 0
    if distances:
        maxd = max((d for n,d in distances.items() if n != 'GOAL'), default=0)

    cmap = cm.get_cmap('plasma')

    # build node color map
    node_colors_map = {}
    for n in simpleG.nodes():
        if n == 'GOAL':
            node_colors_map[n] = '#E85A71'
        else:
            if n in distances:
                d = distances[n]
                if d == 0:
                    node_colors_map[n] = '#E85A71'
                elif maxd > 0:
                    val = (d - 1) / max(1, maxd - 1) if maxd > 1 else 0.0
                    node_colors_map[n] = to_hex(cmap(1.0 - val))
                else:
                    node_colors_map[n] = to_hex(cmap(0.5))
            else:
                node_colors_map[n] = '#999999'

    # draw edges first
    nx.draw_networkx_edges(simpleG, pos, alpha=0.6)

    # draw actor nodes colored by distance
    actor_node_colors = [node_colors_map.get(n, '#999999') for n in actor_nodes]
    nx.draw_networkx_nodes(G, pos, nodelist=actor_nodes, node_color=actor_node_colors,
                           node_size=[node_sizes[n] for n in actor_nodes], alpha=0.95)
    # draw the GOAL node on top
    goal_colors = [node_colors_map.get(n, '#E85A71') for n in goal_nodes_present]
    nx.draw_networkx_nodes(G, pos, nodelist=goal_nodes_present, node_color=goal_colors,
                           node_size=[node_sizes.get(n, 400) + 200 for n in goal_nodes_present], alpha=0.95)

    # wrap long labels so they don't extend off the canvas
    labels_wrapped = {}
    wrap_width = 20
    for n in simpleG.nodes():
        # show shorter labels for goal nodes
        lab = n
        if isinstance(lab, str):
            # skip labeling the central GOAL node
            if lab == 'GOAL' or lab.startswith('GOAL:'):
                labels_wrapped[n] = ''
            else:
                parts = lab.split()
                if len(parts) >= 2:
                    first = parts[0]
                    rest = ' '.join(parts[1:])
                    rest_wrapped = textwrap.fill(rest, width=wrap_width)
                    labels_wrapped[n] = first + '\n' + rest_wrapped
                else:
                    labels_wrapped[n] = textwrap.fill(lab, width=wrap_width)
        else:
            labels_wrapped[n] = str(lab)

    nx.draw_networkx_labels(simpleG, pos, labels=labels_wrapped, font_size=9,)

    # edge labels: join multiple movie titles and wrap them too
    # edge_labels = {}
    # for u,v,d in simpleG.edges(data=True):
    #     joined = '; '.join(d['labels'])
    #     edge_labels[(u,v)] = textwrap.fill(joined, width=wrap_width)

    # nx.draw_networkx_edge_labels(simpleG, pos, edge_labels=edge_labels, font_size=7)

    plt.axis('off')
    plt.title('Actor network — films label edges; goal movies in red center')
    # add legend for distances
    legend_elements = []
    if maxd >= 1:
        for d in range(1, maxd+1):
            if maxd > 1:
                val = (d - 1) / (maxd - 1)
                color = to_hex(cmap(1.0 - val))
            else:
                color = to_hex(cmap(0.5))
            label = f"Distance {d}"
            legend_elements.append(Patch(facecolor=color, edgecolor='k', label=label))
    legend_elements.append(Patch(facecolor='#999999', edgecolor='k', label='Disconnected'))
    legend_elements.append(Patch(facecolor='#E85A71', edgecolor='k', label='Goal'))
    plt.legend(handles=legend_elements, loc='upper right', framealpha=0.9)
    plt.tight_layout()
    plt.savefig(out_path, dpi=150)
    print(f'Saved static graph to {out_path}')


def draw_pyvis(G, goal_nodes, out_path):
    net = Network(height='900px', width='1200px', bgcolor='#ffffff', font_color='black')
    # add nodes
    for n,d in G.nodes(data=True):
        if d.get('type') == 'goal':
                # create a single unlabeled central node
                net.add_node(n, label='', color='#E85A71', size=36)
        else:
            net.add_node(n, label=n, color='#4C8BF5', size=18)

    # add edges with title showing movie titles
    for u,v,edata in G.edges(data=True):
        title = edata.get('movie_title') or ''
        net.add_edge(u, v, title=title)

    # center goal nodes by moving them to center with physics disabled for them
    net.repulsion(node_distance=200)
    net.show_buttons(filter_=['interaction'])
    net.show(out_path)
    print(f'Wrote interactive graph to {out_path}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--csv', help='path to actor_connections.csv', default='actor_connections.csv')
    p.add_argument('--json', help='JSON file with paths (overrides csv)')
    p.add_argument('--out', help='output file (.png or .html)', default='graph.png')
    args = p.parse_args()

    rows = None
    if args.json:
        rows = parse_json(args.json)
    elif os.path.exists(args.csv):
        rows = parse_csv(args.csv)
    else:
        raise SystemExit('No input found: provide --json or ensure actor_connections.csv exists')

    G, goals = build_graph(rows)
    if args.out.lower().endswith('.html') and HAS_PYVIS:
        draw_pyvis(G, goals, args.out)
    else:
        draw_static(G, goals, args.out)


if __name__ == '__main__':
    main()
