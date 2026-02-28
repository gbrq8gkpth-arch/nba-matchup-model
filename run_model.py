import pandas as pd
import datetime
import smtplib
from email.mime.text import MIMEText

from nba_api.stats.endpoints import (
    scoreboardv3,
    leaguedashplayerstats,
    leaguedashteamstats
)

############################
# CONFIG
############################

SEASON = "2025-26"
SEASON_TYPE = "Regular Season"

OUT_PLAYERS = [
    "Deni Avdija",
    "Lauri Markkanen"
]  # Manually add players here if needed

############################
# GET TODAY'S MATCHUPS
############################

def get_today_matchups():

    from datetime import datetime
    from zoneinfo import ZoneInfo
    from nba_api.stats.endpoints import scoreboardv3
    import pandas as pd

    # ---- Kansas Time ----
    central = ZoneInfo("America/Chicago")
    today = datetime.now(central).strftime("%m/%d/%Y")

    print("Kansas Date Used:", today)

    # ---- Pull Scoreboard ----
    scoreboard = scoreboardv3.ScoreboardV3(
        game_date=today,
        timeout=60
    )

    data_frames = scoreboard.get_data_frames()

    # Table 2 = team-level rows (two per game)
    games = data_frames[2]

    if games.empty:
        print("No games today.")
        return pd.DataFrame(columns=["TEAM_ID", "OPP_TEAM_ID"])

    matchups = []

    # Build matchup pairs from team rows
    for game_id, group in games.groupby("gameId"):

        if len(group) != 2:
            continue

        team_ids = group["teamId"].values

        team_a = team_ids[0]
        team_b = team_ids[1]

        matchups.append({"TEAM_ID": team_a, "OPP_TEAM_ID": team_b})
        matchups.append({"TEAM_ID": team_b, "OPP_TEAM_ID": team_a})

    return pd.DataFrame(matchups)

from nba_api.stats.endpoints import commonplayerinfo

def get_player_stats():

    # --- Base stats (PTS, MIN, GP, AST, REB, etc.) ---
    base = leaguedashplayerstats.LeagueDashPlayerStats(
        season=SEASON,
        season_type_all_star=SEASON_TYPE,
        measure_type_detailed_defense="Base",
        per_mode_detailed="PerGame",
        timeout=60
    ).get_data_frames()[0]

    # --- Advanced stats (USG_PCT) ---
    advanced = leaguedashplayerstats.LeagueDashPlayerStats(
        season=SEASON,
        season_type_all_star=SEASON_TYPE,
        measure_type_detailed_defense="Advanced",
        per_mode_detailed="PerGame",
        timeout=60
    ).get_data_frames()[0]

    # Merge USG_PCT into base stats
    players = base.merge(
        advanced[["PLAYER_ID", "USG_PCT"]],
        on="PLAYER_ID",
        how="left"
    )

    return players
def get_team_defense():

    from nba_api.stats.endpoints import leaguedashteamstats

    # --- Base Defense Stats ---
    base_defense = leaguedashteamstats.LeagueDashTeamStats(
        season=SEASON,
        season_type_all_star=SEASON_TYPE,
        measure_type_detailed_defense="Base",
        per_mode_detailed="PerGame",
        timeout=60
    ).get_data_frames()[0]

    # --- Advanced Stats (for Pace + Defensive Rating) ---
    advanced_defense = leaguedashteamstats.LeagueDashTeamStats(
        season=SEASON,
        season_type_all_star=SEASON_TYPE,
        measure_type_detailed_defense="Advanced",
        per_mode_detailed="PerGame",
        timeout=60
    ).get_data_frames()[0]

    # Merge what we need
    defenses = base_defense.merge(
        advanced_defense[["TEAM_ID", "PACE", "DEF_RATING"]],
        on="TEAM_ID",
        how="left"
    )

    # Keep only relevant columns
    defenses = defenses[[
        "TEAM_ID",
        "TEAM_NAME",
        "PTS",         # Points allowed per game
        "FGA",         # Opponent field goal attempts allowed
        "FG3A",        # Opponent 3-point attempts allowed
        "PACE",
        "DEF_RATING"
    ]]

    print("DEFENSE COLUMNS:")
    print(defenses.columns)

    return defenses

def calculate_projections(players, defenses, matchups):

    results = []

    teams_today = matchups["TEAM_ID"].unique()

    players = players[players["TEAM_ID"].isin(teams_today)]

    if OUT_PLAYERS:
        players = players[~players["PLAYER_NAME"].isin(OUT_PLAYERS)]

    players = players.copy()

    # ---- Assign Functional Role ----
    def assign_role(row):
        if row["REB"] >= 8:
            return "Big"
        elif row["AST"] >= 5:
            return "Guard"
        else:
            return "Wing"

    players["ROLE"] = players.apply(assign_role, axis=1)

    # ---- League Environment Averages ----
    league_avg_pace = defenses["PACE"].mean()
    league_avg_fga = defenses["FGA"].mean()
    league_avg_fg3a = defenses["FG3A"].mean()
    league_avg_2pa = (defenses["FGA"] - defenses["FG3A"]).mean()

    for team_id in teams_today:

        team_players = players[players["TEAM_ID"] == team_id]
        team_players = team_players[team_players["MIN"] >= 22]

        if team_players.empty:
            continue

        team_players["OPPORTUNITY_SCORE"] = (
            team_players["USG_PCT"] * team_players["MIN"]
        )

        top_two = (
            team_players
            .sort_values("OPPORTUNITY_SCORE", ascending=False)
            .head(2)
        )

        opp_team_id = matchups[
            matchups["TEAM_ID"] == team_id
        ]["OPP_TEAM_ID"].values[0]

        team_def = defenses[defenses["TEAM_ID"] == team_id]
        opp_def = defenses[defenses["TEAM_ID"] == opp_team_id]

        team_pace = team_def["PACE"].values[0]
        opp_pace = opp_def["PACE"].values[0]

        opp_fga = opp_def["FGA"].values[0]
        opp_fg3a = opp_def["FG3A"].values[0]
        opp_2pa = opp_fga - opp_fg3a

        pace_multiplier = ((team_pace + opp_pace) / 2) / league_avg_pace
        volume_multiplier = opp_fga / league_avg_fga

        for _, player in top_two.iterrows():

            minutes = player["MIN"]
            points = player["PTS"]
            role = player["ROLE"]

            if minutes == 0:
                continue

            ppm = points / minutes
            base_projection = ppm * minutes

            projected_points = base_projection

            # ---- Environment Layer ----
            environment_multiplier = pace_multiplier * volume_multiplier

            # Guards get perimeter boost
            if role == "Guard":
                perimeter_multiplier = opp_fg3a / league_avg_fg3a
                environment_multiplier *= perimeter_multiplier

            # Bigs get interior boost
            if role == "Big":
                paint_multiplier = opp_2pa / league_avg_2pa
                environment_multiplier *= paint_multiplier

            mismatch_score = projected_points * environment_multiplier

            results.append({
                "Player": player["PLAYER_NAME"],
                "Projected_Points": round(projected_points, 1),
                "Minutes": round(minutes, 1),
                "USG_PCT": player["USG_PCT"],
                "Role": role,
                "Mismatch_Score": round(mismatch_score, 2)
            })

    final_df = (
        pd.DataFrame(results)
        .sort_values("Mismatch_Score", ascending=False)
        .head(10)
        .reset_index(drop=True)
    )

    return final_df

def send_email(results):

    if results.empty:
        print("No projections generated.")
        return

    import os
    import smtplib
    from email.mime.text import MIMEText

    EMAIL = os.getenv("EMAIL_ADDRESS")
    PASSWORD = os.getenv("EMAIL_PASSWORD")

    if not EMAIL or not PASSWORD:
        raise ValueError("Email credentials not found in environment variables")

    RECIPIENTS = [
        EMAIL,
        "cwstall4@icloud.com"
    ]

    # ----- Format Email Body -----
    body = "NBA AI Model – Top Usage Mismatch Spots\n\n"
    body += "-" * 90 + "\n"

    for _, row in results.iterrows():
        body += (
            f"{row['Player']:<24}"
            f"  Proj: {row['Projected_Points']:.1f}"
            f"  Min: {row['Minutes']:.1f}"
            f"  USG: {round(row['USG_PCT']*100,1)}%"
            f"  Mismatch: {row['Mismatch_Score']:.2f}\n"
        )

    # ----- Build Message -----
    msg = MIMEText(body)
    msg["Subject"] = "NBA AI Model Projections"
    msg["From"] = EMAIL
    msg["To"] = ", ".join(RECIPIENTS)

    # ----- Send -----
    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(EMAIL, PASSWORD)
        server.sendmail(EMAIL, RECIPIENTS, msg.as_string())

    print("Email sent successfully to:", RECIPIENTS)

def main():
    print("Pulling matchups...")
    matchups = get_today_matchups()

    print("Pulling players...")
    players = get_player_stats()

    print("Pulling defenses...")
    defenses = get_team_defense()

    print("Calculating projections...")
    results = calculate_projections(players, defenses, matchups)

    print(results)

    send_email(results)

if __name__ == "__main__":
    main()
