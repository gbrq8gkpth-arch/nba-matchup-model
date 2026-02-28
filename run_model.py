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
    teams = leaguedashteamstats.LeagueDashTeamStats(
        season=SEASON,
        season_type_all_star=SEASON_TYPE,
        measure_type_detailed_defense="Advanced",
        per_mode_detailed="PerGame",
        timeout=60
    ).get_data_frames()[0]

    return teams[["TEAM_ID", "DEF_RATING", "PACE"]]

############################
# CALCULATE PROJECTIONS
############################

def calculate_projections(players, defenses, matchups):

    results = []

    teams_today = matchups["TEAM_ID"].unique()

    # Filter to only teams playing today
    players = players[players["TEAM_ID"].isin(teams_today)]

    # Remove manually marked OUT players
    if OUT_PLAYERS:
        players = players[~players["PLAYER_NAME"].isin(OUT_PLAYERS)]

    # ---- Create Functional Role ----
    players = players.copy()

    def assign_role(row):
        if row["AST"] >= 5:
            return "Guard"
        elif row["REB"] >= 8:
            return "Big"
        else:
            return "Wing"

    players["ROLE"] = players.apply(assign_role, axis=1)

    # ---- League averages by role ----
    league_role_avg_pts = players.groupby("ROLE")["PTS"].mean().to_dict()

    for team_id in teams_today:

        team_players = players[players["TEAM_ID"] == team_id]

        # Rotation filter
        team_players = team_players[team_players["MIN"] >= 22]

        if team_players.empty:
            continue

        # Opportunity score
        team_players["OPPORTUNITY_SCORE"] = (
            team_players["USG_PCT"] * team_players["MIN"]
        )

        top_two = (
            team_players
            .sort_values("OPPORTUNITY_SCORE", ascending=False)
            .head(2)
        )

        # Opponent
        opp_team_id = matchups[
            matchups["TEAM_ID"] == team_id
        ]["OPP_TEAM_ID"].values[0]

        opp_players = players[players["TEAM_ID"] == opp_team_id]

        for _, player in top_two.iterrows():

            minutes = player["MIN"]
            points = player["PTS"]
            role = player["ROLE"]

            if minutes == 0:
                continue

            ppm = points / minutes
            base_projection = ppm * minutes

            # ---- Pace Only (remove defense from base) ----
            team_def = defenses[defenses["TEAM_ID"] == team_id]
            opp_def = defenses[defenses["TEAM_ID"] == opp_team_id]

            team_pace = team_def["PACE"].values[0]
            opp_pace = opp_def["PACE"].values[0]
            league_avg_pace = defenses["PACE"].mean()

            pace_multiplier = ((team_pace + opp_pace) / 2) / league_avg_pace

            projected_points = base_projection * pace_multiplier

            # ---- Role Weakness Factor ----
            opp_role_pts_allowed = opp_players[
                opp_players["ROLE"] == role
            ]["PTS"].mean()

            league_role_pts = league_role_avg_pts.get(role, 1)

            if league_role_pts == 0:
                weakness_factor = 1
            else:
                weakness_factor = opp_role_pts_allowed / league_role_pts

            mismatch_score = projected_points * weakness_factor

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
