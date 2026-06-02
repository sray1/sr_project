"""
Projection data integration for DraftKings DFS lineups.
Provides methods to load, merge, and analyze player projections.
"""

import pandas as pd
from typing import Dict, List, Optional
from draft_kings import Client


class ProjectionManager:
    """Manages projection data for DFS lineup optimization."""

    def __init__(self):
        self.projections: Dict[str, Dict] = {}
        self.projection_fields = ['player_id', 'player_name', 'projected_points', 'projected_value']

    def load_from_csv(self, filepath: str) -> pd.DataFrame:
        """
        Load projections from a CSV file.

        Expected columns: player_id, player_name, projected_points, projected_value
        """
        df = pd.read_csv(filepath)
        self._store_projections(df)
        return df

    def load_from_dict(self, projections: List[Dict]) -> pd.DataFrame:
        """
        Load projections from a list of dictionaries.

        Args:
            projections: List of dicts with player projection data
        """
        df = pd.DataFrame(projections)
        self._store_projections(df)
        return df

    def create_sample_projections(self, draft_group_id: int) -> pd.DataFrame:
        """
        Create sample projections for a draft group (for testing).

        In production, this would be replaced with actual projection data.
        """
        client = Client()
        draftables = client.draftables(draft_group_id)
        players = draftables.players

        projections = []
        for player in players:
            # Simple projection based on salary (higher salary = higher projection)
            # In production, use actual projection data from your source
            salary = player.salary
            projected_points = salary / 1000  # Rough estimate: 1 point per $1k salary

            projections.append({
                'player_id': player.player_id,
                'player_name': player.name_details.display,
                'team': player.team_details.abbreviation,
                'position': player.position_name,
                'salary': salary,
                'projected_points': round(projected_points, 1),
                'projected_value': round(projected_points / (salary / 1000), 3)
            })

        df = pd.DataFrame(projections)
        self._store_projections(df)
        return df

    def get_player_projection(self, player_id: str) -> Optional[Dict]:
        """Get projection for a specific player."""
        return self.projections.get(str(player_id))

    def merge_with_draftables(self, draft_group_id: int) -> pd.DataFrame:
        """
        Merge projection data with DraftKings draftable players.

        Returns a DataFrame with both DK data and projections.
        """
        client = Client()
        draftables = client.draftables(draft_group_id)

        rows = []
        for player in draftables.players:
            proj = self.get_player_projection(player.player_id)
            row = {
                'player_id': player.player_id,
                'player_name': player.name_details.display,
                'team': player.team_details.abbreviation,
                'position': player.position_name,
                'salary': player.salary,
                'starts_at': player.competition_details.starts_at,
                'draftable': player.is_disabled == False
            }

            if proj:
                row['projected_points'] = proj.get('projected_points')
                row['projected_value'] = proj.get('projected_value')
            else:
                row['projected_points'] = None
                row['projected_value'] = None

            rows.append(row)

        return pd.DataFrame(rows)

    def get_top_values(self, draft_group_id: int, top_n: int = 10) -> pd.DataFrame:
        """Get top value plays (best projected points per $1k salary)."""
        df = self.merge_with_draftables(draft_group_id)
        df = df[df['projected_value'].notna()]
        return df.nlargest(top_n, 'projected_value')

    def _store_projections(self, df: pd.DataFrame):
        """Store projections in internal dictionary."""
        self.projections.clear()
        for _, row in df.iterrows():
            player_id = str(row.get('player_id', ''))
            if player_id:
                self.projections[player_id] = row.to_dict()


def demo_projection_integration():
    """Demonstrate projection data integration."""
    print("=" * 60)
    print("DFS Projection Data Integration Demo")
    print("=" * 60)

    manager = ProjectionManager()

    # Get sample draft group
    from draft_kings import Sport
    client = Client()
    drafts = client.contests(Sport.NBA)
    if not drafts.contests:
        print("No NBA contests available")
        return

    draft_group_id = drafts.contests[0].draft_group_id
    print(f"\nUsing draft group: {draft_group_id}\n")

    # Create sample projections
    print("1. Creating sample projections...")
    projections_df = manager.create_sample_projections(draft_group_id)
    print(f"   Created {len(projections_df)} projections")
    print(f"   Sample projections:\n{projections_df.head()}\n")

    # Merge with draftables
    print("2. Merging with DraftKings data...")
    merged_df = manager.merge_with_draftables(draft_group_id)
    print(f"   Merged {len(merged_df)} players")
    print(f"   Sample merged data:\n{merged_df.head()}\n")

    # Get top value plays
    print("3. Top 10 value plays (best points per $1k salary):")
    top_values = manager.get_top_values(draft_group_id, top_n=10)
    print(top_values[['player_name', 'position', 'salary', 'projected_points', 'projected_value']].to_string(index=False))

    print("\n" + "=" * 60)


if __name__ == "__main__":
    demo_projection_integration()