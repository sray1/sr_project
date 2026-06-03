"""
NBA depth chart and rotation data for 2025-2026 season.
Extracted from Hoops Distillery depth charts.
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
        "rotation": ["Miles McBride", "Jose Alvarado", "Tyler Kolek", "Jordan Clarkson", "Mohamed Diawara", "Kevin McCullar", "Jeremy Sochan", "Pacome Dadiet", "Mitchell Robinson", "Ariel Hukporti"]
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
        "rotation": ["Dylan Harper", "Jordan McLaughlin", "Keldon Johnson", "Carter Bryant", "Harrison Barnes", "Kelly Olynyk", "Luke Kornet", "Mason Plumlee"]
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