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
# Manual injury override list
OUT_PLAYERS = [
    "Stephen Curry",
    "Shai Gilgeous-Alexander"
]
# Star impact mapping (teams that lose high-minute players)
STAR_IMPACT_TEAMS = {
    "Stephen Curry": 1610612744,  # Warriors
    "Shai Gilgeous-Alexander": 1610612760  # Thunder
}
def get_today_games():
    today = datetime.today().strftime('%m/%d/%Y')

    try:
        print("Calling scoreboard...")
        scoreboard = scoreboardv3.ScoreboardV3(game_date=today, timeout=10)

        # Get the games header table
        games_df = scoreboard.get_data_frames()[0]

        # Team info table (contains team IDs)
        teams_df = scoreboard.get_data_frames()[2]

        print("Scoreboard pulled.")

        return teams_df

    except Exception as e:
        print("Scoreboard failed:", e)
        return pd.DataFrame()


def get_player_stats():

    # Season stats
    season = leaguedashplayerstats.LeagueDashPlayerStats(
        season='2025-26',
        season_type_all_star='Regular Season',
        per_mode_detailed='PerGame'
    )
    season_df = season.get_data_frames()[0]

    # Tracking stats (shot profile)
    tracking = leaguedashplayerstats.LeagueDashPlayerStats(
        season='2025-26',
        season_type_all_star='Regular Season',
        measure_type_detailed_defense='Tracking',
        per_mode_detailed='PerGame'
    )
    tracking_df = tracking.get_data_frames()[0]

    # Last 10 stats
    last10 = leaguedashplayerstats.LeagueDashPlayerStats(
        season='2025-26',
        season_type_all_star='Regular Season',
        per_mode_detailed='PerGame',
        last_n_games=10
    )
    last10_df = last10.get_data_frames()[0]

    # Merge season + last10
    df = season_df.merge(
        last10_df[["PLAYER_ID", "MIN", "PTS"]],
        on="PLAYER_ID",
        suffixes=("_SEASON", "_L10")
    )

    # Merge tracking stats
    df = df.merge(
        tracking_df[[
            "PLAYER_ID",
            "CATCH_SHOOT_FGA",
            "PULL_UP_FGA",
            "DRIVES"
        ]],
        on="PLAYER_ID",
        how="left"
    )

    # Keep rotation players
    df = df[df["MIN_SEASON"] > 15]

    return df[[
        "PLAYER_NAME",
        "TEAM_ID",
        "MIN_SEASON",
        "PTS_SEASON",
        "MIN_L10",
        "PTS_L10",
        "CATCH_SHOOT_FGA",
        "PULL_UP_FGA",
        "DRIVES"
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

    playing_teams = set(games["teamId"])

    for _, player in players.iterrows():
        if player["PLAYER_NAME"] in OUT_PLAYERS:
            continue

        if player["TEAM_ID"] not in playing_teams:
            continue

        defense = defenses[defenses["TEAM_ID"] != player["TEAM_ID"]].mean()

        # --- Blended Minutes ---
        projected_min = (
            (player["MIN_SEASON"] * 0.6) +
            (player["MIN_L10"] * 0.4)
        )

        # Minutes boost if star teammate is OUT
        for out_player in OUT_PLAYERS:
            if out_player in STAR_IMPACT_TEAMS:
                if player["TEAM_ID"] == STAR_IMPACT_TEAMS[out_player]:
                    projected_min *= 1.08  # 8% boost
        
        # --- Scoring Rates ---
        season_ppm = player["PTS_SEASON"] / player["MIN_SEASON"] if player["MIN_SEASON"] > 0 else 0
        l10_ppm = player["PTS_L10"] / player["MIN_L10"] if player["MIN_L10"] > 0 else 0

        blended_ppm = (season_ppm * 0.6) + (l10_ppm * 0.4)

        # --- Matchup Adjustments ---
        pace_multiplier = defense["PACE"] / league_avg_pace
        defense_multiplier = league_avg_def / defense["DEF_RATING"]

        projected_points = projected_min * blended_ppm * pace_multiplier * defense_multiplier

        results.append({
            "Player": player["PLAYER_NAME"],
            "Team_ID": player["TEAM_ID"],
            "Projected_Points": round(projected_points, 2)
        })

    results_df = pd.DataFrame(results)

    if results_df.empty:
        return results_df

    results_df = results_df.sort_values("Projected_Points", ascending=False)
    # Keep top 3 projected players per team
    results_df = (
        results_df
        .groupby("Team_ID", group_keys=False)
        .apply(lambda x: x.sort_values("Projected_Points", ascending=False).head(3))
    )
    return results_df


def send_email(report_df):

    if report_df.empty:
        body = "No games or data available today."
    else:
        top5 = report_df.head(5)

        body = "Top 5 Matchup Edges\n\n"

        for _, row in top5.iterrows():
            body += f"{row['Player']} — Projected Points: {row['Projected_Points']}\n"

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
