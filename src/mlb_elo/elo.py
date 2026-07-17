"""Elo rating engine for MLB teams.

Follows the general shape of FiveThirtyEight's sports Elo systems: a base
K-factor scaled by a margin-of-victory multiplier (derived from run
differential), a fixed home-field bump, and between-season regression to the
mean so a team's rating carries over but isn't fully "sticky" year to year.

No starting-pitcher or park adjustment yet — this is team-strength-from-
results only. Those come later as adjustments layered on top of expected
win probability.
"""
from dataclasses import dataclass, field
import math

DEFAULT_RATING = 1500.0
HOME_ADVANTAGE = 24.0
K_BASE = 4.0
SEASON_REGRESSION = 1.0 / 3.0  # fraction reverted toward the mean each new season


def expected_win_prob(rating_a: float, rating_b: float) -> float:
    """Probability that team A beats team B, given their current ratings."""
    return 1.0 / (1.0 + 10 ** (-(rating_a - rating_b) / 400.0))


def mov_multiplier(elo_diff: float, run_diff: int) -> float:
    """Scale the rating update by how lopsided the game was.

    Larger blowouts move ratings more, but the effect is damped when the
    winner was already a big favorite (an expected blowout is less
    informative than a surprising one). Same functional form 538 uses
    across sports; run_diff is baseball's analogue of point margin.
    """
    return math.log(abs(run_diff) + 1) * (2.2 / (abs(elo_diff) * 0.001 + 2.2))


@dataclass
class EloEngine:
    ratings: dict[int, float] = field(default_factory=dict)
    team_names: dict[int, str] = field(default_factory=dict)
    _current_season: int | None = None

    def get_rating(self, team_id: int) -> float:
        return self.ratings.get(team_id, DEFAULT_RATING)

    def _maybe_start_new_season(self, season: int) -> None:
        if self._current_season is not None and season != self._current_season:
            for team_id, rating in self.ratings.items():
                self.ratings[team_id] = (
                    DEFAULT_RATING + (rating - DEFAULT_RATING) * (1 - SEASON_REGRESSION)
                )
        self._current_season = season

    def process_game(
        self,
        season: int,
        home_team_id: int,
        home_team_name: str,
        away_team_id: int,
        away_team_name: str,
        home_score: int,
        away_score: int,
    ) -> None:
        self._maybe_start_new_season(season)
        self.team_names[home_team_id] = home_team_name
        self.team_names[away_team_id] = away_team_name

        home_rating = self.get_rating(home_team_id)
        away_rating = self.get_rating(away_team_id)

        expected_home = expected_win_prob(home_rating + HOME_ADVANTAGE, away_rating)
        actual_home = 1.0 if home_score > away_score else 0.0

        elo_diff = (home_rating + HOME_ADVANTAGE) - away_rating
        run_diff = home_score - away_score
        multiplier = mov_multiplier(elo_diff, run_diff)

        shift = K_BASE * multiplier * (actual_home - expected_home)

        self.ratings[home_team_id] = home_rating + shift
        self.ratings[away_team_id] = away_rating - shift

    def standings(self) -> list[tuple[str, float]]:
        return sorted(
            ((self.team_names.get(tid, str(tid)), r) for tid, r in self.ratings.items()),
            key=lambda pair: pair[1],
            reverse=True,
        )
