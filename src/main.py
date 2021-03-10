import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

import httpx
import jmespath
from dateutil.parser import isoparse

import settings

OLDBOYS_SEASON_4_ID = 103


@dataclass
class MatchData:
    id: int
    time: datetime
    map: str
    match_group: int
    team_names: Tuple[str]
    team_ids: Tuple[int]
    scores: Tuple[int]

    def result_for(self, team_id):
        if self.scores[0] == self.scores[1]:
            return "DRAW"

        return (
            "WIN" if self.score_for(team_id) > self.score_for_other(team_id) else "LOSS"
        )

    def score_for(self, team_id):
        assert team_id in self.team_ids
        return self.scores[0] if team_id == self.team_ids[0] else self.scores[1]

    def score_for_other(self, team_id):
        assert team_id in self.team_ids
        return self.scores[1] if team_id == self.team_ids[0] else self.scores[0]

    def format_for(self, team_id, longest_team_name):
        return f"{self.team_names[0]:20} vs {self.team_names[1]:20}: {self.scores[0]:>2}-{self.scores[1]:<2} ({self.result_for(team_id):>4}) [{self.map}]"


def parse_team_data(team_data):
    id = jmespath.search("id", team_data)
    name = jmespath.search("name", team_data)

    match_ids = jmespath.search(
        f"matches[?tournamentId==`{OLDBOYS_SEASON_4_ID}`].id", team_data
    )

    return id, name, match_ids


def parse_match_data(match_data):
    if not match_data["resultLocked"]:
        return None

    id = jmespath.search("id", match_data)
    time = jmespath.search("time", match_data)
    mapName = jmespath.search("mapName", match_data)

    # For Bo2, this will point to the first one -- should be used for grouping
    match_group = jmespath.search("matchGroup", match_data)

    team_names = jmespath.search("MatchTeams[*].Team.name", match_data)
    team_ids = jmespath.search("MatchTeams[*].team_id", match_data)
    scores = jmespath.search("MatchTeams[*].score", match_data)

    res = MatchData(
        id=id,
        time=isoparse(time),
        map=mapName,
        match_group=match_group,
        team_names=team_names,
        team_ids=team_ids,
        scores=scores,
    )

    return res


def get_with_cache(path: Path, url: str):
    if path.exists():
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        diff = datetime.now() - mtime
        if diff < timedelta(days=1):
            return json.load(path.open())

    r = httpx.get(url)
    assert r.status_code == 200
    path.write_bytes(r.content)

    return r.json()


def get_team_data(team_id: int):
    path = settings.TEST_CACHE_FOLDER / f"team-{team_id}.json"
    url = f"https://app.esportligaen.dk/api/team/{team_id}?includeViewInfo=true"

    data = get_with_cache(path, url)
    return parse_team_data(data)


def get_match_data(match_id: int):
    path = settings.TEST_CACHE_FOLDER / f"match-{match_id}.json"
    url = f"https://app.esportligaen.dk/api/match/details/{match_id}"

    data = get_with_cache(path, url)
    return parse_match_data(data)


if __name__ == "__main__":
    team_id = 2244
    _, name, match_ids = get_team_data(team_id)

    print(f"{name=} ({team_id=})")
    print()

    print("=== Matches ===")

    match_datas: List[MatchData] = []

    longest_team_name = 0

    for match_ids in match_ids:
        data = get_match_data(match_ids)

        # will be None if game not played yet
        if data:
            match_datas.append(data)
            longest_team_name = max(
                longest_team_name, len(data.team_names[0]), len(data.team_names[1])
            )

    match_datas.sort(key=lambda d: d.time)

    for i, match_data in enumerate(match_datas):
        print(match_data.format_for(team_id, longest_team_name))
        if i % 2 == 1:
            print()

    played = Counter()
    picks = Counter()
    wins = defaultdict(list)
    draws = defaultdict(list)
    losses = defaultdict(list)
    result_dicts = {"WIN": wins, "DRAW": draws, "LOSS": losses}
    close_games = list()
    close_games_per_map = Counter()

    CLOSE_GAME_START = 12  # if both team are at this number of rounds

    for m in match_datas:
        map = m.map

        played[map] += 1

        if m.team_ids[0] == team_id:
            picks[map] += 1

        result_dicts[m.result_for(team_id)][map].append(m.id)

        if min(*m.scores) >= CLOSE_GAME_START:
            close_games.append(m.id)
            close_games_per_map[map] += 1

    def fmt_dict_output(d: Dict, is_results=True):
        if is_results:
            key = lambda t: len(t[1]) / played[t[0]]
        else:
            key = lambda t: t[1] / played[t[0]]

        for map, item in sorted(d.items(), key=key, reverse=True):
            if not is_results:
                num_times = item
                print(f"{map:>8}: {num_times} times ({100*num_times/played[map]:.0f}%)")
            else:
                num_close_games = sum(1 if id in close_games else 0 for id in item)
                num_times = len(item)
                print(
                    f"{map:>8}: {num_times} times ({100*num_times/played[map]:>3.0f}%), of those {num_close_games} was a close games"
                )

    print("=== Played ===")
    fmt_dict_output(played, is_results=False)

    print()
    print("=== Picks ===")
    fmt_dict_output(picks, is_results=False)

    print()
    print("=== Wins ===")
    fmt_dict_output(wins)

    print()
    print("=== Draws ===")
    fmt_dict_output(draws)

    print()
    print("=== Losses ===")
    fmt_dict_output(losses)

    print()
    print(f"=== Close games (both teams have {CLOSE_GAME_START}+ rounds) ===")
    fmt_dict_output(close_games_per_map, is_results=False)
