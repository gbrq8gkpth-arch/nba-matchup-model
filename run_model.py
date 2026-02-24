from nba_api.stats.endpoints import scoreboardv2
import pandas as pd
from datetime import datetime

def get_today_games():
    today = datetime.today().strftime('%m/%d/%Y')
    scoreboard = scoreboardv2.ScoreboardV2(game_date=today)
    games = scoreboard.game_header.get_data_frame()
    return games[['GAME_ID', 'HOME_TEAM_ID', 'VISITOR_TEAM_ID']]

def main():
    games = get_today_games()
    print("Today's Games:")
    print(games)
    games.to_csv("daily_games.csv", index=False)

if __name__ == "__main__":
    main()
