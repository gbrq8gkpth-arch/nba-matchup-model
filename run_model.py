import os
import ssl
import smtplib
import pandas as pd
from datetime import datetime

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from nba_api.stats.endpoints import scoreboardv3, leaguedashplayerstats, leaguedashteamstats

print("Script started...")

EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL")


def get_today_games():
    today = datetime.today().strftime('%m/%d/%Y')

    try:
        print("Calling scoreboard...")
        scoreboard = scoreboardv3.ScoreboardV3(game_date=today, timeout=10)
        games_df = scoreboard.get_data_frames()[0]
        print("Scoreboard pulled.")
        return games_df
    except Exception as e:
        print("Scoreboard failed:", e)
        return pd.DataFrame()


def get_player_stats():

    # Base stats
    base = leaguedashplayerstats.LeagueDashPlayerStats(
        season='2025-26',
        season_type_all_star='Regular Season',
        per_mode_detailed='PerGame'
    )

    base_df = base.get_data_frames()[0]

    # Advanced stats
    advanced = leaguedashplayerstats.LeagueDashPlayerStats(
        season='2025-26',
        season_type_all_star='Regular Season',
        measure_type_detailed='Advanced',
        per_mode_detailed='PerGame'
    )

    adv_df = advanced.get_data_frames()[0]

    # Merge on PLAYER_ID
    df = base_df.merge(
        adv_df[["PLAYER_ID", "USG_PCT"]],
        on="PLAYER_ID"
    )

    # Keep likely starters
    starters = df[df["MIN"] > 20]

    return starters[[
        "PLAYER_NAME",
        "TEAM_ID",
        "MIN",
        "USG_PCT"
    ]]


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

    if games.empty:
        print("No games found.")
        return pd.DataFrame()

    results = []

    league_avg_def = defenses["DEF_RATING"].mean()
    league_avg_pace = defenses["PACE"].mean()

    playing_teams = set(games["HOME_TEAM_ID"]).union(set(games["VISITOR_TEAM_ID"]))

    for _, player in players.iterrows():

        if player["TEAM_ID"] not in playing_teams:
            continue

        defense = defenses[defenses["TEAM_ID"] != player["TEAM_ID"]].mean()

        usage = player["USG_PCT"]
        minutes = player["MIN"]

        def_edge = league_avg_def - defense["DEF_RATING"]
        pace_edge = defense["PACE"] - league_avg_pace

        edge_score = (
            (usage * 0.7) +
            (def_edge * 0.2) +
            (pace_edge * 0.1)
        )

        results.append({
            "Player": player["PLAYER_NAME"],
            "Team_ID": player["TEAM_ID"],
            "Edge_Score": edge_score
        })

    results_df = pd.DataFrame(results)

    if results_df.empty:
        return results_df

    results_df = results_df.sort_values("Edge_Score", ascending=False)

    # Normalize to 0–10 scale
    min_score = results_df["Edge_Score"].min()
    max_score = results_df["Edge_Score"].max()

    if max_score != min_score:
        results_df["Edge_Score"] = (
            (results_df["Edge_Score"] - min_score) /
            (max_score - min_score)
        ) * 10
    else:
        results_df["Edge_Score"] = 5

    results_df["Edge_Score"] = results_df["Edge_Score"].round(2)

    return results_df


def send_email(report_df):

    if report_df.empty:
        body = "No games or data available today."
    else:
        top5 = report_df.head(5)

        body = "Top 5 Matchup Edges\n\n"

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


if __name__ == "__main__":
    main()
