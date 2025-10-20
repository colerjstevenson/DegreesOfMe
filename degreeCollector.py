# imports list of actors and then finds a connection for each using DegreesOfMe3.py
# stores the connections their length for each actor in a csv file


import asyncio
from DegreesOfMe3 import find_actor_to_any_goal_async

# import list of actors from actors.txt
def get_list():
    with open("actors.txt", "r") as f:
        return [line.strip() for line in f.readlines()]

def find_actor_connections(actor_name, goal_titles, excluded_ids):
    return asyncio.run(find_actor_to_any_goal_async(actor_name, goal_titles, excluded_ids))
    


def main():
    actors = get_list()
    print(f"Loaded {len(actors)} actors from actors.txt")
    goal_titles = ["The Bad Guys 2", "Ghostbusters: Frozen Empire"]
    excluded_ids = set()

    results = []
    for actor in actors:
        print(f"Finding connection for actor: {actor}")
        path = find_actor_connections(actor, goal_titles, excluded_ids)
        if path[0] is not None:
            path_length = (len(path[0]) // 2) - 1  # each step is actor-movie pair
            results.append((actor, path_length, path))
            print(f"Found connection of length {path_length} for actor {actor}")
            print(f"Path: {path}")
        else:
            results.append((actor, None))
            print(f"No connection found for actor {actor}")
    
    # Write results to CSV
    with open("actor_connections.csv", "w") as f:
        f.write("Actor,Path Length,Path\n")
        for result in results:
            actor = result[0]
            path_length = result[1]
            path = result[2] if len(result) > 2 else ""
            f.write(f'"{actor}",{path_length},"{path}"\n')
            
if __name__ == "__main__":
    main()