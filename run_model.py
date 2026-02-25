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

    from nba_api.stats.endpoints import scoreboardv2
    from datetime import datetime

    today = datetime.today().strftime('%m/%d/%Y')

    scoreboard = scoreboardv2.ScoreboardV2(game_date=today, timeout=60)

    games_df = scoreboard.get_data_frames()[0]

    # Extract unique team IDs playing today
    home_teams = games_df["HOME_TEAM_ID"].tolist()
    away_teams = games_df["VISITOR_TEAM_ID"].tolist()

    playing_teams = set(home_teams + away_teams)

    return playing_teams


def get_player_stats():

    # Season stats
    season = leaguedashplayerstats.LeagueDashPlayerStats(
        season='2025-26',
        season_type_all_star='Regular Season',
        per_mode_detailed='PerGame'
    )
    season_df = season.get_data_frames()[0]

    # Last 10 stats
    last10 = leaguedashplayerstats.LeagueDashPlayerStats(
        season='2025-26',
        season_type_all_star='Regular Season',
        per_mode_detailed='PerGame',
        last_n_games=10
    )
    last10_df = last10.get_data_frames()[0]

    df = season_df.merge(
        last10_df[["PLAYER_ID", "MIN", "PTS"]],
        on="PLAYER_ID",
        suffixes=("_SEASON", "_L10")
    )

    # Keep rotation players
    df = df[df["MIN_SEASON"] > 15]

    return df[[
        "PLAYER_NAME",
        "PLAYER_ID",
        "TEAM_ID",
        "MIN_SEASON",
        "PTS_SEASON",
        "MIN_L10",
        "PTS_L10"
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


def calculate_edges(players, defenses, playing_teams):

    results = []

    league_avg_def = defenses["DEF_RATING"].mean()
    league_avg_pace = defenses["PACE"].mean()

    for _, player in players.iterrows():
        if player["TEAM_ID"] not in playing_teams:
            continue
        # Skip injured players
        if player["PLAYER_NAME"] in OUT_PLAYERS:
            continue

        # Get opponent defensive averages (simple version)
        opp_def_rating = defenses["DEF_RATING"].mean()
        opp_pace = defenses["PACE"].mean()

        # --- Blended Minutes ---
        projected_min = (
            (player["MIN_SEASON"] * 0.6) +
            (player["MIN_L10"] * 0.4)
        )

        # --- Base scoring rate ---
        if player["MIN_SEASON"] > 0:
            pts_per_min = player["PTS_SEASON"] / player["MIN_SEASON"]
        else:
            pts_per_min = 0

        # --- Usage Adjustment (safe default if missing) ---
        usage = player.get("USG_PCT", 20)
        usage_factor = usage / 20
        usage_multiplier = 0.7 + (usage_factor * 0.3)

        adjusted_scoring_rate = pts_per_min * usage_multiplier

        # --- Defense adjustment ---
        def_factor = league_avg_def / opp_def_rating if opp_def_rating > 0 else 1

        # --- Pace adjustment ---
        pace_factor = opp_pace / league_avg_pace if league_avg_pace > 0 else 1

        # --- Final projection ---
        projected_points = projected_min * adjusted_scoring_rate * def_factor * pace_factor

        results.append({
            "Player": player["PLAYER_NAME"],
            "Team_ID": player["TEAM_ID"],
            "Projected_Points": round(projected_points, 2),
            "Projected_Minutes": round(projected_min, 1)
        })

    results_df = pd.DataFrame(results)

    if results_df.empty:
        return results_df

    # Sort by projection
    results_df = results_df.sort_values("Projected_Points", ascending=False)

    # Keep top 3 per team
    results_df = (
        results_df
        .groupby("Team_ID", group_keys=False)
        .apply(lambda x: x.sort_values("Projected_Points", ascending=False).head(3))
    )

    # Re-sort entire slate and keep top 15 overall
    results_df = results_df.sort_values("Projected_Points", ascending=False).head(15)

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
    playing_teams = get_today_games()

    print("Pulling player stats...")
    players = get_player_stats()

    print("Pulling team defense...")
    defenses = get_team_defense()

    print("Calculating edges...")
    results = calculate_edges(players, defenses, playing_teams)

    print("Sending email...")
    send_email(results)

    print("Done.")


if __name__ == "__main__":
    main()
