"""
NBA depth chart, rotation data, and minutes per game for 2025-2026 season.
Rotation data from Hoops Distillery depth charts.
MPG from last 15 games (playoffs) via LandOfBasketball, ESPN, StatMuse — more DFS-relevant than season averages.
"""

NBA_ROTATIONS = {
    "ATL": {
        "name": "Atlanta Hawks",
        "starting": ["N. Alexander-Walker", "Dyson Daniels", "CJ McCollum", "Jalen Johnson", "Onyeka Okongwu"],
        "rotation": ["Gabe Vincent", "Corey Kispert", "Buddy Hield", "Zaccharie Risacher", "Jonathan Kuminga", "Asa Newell", "Jock Landale", "Mouhamed Gueye"]
    },
    "BOS": {
        "name": "Boston Celtics",
        "starting": ["Derrick White", "Jaylen Brown", "Sam Hauser", "Jayson Tatum", "Neemias Queta"],
        "rotation": ["Payton Pritchard", "Ron Harper", "Hugo Gonzalez", "Baylor Scheierman", "Jordan Walsh", "Luka Garza", "Nikola Vucevic", "Amari Williams"]
    },
    "BKN": {
        "name": "Brooklyn Nets",
        "starting": ["Nolan Traore", "Egor Demin", "Noah Clowney", "Michael Porter Jr.", "Nic Claxton"],
        "rotation": ["Terance Mann", "Ben Saraf", "Ochai Agbaji", "Tyrese Martin", "Ziaire Williams", "Drake Powell", "Danny Wolf", "Tyson Etienne", "Day'Ron Sharpe", "Hunter Tyson", "Josh Minott"]
    },
    "CHA": {
        "name": "Charlotte Hornets",
        "starting": ["LaMelo Ball", "Brandon Miller", "Kon Knueppel", "Miles Bridges", "Moussa Diabate"],
        "rotation": ["Sion James", "Tre Mann", "Coby White", "Liam McNeeley", "Josh Green", "Pat Connaughton", "Grant Williams", "Tidjane Salaun", "Ryan Kalkbrenner", "Xavier Tillman"]
    },
    "CHI": {
        "name": "Chicago Bulls",
        "starting": ["Josh Giddey", "Anfernee Simons", "Isaac Okoro", "Matas Buzelis", "Jalen Smith"],
        "rotation": ["Tre Jones", "Rob Dillingham", "Collin Sexton", "Patrick Williams", "Guerschon Yabusele", "Leonard Miller", "Zach Collins", "Nick Richards"]
    },
    "CLE": {
        "name": "Cleveland Cavaliers",
        "starting": ["James Harden", "Donovan Mitchell", "Jaylon Tyson", "Evan Mobley", "Jarrett Allen"],
        "rotation": ["Dennis Schroder", "Craig Porter", "Keon Ellis", "Tyrese Proctor", "Dean Wade", "Sam Merrill", "Nae'Qwan Tomlin", "Larry Nance", "Thomas Bryant"]
    },
    "DAL": {
        "name": "Dallas Mavericks",
        "starting": ["Ryan Nembhard", "Max Christie", "Cooper Flagg", "P.J. Washington", "Daniel Gafford"],
        "rotation": ["Brandon Williams", "AJ Johnson", "Naji Marshall", "Klay Thompson", "Caleb Martin", "Khris Middleton", "Dwight Powell", "Marvin Bagley"]
    },
    "DEN": {
        "name": "Denver Nuggets",
        "starting": ["Jamal Murray", "Christian Braun", "Cam Johnson", "Aaron Gordon", "Nikola Jokic"],
        "rotation": ["Tyus Jones", "Jalen Pickett", "Bruce Brown", "Julian Strawther", "Tim Hardaway", "Spencer Jones", "Peyton Watson", "DaRon Holmes", "Jonas Valanciunas", "Zeke Nnaji", "KJ Simpson"]
    },
    "DET": {
        "name": "Detroit Pistons",
        "starting": ["Cade Cunningham", "Duncan Robinson", "Ausar Thompson", "Tobias Harris", "Jalen Duren"],
        "rotation": ["Caris LeVert", "Marcus Sasser", "Kevin Huerter", "Daniss Jenkins", "Ron Holland", "Chaz Lanier", "Isaiah Stewart", "Bobi Klintman", "Paul Reed"]
    },
    "GSW": {
        "name": "Golden State Warriors",
        "starting": ["Stephen Curry", "Moses Moody", "De'Anthony Melton", "Draymond Green", "Kristaps Porzingis"],
        "rotation": ["Will Richard", "Pat Spencer", "Brandin Podziemski", "Gary Payton", "Gui Santos", "Alex Toohey", "Al Horford", "Quinten Post"]
    },
    "HOU": {
        "name": "Houston Rockets",
        "starting": ["Amen Thompson", "Jabari Smith", "Tari Eason", "Kevin Durant", "Alperen Sengun"],
        "rotation": ["Reed Sheppard", "JD Davison", "Josh Okogie", "Aaron Holiday", "Jae'Sean Tate", "Dorian Finney-Smith", "Jeff Green", "Clint Capela"]
    },
    "IND": {
        "name": "Indiana Pacers",
        "starting": ["Andrew Nembhard", "Ben Sheppard", "Aaron Nesmith", "Pascal Siakam", "Ivica Zubac"],
        "rotation": ["T.J. McConnell", "Quenton Jackson", "Kam Jones", "Taelon Peter", "Jarace Walker", "Micah Potter", "Obi Toppin", "Ethan Thompson", "Kobe Brown", "Jay Huff"]
    },
    "LAC": {
        "name": "Los Angeles Clippers",
        "starting": ["Darius Garland", "Bogdan Bogdanovic", "Kawhi Leonard", "John Collins", "Brook Lopez"],
        "rotation": ["Kris Dunn", "Cam Christie", "Benn Mathurin", "Jordan Miller", "Derrick Jones", "Kobe Sanders", "Nicolas Batum", "Isaiah Jackson", "Yanic Konan Niederhauser"]
    },
    "LAL": {
        "name": "Los Angeles Lakers",
        "starting": ["Luka Doncic", "Austin Reaves", "LeBron James", "Rui Hachimura", "Deandre Ayton"],
        "rotation": ["Marcus Smart", "Bronny James", "Luke Kennard", "Dalton Knecht", "Jake LaRavia", "Adou Thiero", "Jarred Vanderbilt", "Maxi Kleber", "Jaxson Hayes", "Drew Timme"]
    },
    "MEM": {
        "name": "Memphis Grizzlies",
        "starting": ["Ja Morant", "Cedric Coward", "Jaylen Wells", "Santi Aldama", "Zach Edey"],
        "rotation": ["Ty Jerome", "Walter Clayton", "Scotty Pippen", "Cam Spencer", "Taylor Hendricks", "Rayan Rupert", "GG Jackson", "O-Max Prosper", "K. Caldwell-Pope"]
    },
    "MIA": {
        "name": "Miami Heat",
        "starting": ["Davion Mitchell", "Tyler Herro", "Norman Powell", "Andrew Wiggins", "Bam Adebayo"],
        "rotation": ["Kasparas Jakucionis", "Dru Smith", "Pelle Larsson", "Simone Fontecchio", "Jaime Jaquez", "Keshad Johnson", "Nikola Jovic", "Kel'el Ware"]
    },
    "MIL": {
        "name": "Milwaukee Bucks",
        "starting": ["Ryan Rollins", "A.J. Green", "Kyle Kuzma", "Giannis Antetokounmpo", "Myles Turner"],
        "rotation": ["Kevin Porter", "Andre Jackson", "Gary Trent", "Gary Harris", "Cam Thomas", "Taurean Prince", "Bobby Portis", "Ousmane Dieng", "Jericho Sims", "Thanasis Antetokounmpo"]
    },
    "MIN": {
        "name": "Minnesota Timberwolves",
        "starting": ["Donte DiVincenzo", "Anthony Edwards", "Jaden McDaniels", "Julius Randle", "Rudy Gobert"],
        "rotation": ["Ayo Dosunmu", "Mike Conley", "Terrence Shannon", "Bones Hyland", "Jaylen Clark", "Julian Phillips", "Naz Reid", "Joe Ingles", "Kyle Anderson", "Joan Beringer"]
    },
    "NOP": {
        "name": "New Orleans Pelicans",
        "starting": ["Dejounte Murray", "Trey Murphy", "Herb Jones", "Zion Williamson", "Derik Queen"],
        "rotation": ["Jeremiah Fears", "Jordan Poole", "Jordan Hawkins", "Saddiq Bey", "Bryce McGowens", "Micah Peavy", "Yves Missi", "Karlo Matkovic", "Kevon Looney", "Deandre Jordan"]
    },
    "NYK": {
        "name": "New York Knicks",
        "starting": ["Jalen Brunson", "Mikal Bridges", "Josh Hart", "OG Anunoby", "Karl-Anthony Towns"],
        "rotation": ["Miles McBride", "Landry Shamet", "Jordan Clarkson", "Mitchell Robinson", "Jose Alvarado", "Tyler Kolek", "Guerschon Yabusele"]
    },
    "OKC": {
        "name": "Oklahoma City Thunder",
        "starting": ["Shai Gilgeous-Alexander", "Jalen Williams", "Lu Dort", "Chet Holmgren", "Isaiah Hartenstein"],
        "rotation": ["Cason Wallace", "Nikola Topic", "Ajay Mitchell", "Aaron Wiggins", "Jared McCain", "Alex Caruso", "Isaiah Joe", "Kenrich Williams", "Jaylin Williams"]
    },
    "ORL": {
        "name": "Orlando Magic",
        "starting": ["Jalen Suggs", "Desmond Bane", "Franz Wagner", "Paolo Banchero", "Wendell Carter"],
        "rotation": ["Jase Richardson", "Jett Howard", "Anthony Black", "Tristan Da Silva", "Noah Penda", "Jonathan Isaac", "Goga Bitadze", "Mo Wagner"]
    },
    "PHI": {
        "name": "Philadelphia 76ers",
        "starting": ["Tyrese Maxey", "VJ Edgecombe", "Dom Barlow", "Kelly Oubre", "Joel Embiid"],
        "rotation": ["Quentin Grimes", "Kyle Lowry", "Justin Edwards", "Jabari Walker", "Trendon Watford", "Johni Broome", "Adem Bona", "Andre Drummond"]
    },
    "PHX": {
        "name": "Phoenix Suns",
        "starting": ["Collin Gillespie", "Devin Booker", "Jalen Green", "Royce O'Neale", "Mark Williams"],
        "rotation": ["Jordan Goodwin", "Jamaree Bouyea", "Grayson Allen", "Cole Anthony", "Ryan Dunn", "Amir Coffey", "Oso Ighodaro", "Haywood Highsmith", "Khaman Maluach", "Rasheer Fleming", "Kobe Brea", "Isaiah Livers"]
    },
    "POR": {
        "name": "Portland Trail Blazers",
        "starting": ["Jrue Holiday", "Shaedon Sharpe", "Toumani Camara", "Deni Avdija", "Donovan Clingan"],
        "rotation": ["Scoot Henderson", "Blake Wesley", "Vit Krejci", "Matisse Thybulle", "Jerami Grant", "Kris Murray", "Hansen Yang", "Sidy Cissoko", "Robert Williams"]
    },
    "SAC": {
        "name": "Sacramento Kings",
        "starting": ["Russell Westbrook", "DeMar DeRozan", "Keegan Murray", "Precious Achiuwa", "Maxime Raynaud"],
        "rotation": ["Devin Carter", "Isaiah Stevens", "Malik Monk", "Doug McDermott", "Nique Clifford", "Isaac Jones", "De'Andre Hunter", "Drew Eubanks", "Dylan Cardwell"]
    },
    "SAS": {
        "name": "San Antonio Spurs",
        "starting": ["De'Aaron Fox", "Stephon Castle", "Devin Vassell", "Julian Champagnie", "Victor Wembanyama"],
        "rotation": ["Dylan Harper", "Keldon Johnson", "Luke Kornet", "Carter Bryant"]
    },
    "TOR": {
        "name": "Toronto Raptors",
        "starting": ["Immanuel Quickley", "RJ Barrett", "Brandon Ingram", "Scottie Barnes", "Jakob Poeltl"],
        "rotation": ["Jamal Shead", "Garrett Temple", "Gradey Dick", "Ja'Kobe Walter", "Collin Murray-Boyles", "Jonathan Mogbo", "Sandro Mamukelashvili", "Trayce Jackson-Davis"]
    },
    "UTA": {
        "name": "Utah Jazz",
        "starting": ["Isaiah Collier", "Keyonte George", "Ace Bailey", "Lauri Markkanen", "Jusuf Nurkic"],
        "rotation": ["Cody Williams", "John Konchar", "Bryce Sensabaugh", "Svi Mykhailiuk", "Kyle Filipowski"]
    },
    "WAS": {
        "name": "Washington Wizards",
        "starting": ["Trae Young", "Tre Johnson", "Kyshawn George", "Justin Champagnie", "Alex Sarr"],
        "rotation": ["Bub Carrington", "Sharife Cooper", "Will Riley", "Jaden Hardy", "Cam Whitmore", "Jamir Watkins", "Bilal Coulibaly", "Anthony Gill", "Tristan Vukcevic"]
    }
}

# Minutes per game over the LAST 15 GAMES (playoffs / late season).
# More relevant for DFS than season averages — reflects current usage and rotations.
# Sources: LandOfBasketball, ESPN, StatMuse.
# Keyed by (team_abbr, player_name) for precise lookup.
ACTUAL_MPG = {
    # ── New York Knicks (last 15 games, playoffs) ──
    ("NYK", "Jalen Brunson"): 36.2,
    ("NYK", "OG Anunoby"): 33.6,
    ("NYK", "Josh Hart"): 32.6,
    ("NYK", "Mikal Bridges"): 31.5,
    ("NYK", "Karl-Anthony Towns"): 30.5,
    ("NYK", "Miles McBride"): 19.1,
    ("NYK", "Landry Shamet"): 14.8,
    ("NYK", "Mitchell Robinson"): 14.1,
    ("NYK", "Jordan Clarkson"): 11.4,
    ("NYK", "Jose Alvarado"): 8.6,
    ("NYK", "Ariel Hukporti"): 8.8,
    ("NYK", "Tyler Kolek"): 6.6,
    ("NYK", "Pacome Dadiet"): 5.7,
    ("NYK", "Mohamed Diawara"): 7.2,
    ("NYK", "Kevin McCullar Jr."): 5.5,
    ("NYK", "Jeremy Sochan"): 4.6,
    ("NYK", "Guerschon Yabusele"): 4.6,

    # ── San Antonio Spurs (last 15+ games, playoffs) ──
    ("SAS", "Devin Vassell"): 33.9,
    ("SAS", "Stephon Castle"): 33.5,
    ("SAS", "De'Aaron Fox"): 32.9,
    ("SAS", "Victor Wembanyama"): 32.8,
    ("SAS", "Julian Champagnie"): 30.5,
    ("SAS", "Dylan Harper"): 25.7,
    ("SAS", "Keldon Johnson"): 18.1,
    ("SAS", "Luke Kornet"): 13.9,
    ("SAS", "Carter Bryant"): 9.4,
    ("SAS", "Harrison Barnes"): 9.4,
    ("SAS", "Kelly Olynyk"): 3.9,
    ("SAS", "Jordan McLaughlin"): 4.6,
    ("SAS", "Bismack Biyombo"): 2.8,
    ("SAS", "Mason Plumlee"): 3.0,
    ("SAS", "Lindy Waters III"): 3.8,

    # ── Top league MPG leaders (season, for other teams) ──
    ("PHI", "Tyrese Maxey"): 38.0,
    ("HOU", "Amen Thompson"): 37.4,
    ("HOU", "Kevin Durant"): 36.4,
    ("LAL", "Luka Doncic"): 35.8,
    ("NOP", "Trey Murphy"): 35.5,
    ("DEN", "Jamal Murray"): 35.4,
    ("ATL", "Jalen Johnson"): 35.2,
    ("HOU", "Jabari Smith"): 35.1,
    ("MIN", "Anthony Edwards"): 35.0,
    ("PHI", "VJ Edgecombe"): 35.0,
    ("CLE", "James Harden"): 34.8,
    ("DEN", "Nikola Jokic"): 34.8,
    ("ORL", "Paolo Banchero"): 34.8,
    ("BOS", "Jaylen Brown"): 34.4,
    ("BOS", "Derrick White"): 34.1,
    ("DET", "Cade Cunningham"): 33.9,
    ("TOR", "Brandon Ingram"): 33.8,
    ("ORL", "Desmond Bane"): 33.6,
    ("PHX", "Devin Booker"): 33.5,
    ("CLE", "Donovan Mitchell"): 33.5,
    ("TOR", "Scottie Barnes"): 33.5,
    ("DAL", "Cooper Flagg"): 33.5,
    ("ATL", "N. Alexander-Walker"): 33.4,
    ("POR", "Deni Avdija"): 33.3,
    ("HOU", "Alperen Sengun"): 33.3,
    ("POR", "Toumani Camara"): 33.3,
    ("OKC", "Shai Gilgeous-Alexander"): 33.2,
    ("LAL", "LeBron James"): 33.2,
    ("IND", "Pascal Siakam"): 33.2,
}


def get_team_rotation(team_abbr):
    """Get rotation data for a team by abbreviation."""
    return NBA_ROTATIONS.get(team_abbr.upper())


def is_starter(player_name, team_abbr):
    """Check if a player is a starter for their team."""
    rotation = get_team_rotation(team_abbr)
    if rotation:
        return player_name in rotation["starting"]
    return False


def is_rotation_player(player_name, team_abbr):
    """Check if a player is in the rotation (starter or bench) for their team."""
    rotation = get_team_rotation(team_abbr)
    if rotation:
        return player_name in rotation["starting"] or player_name in rotation["rotation"]
    return False


def get_rotation_status(player_name, team_abbr):
    """
    Get rotation status for a player.
    Returns: 'starter', 'rotation', or 'none'
    """
    rotation = get_team_rotation(team_abbr)
    if not rotation:
        return 'none'

    if player_name in rotation["starting"]:
        return 'starter'
    elif player_name in rotation["rotation"]:
        return 'rotation'
    else:
        return 'none'


def get_actual_mpg(player_name, team_abbr):
    """
    Look up actual minutes per game from season stats.

    Args:
        player_name: Player's full name (e.g. 'Jalen Brunson')
        team_abbr: Team abbreviation (e.g. 'NYK')

    Returns:
        Actual MPG float if found, otherwise None
    """
    key = (team_abbr.upper(), player_name)
    return ACTUAL_MPG.get(key)


def get_estimated_minutes(player_name, team_abbr, salary=None):
    """
    Get minutes per game for a player, preferring last-15-games data.

    Priority:
      1. Last-15-games MPG from ACTUAL_MPG (most DFS-relevant)
      2. Estimated from rotation role + salary

    Args:
        player_name: Player's full name (e.g. 'Jalen Brunson')
        team_abbr: Team abbreviation (e.g. 'NYK')
        salary: Player's DK salary (used to refine star starter estimates)

    Returns:
        Minutes per game (float, rounded to 1 decimal)
    """
    # Prefer actual data
    actual = get_actual_mpg(player_name, team_abbr)
    if actual is not None:
        return actual

    # Fallback to role-based estimate
    status = get_rotation_status(player_name, team_abbr)
    base_minutes = {'starter': 33, 'rotation': 21, 'none': 8}.get(status, 8)

    # Star starters (high salary) play more minutes
    if status == 'starter' and salary is not None and salary >= 8000:
        extra = min(5, (salary - 8000) / 2000)
        base_minutes = base_minutes + extra

    return round(base_minutes, 1)


def get_minutes_weight(minutes):
    """
    Convert minutes per game to a reliability weight (0-1).
    Players who play more minutes are more reliable DFS plays.

    Scale:
        35+ min -> 1.0 (full reliability)
        30+ min -> 0.9
        25+ min -> 0.8
        20+ min -> 0.65
        15+ min -> 0.5
        10+ min -> 0.3
        <10 min -> 0.15
    """
    if minutes >= 35:
        return 1.0
    elif minutes >= 30:
        return 0.9
    elif minutes >= 25:
        return 0.8
    elif minutes >= 20:
        return 0.65
    elif minutes >= 15:
        return 0.5
    elif minutes >= 10:
        return 0.3
    else:
        return 0.15