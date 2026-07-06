"""
2026 US/Canadian thoroughbred meet schedule.

Maps each major track code to a list of (meet_start, meet_end) date ranges
(YYYY-MM-DD strings) for the 2025-2026 / 2026 racing year. Used by
`active_tracks(start, end)` to pick the tracks whose meets overlap a backtest
window, so the runner only fetches tracks that are actually in session.

Sources: NYRA 2026 schedule (SAR / BEL / AQU), track press releases / 2026
condition books (CD, KEE, ELP, GP, SA, DMR, FG, TAM, OP, MTH, PIM, LRL, PRX,
DEL, CBY, WO, IND, etc.). Dates are 2026-specific and approximate to within a
few days; the runner probes HRN per track/date, so a track flagged active but
dark on a given date simply yields no card (self-correcting). Golden Gate Fields
(GG) is omitted - it closed permanently in June 2024.

A track is "active" in a window if any of its meet ranges overlaps [start, end]
inclusive (m_start <= end AND m_end >= start).
"""

from datetime import date

# track_code -> list of (meet_start_iso, meet_end_iso)
SCHEDULE = {
    # NYRA
    "AQU": [("2026-01-01", "2026-07-02")],   # "Belmont at the Big A" spring/summer; Aqueduct closes, Belmont reopens fall
    "BEL": [("2026-09-12", "2026-10-30")],   # Belmont Park reopens fall 2026 (post-renovation)
    "SAR": [("2026-06-03", "2026-06-07"),    # Belmont Stakes Racing Festival at Saratoga
            ("2026-07-03", "2026-09-07")],   # Summer meet (opens July 3)
    # Florida
    "GP":  [("2026-01-01", "2026-12-31")],   # year-round (Championship + Royal Palm meets)
    "TAM": [("2025-11-26", "2026-05-30")],   # 2025-26 meet
    # California
    "SA":  [("2025-12-26", "2026-06-14"),    # winter/spring
            ("2026-09-26", "2026-11-02")],   # autumn
    "DMR": [("2026-07-18", "2026-09-10"),    # summer
            ("2026-11-07", "2026-11-29")],   # fall
    # Kentucky
    "CD":  [("2026-04-25", "2026-06-29"),    # spring/summer
            ("2026-09-11", "2026-09-28")],   # September meet
    "KEE": [("2026-04-03", "2026-04-24"),    # spring
            ("2026-10-09", "2026-10-31")],   # fall
    "ELP": [("2026-07-02", "2026-09-07")],   # Ellis Park summer
    "TP":  [("2026-01-01", "2026-03-29"),    # Turfway winter/spring
            ("2026-11-27", "2026-12-31")],   # holiday meet
    # Arkansas
    "OP":  [("2025-12-06", "2026-05-10")],   # 2025-26
    # Louisiana
    "FG":  [("2025-11-22", "2026-03-29")],   # 2025-26
    "EVD": [("2026-04-15", "2026-09-13")],
    "DTA": [("2025-10-21", "2026-03-15")],   # Delta thoroughbred (harness in summer)
    # New Jersey
    "MTH": [("2026-05-09", "2026-09-27")],   # Monmouth
    # Maryland
    "PIM": [("2026-05-01", "2026-05-16")],   # Preakness season
    "LRL": [("2026-01-01", "2026-12-31")],   # Laurel year-round
    # Pennsylvania
    "PRX": [("2026-01-01", "2026-12-31")],   # Parx year-round
    # Virginia
    "COL": [("2026-07-09", "2026-09-06")],   # Colonial Downs summer
    # Delaware
    "DEL": [("2026-05-13", "2026-10-10")],
    # Minnesota
    "CBY": [("2026-05-23", "2026-09-19")],   # Canterbury
    # Illinois
    "HTH": [("2026-05-02", "2026-09-06")],   # Hawthorne thoroughbred
    # Indiana
    "IND": [("2026-04-14", "2026-11-09")],   # Horseshoe Indianapolis
    # Ohio
    "TDP": [("2026-04-27", "2026-10-05")],   # Thistledown
    "PID": [("2026-07-06", "2026-10-01")],   # Presque Isle
    # West Virginia
    "MNR": [("2026-01-01", "2026-12-31")],   # Mountaineer
    "MVG": [("2025-10-01", "2026-04-25")],   # Mahoning Valley (races Oct-Apr)
    # New York (other)
    "FL":  [("2026-05-03", "2026-11-15")],   # Finger Lakes
    # Texas
    "HOU": [("2026-01-02", "2026-05-04")],   # Sam Houston
    "LSA": [("2026-04-10", "2026-08-17")],   # Lone Star
    "RP":  [("2026-08-21", "2026-12-26")],   # Remington
    # Canada
    "WO":  [("2026-04-25", "2026-12-14")],   # Woodbine
}


def _to_date(s):
    y, m, d = s.split("-")
    return date(int(y), int(m), int(d))


def active_tracks(start_date, end_date, schedule=None):
    """Return sorted list of track codes whose meet overlaps [start_date, end_date].

    Overlap is inclusive: a meet [m_start, m_end] overlaps [start, end] iff
    m_start <= end AND m_end >= start.
    """
    sched = schedule if schedule is not None else SCHEDULE
    start = _to_date(start_date)
    end = _to_date(end_date)
    out = []
    for code, meets in sched.items():
        for m_start, m_end in meets:
            if _to_date(m_start) <= end and _to_date(m_end) >= start:
                out.append(code)
                break
    return sorted(out)


def inactive_tracks(start_date, end_date, schedule=None):
    """Complement of active_tracks - tracks in the schedule NOT overlapping window."""
    sched = schedule if schedule is not None else SCHEDULE
    active = set(active_tracks(start_date, end_date, sched))
    return sorted(code for code in sched if code not in active)


def all_tracks(schedule=None):
    """All track codes in the schedule, sorted."""
    sched = schedule if schedule is not None else SCHEDULE
    return sorted(sched.keys())