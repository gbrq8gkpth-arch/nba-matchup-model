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

def get_player_stats():
    # Pull Base stats (for PTS, MIN)
    base = leaguedashplayerstats.LeagueDashPlayerStats(
        season=SEASON,
        season_type_all_star=SEASON_TYPE,
        measure_type_detailed_defense="Base",
        per_mode_detailed="PerGame",
        timeout=60
    ).get_data_frames()[0]

    # Pull Advanced stats (for USG_PCT)
    advanced = leaguedashplayerstats.LeagueDashPlayerStats(
        season=SEASON,
        season_type_all_star=SEASON_TYPE,
        measure_type_detailed_defense="Advanced",
        per_mode_detailed="PerGame",
        timeout=60
    ).get_data_frames()[0]

    if "USG_PCT" not in advanced.columns:
        raise ValueError("USG_PCT missing from advanced stats")

    # Merge on PLAYER_ID
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

    for team_id in teams_today:

        team_players = players[players["TEAM_ID"] == team_id]

        # ---- Rotation Filter (removes garbage players) ----
        team_players = team_players[team_players["MIN"] >= 22]

        if team_players.empty:
            continue

        # ---- Opportunity Score (usage × minutes) ----
        team_players = team_players.copy()
        team_players["OPPORTUNITY_SCORE"] = (
            team_players["USG_PCT"] * team_players["MIN"]
        )

        # ---- Select top 2 offensive engines per team ----
        top_two = (
            team_players
            .sort_values("OPPORTUNITY_SCORE", ascending=False)
            .head(2)
        )

        # ---- Get Opponent ----
        opp_team_id = matchups[
            matchups["TEAM_ID"] == team_id
        ]["OPP_TEAM_ID"].values[0]

        team_def = defenses[defenses["TEAM_ID"] == team_id]
        opp_def = defenses[defenses["TEAM_ID"] == opp_team_id]

        if team_def.empty or opp_def.empty:
            continue

        team_pace = team_def["PACE"].values[0]
        opp_pace = opp_def["PACE"].values[0]
        opp_def_rating = opp_def["DEF_RATING"].values[0]

        league_avg_def = defenses["DEF_RATING"].mean()
        league_avg_pace = defenses["PACE"].mean()

        for _, player in top_two.iterrows():

            minutes = player["MIN"]
            points = player["PTS"]

            if minutes == 0:
                continue

            ppm = points / minutes
            base_projection = ppm * minutes

            pace_multiplier = ((team_pace + opp_pace) / 2) / league_avg_pace
            defense_multiplier = league_avg_def / opp_def_rating

            projected_points = base_projection * pace_multiplier * defense_multiplier

            results.append({
                "Player": player["PLAYER_NAME"],
                "Projected_Points": round(projected_points, 1),
                "Minutes": round(minutes, 1),
                "USG_PCT": player["USG_PCT"]
            })

    # ---- Final Ranking (Top 10 Only) ----
    final_df = (
        pd.DataFrame(results)
        .sort_values("Projected_Points", ascending=False)
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

    # Send to both emails
    RECIPIENTS = [
        EMAIL,                     # Gmail from environment
        "cwstall4@icloud.com"      # Your second email
    ]

    # -------- Format Email Body --------
    body = "NBA AI Model – Top Usage Projections\n\n"
    body += "-" * 70 + "\n"

    for _, row in results.iterrows():
        body += (
            f"{row['Player']:<20}"
            f"  Proj: {row['Projected_Points']:.1f}"
            f"  Min: {row['Minutes']:.1f}"
            f"  USG: {round(row['USG_PCT']*100,1)}%\n"
        )

    # -------- Build Message --------
    msg = MIMEText(body)
    msg["Subject"] = "NBA AI Model Projections"
    msg["From"] = EMAIL
    msg["To"] = ", ".join(RECIPIENTS)

    # -------- Send --------
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
