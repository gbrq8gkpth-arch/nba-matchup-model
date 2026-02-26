import os
import ssl
import smtplib
import pandas as pd
from datetime import datetime

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from nba_api.stats.endpoints import scoreboardv2, leaguedashplayerstats, leaguedashteamstats

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

    scoreboard = scoreboardv2.ScoreboardV2(game_date=today, timeout=60)

    # Line score table (contains TEAM_ID)
    line_score = scoreboard.get_data_frames()[1]

    playing_teams = line_score["TEAM_ID"].unique()

    return pd.DataFrame({"TEAM_ID": playing_teams})


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
    "PTS_L10",
    "FGA",
    "FG3A"
]]

def get_team_defense():

    from nba_api.stats.endpoints import leaguedashteamstats

    # Advanced defense metrics
    advanced = leaguedashteamstats.LeagueDashTeamStats(
        season='2025-26',
        measure_type_detailed_defense='Advanced'
    )

    adv_df = advanced.get_data_frames()[0]

    # Opponent shooting metrics
    opponent = leaguedashteamstats.LeagueDashTeamStats(
        season='2025-26',
        measure_type_detailed_defense='Opponent'
    )

    opp_df = opponent.get_data_frames()[0]

    # Merge both on TEAM_ID
    df = adv_df.merge(
        opp_df[["TEAM_ID", "OPP_FG3A", "OPP_FG3_PCT"]],
        on="TEAM_ID",
        how="left"
    )

    return df[[
        "TEAM_ID",
        "DEF_RATING",
        "PACE",
        "OPP_FG3A",
        "OPP_FG3_PCT"
    ]]


def calculate_edges(players, defenses, matchups):

    print("Calculating edges...")

    results = []

    OUT_PLAYER_IDS = []  # keep if using manual OUT filtering

    # Teams playing today
    playing_teams = set(matchups["TEAM_ID"])

    # League averages
    league_avg_def = defenses["DEF_RATING"].mean()
    league_avg_pace = defenses["PACE"].mean()

    # --- LOOP BY TEAM ---
    for team_id in playing_teams:

        team_players = players[players["TEAM_ID"] == team_id]

        if team_players.empty:
            continue

        # --- Keep Top 2 Usage Players Only ---
        team_players = team_players.sort_values(
            by="FGA",
            ascending=False
        ).head(2)

        for _, player in team_players.iterrows():

            if player["PLAYER_ID"] in OUT_PLAYER_IDS:
                continue

            # --- Minutes Projection ---
            projected_min = (
                (player["MIN_SEASON"] * 0.6) +
                (player["MIN_L10"] * 0.4)
            )

            # --- Points Per Minute ---
            if player["MIN_SEASON"] > 0:
                pts_per_min = player["PTS_SEASON"] / player["MIN_SEASON"]
            else:
                pts_per_min = 0

            # --- Base Projection ---
            projected_points = projected_min * pts_per_min

            # --- Opponent Info ---
            opp = defenses[defenses["TEAM_ID"] != team_id]

            if opp.empty:
                continue

            # We need the actual opponent team ID
            # matchups should map TEAM_ID to OPP_TEAM_ID
            opp_team_id = matchups[matchups["TEAM_ID"] == team_id]["OPP_TEAM_ID"].values[0]

            opp_row = defenses[defenses["TEAM_ID"] == opp_team_id]

            if opp_row.empty:
                continue

            opp_def_rating = opp_row["DEF_RATING"].values[0]
            opp_pace = opp_row["PACE"].values[0]

            # --- MATCHUP SCORE ---
            matchup_score = 0

            # Usage impact
            usage = player.get("USG_PCT", 20)
            matchup_score += (usage - 20) * 0.1

            # Defense weakness
            def_diff = league_avg_def - opp_def_rating
            matchup_score += def_diff * 0.05

            # Pace environment
            pace_diff = opp_pace - league_avg_pace
            matchup_score += pace_diff * 0.05

            results.append({
                "Player": player["PLAYER_NAME"],
                "Projected_Points": round(projected_points, 2),
                "Projected_Minutes": round(projected_min, 1),
                "Matchup_Score": round(matchup_score, 2)
            })

    results_df = pd.DataFrame(results)

    if results_df.empty:
        return results_df

    # Sort by matchup advantage
    results_df = results_df.sort_values(
        by="Matchup_Score",
        ascending=False
    )

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
