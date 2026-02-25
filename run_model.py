import os
import smtplib
import ssl
import pandas as pd
import numpy as np
from datetime import datetime
from nba_api.stats.endpoints import scoreboardv2, leaguedashplayerstats, leaguedashteamstats, leaguedashptstats

print("Script started...")
    # ----------------------------
    # ENV VARIABLES
    # ----------------------------
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")

    # ----------------------------
    # GET TODAY'S GAMES
    # ----------------------------
def get_today_games():
    today = datetime.today().strftime('%m/%d/%Y')
    scoreboard = scoreboardv2.ScoreboardV2(game_date=today, timeout=60)
    games = scoreboard.game_header.get_data_frame()
    return games[['HOME_TEAM_ID', 'VISITOR_TEAM_ID']]

def get_player_stats():
    # ADVANCED PLAYER STATS
    players_adv = leaguedashplayerstats.LeagueDashPlayerStats(
        season='2025-26',
        per_mode_detailed='PerGame',
        measure_type_detailed_defense='Advanced'
    )

    adv_df = players_adv.get_data_frames()[0]

    # Keep starters approximation (top 5 minutes per team)
    adv_df = adv_df.sort_values("MIN", ascending=False)
    starters = adv_df.groupby("TEAM_ID").head(5)

    return starters[[
        "PLAYER_NAME",
        "TEAM_ID",
        "MIN",
        "USG_PCT",
        "TS_PCT"
    ]]

    return starters[['PLAYER_NAME', 'TEAM_ID', 'MIN', 'USG_PCT', 'FG3A', 'FGA']]

    # ----------------------------
    # GET TEAM DEFENSE
    # ----------------------------
def get_team_defense():
    teams = leaguedashteamstats.LeagueDashTeamStats(
        season='2025-26',
        measure_type_detailed_defense='Advanced'
    )

    df = teams.get_data_frames()[0]

    return df[[
        "TEAM_ID",
        "DEF_RATING",
        "PACE"
    ]]
def calculate_edges(players, defenses, games):
    results = []

    league_def_rating_avg = defenses["DEF_RATING"].mean()
    league_pace_avg = defenses["PACE"].mean()

    for _, game in games.iterrows():
        home = game["HOME_TEAM_ID"]
        away = game["VISITOR_TEAM_ID"]

        matchups = [(home, away), (away, home)]

        for team_id, opp_id in matchups:
            team_players = players[players["TEAM_ID"] == team_id]
            opp_def = defenses[defenses["TEAM_ID"] == opp_id]

            if opp_def.empty:
                continue

            opp_def_rating = opp_def["DEF_RATING"].values[0]
            opp_pace = opp_def["PACE"].values[0]

            for _, player in team_players.iterrows():
                usage = player["USG_PCT"]
                minutes = player["MIN"]

                def_edge = (opp_def_rating - league_def_rating_avg) * -1
                pace_edge = (opp_pace - league_pace_avg)

                edge_score = (
    (usage * 0.6) +
    (def_edge * 0.25) +
    (pace_edge * 0.15)
    )

                results.append({
                    "Player": player["PLAYER_NAME"],
                    "Team_ID": team_id,
                    "Edge_Score": round(edge_score, 2)
                })

    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values("Edge_Score", ascending=False)

    return results_df

    # ----------------------------
    # EMAIL REPORT
    # ----------------------------
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

def send_email(report_df):
    top5 = report_df.head(5)

    body = "Top 5 Matchup Edges (Starters Only)\n\n"

    for _, row in top5.iterrows():
        body += f"{row['Player']} — Edge Score: {row['Edge_Score']}\n"

    msg = MIMEMultipart()
    msg["From"] = EMAIL_ADDRESS
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = f"NBA Matchup Report - {datetime.today().strftime('%b %d')}"

    msg.attach(MIMEText(body, "plain", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.sendmail(EMAIL_ADDRESS, RECIPIENT_EMAIL, msg.as_string())

    # ----------------------------
    # MAIN
    # ----------------------------
def main():

    print("Pulling today's slate...")
    games = get_today_games()

    print("Pulling player stats...")
    players = get_player_stats()

    print("Pulling team defense...")
    defenses = get_team_defense()

    print("Calculating edges...")
    results = calculate_edges(players, defenses, games)

    print("Sending email...")
    send_email(results)

    print("Done.")
