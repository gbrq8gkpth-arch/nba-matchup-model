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

    matchups = {}

    for _, row in games_df.iterrows():
        home = row["HOME_TEAM_ID"]
        away = row["VISITOR_TEAM_ID"]

        matchups[home] = away
        matchups[away] = home

    return matchups


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
        "OPP_FG3A"
    ]]


def calculate_edges(players, defenses, matchups):

    results = []

    league_avg_def = defenses["DEF_RATING"].mean()
    league_avg_pace = defenses["PACE"].mean()

    for _, player in players.iterrows():

        # Skip injured players
        if player["PLAYER_NAME"] in OUT_PLAYERS:
            continue

        # Skip if team not playing today
        if player["TEAM_ID"] not in matchups:
            continue

        opponent_id = matchups[player["TEAM_ID"]]

        opp_def = defenses[defenses["TEAM_ID"] == opponent_id]

        if opp_def.empty:
            continue

        opp_def_rating = opp_def["DEF_RATING"].values[0]
        opp_pace = opp_def["PACE"].values[0]

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

        # --- Usage Adjustment ---
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

    results_df = results_df.sort_values("Projected_Points", ascending=False)

    results_df = (
        results_df
        .groupby("Team_ID", group_keys=False)
        .apply(lambda x: x.sort_values("Projected_Points", ascending=False).head(3))
    )

    results_df = results_df.sort_values("Projected_Points", ascending=False).head(15)

    return results_df


def send_email(report_df):

    if report_df.empty:
        body = "No games or data available today."
    else:
        top5 = report_df.head(5)

        body = "Top 5 Matchup Edges\n\n"

        for _, row in top5.iterrows():
            body += (
    f"{row['Player']} — "
    f"Proj Pts: {row['Projected_Points']} | "
    f"Proj Min: {row['Projected_Minutes']}\n"
)

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
    matchups = get_today_games()

    print("Pulling player stats...")
    players = get_player_stats()

    print("Pulling team defense...")
    defenses = get_team_defense()

    print("Calculating edges...")
    results = calculate_edges(players, defenses, matchups)

    print("Sending email...")
    send_email(results)

    print("Done.")


if __name__ == "__main__":
    main()
