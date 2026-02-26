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

OUT_PLAYERS = []  # Manually add players here if needed

############################
# GET TODAY'S MATCHUPS
############################

def get_today_matchups():
    today = datetime.datetime.now().strftime("%m/%d/%Y")

    scoreboard = scoreboardv3.ScoreboardV3(
        game_date=today,
        timeout=60
    )

    games = scoreboard.get_data_frames()[0]

    matchups = []

    for _, row in games.iterrows():
        home = row["HOME_TEAM_ID"]
        away = row["VISITOR_TEAM_ID"]

        matchups.append({"TEAM_ID": home, "OPP_TEAM_ID": away})
        matchups.append({"TEAM_ID": away, "OPP_TEAM_ID": home})

    return pd.DataFrame(matchups)

############################
# GET PLAYER STATS
############################

def get_player_stats():
    players = leaguedashplayerstats.LeagueDashPlayerStats(
        season=SEASON,
        season_type_all_star=SEASON_TYPE,
        measure_type_detailed_defense="Advanced",
        per_mode_detailed="PerGame",
        timeout=60
    ).get_data_frames()[0]

    if "USG_PCT" not in players.columns:
        raise ValueError("USG_PCT not found in player stats")

    return players

############################
# GET TEAM DEFENSE
############################

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

    players = players[players["TEAM_ID"].isin(teams_today)]
    players = players[~players["PLAYER_NAME"].isin(OUT_PLAYERS)]

    for team_id in teams_today:

        team_players = players[players["TEAM_ID"] == team_id]

        if team_players.empty:
            continue

        top_two = (
            team_players
            .sort_values("USG_PCT", ascending=False)
            .head(2)
        )

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
                "Team_ID": team_id,
                "USG_PCT": round(player["USG_PCT"], 2),
                "Minutes": round(minutes, 1),
                "Base_Points": round(base_projection, 1),
                "Projected_Points": round(projected_points, 1)
            })

    return pd.DataFrame(results).sort_values(
        "Projected_Points",
        ascending=False
    )

############################
# EMAIL RESULTS
############################

def send_email(results):

    if results.empty:
        print("No projections generated.")
        return

    body = results.to_string(index=False)

    msg = MIMEText(body)
    msg["Subject"] = "NBA AI Model Projections"
    msg["From"] = "your_email@gmail.com"
    msg["To"] = "your_email@gmail.com"

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login("your_email@gmail.com", "your_app_password")
        server.send_message(msg)

############################
# MAIN
############################

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
