"""Generate a PNG code flow diagram for the DFS Lineup Optimizer README."""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch

fig, ax = plt.subplots(1, 1, figsize=(14, 10))
ax.set_xlim(0, 14)
ax.set_ylim(0, 10)
ax.axis('off')

# Colors
DK_GREEN = '#1D8B1D'
DK_DARK = '#1A1A2E'
ACCENT_BLUE = '#4A90D9'
ACCENT_ORANGE = '#E8853D'
ACCENT_PURPLE = '#8B5CF6'
SHARED_COLOR = '#2D9CDB'
PREDICT_COLOR = '#E85D75'
LIGHT_BG = '#F8F9FA'
BORDER_COLOR = '#DEE2E6'

def draw_box(ax, x, y, w, h, label, color, fontsize=9, sublabel=None):
    box = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.15",
                          facecolor=color, edgecolor='white', linewidth=1.5, alpha=0.9)
    ax.add_patch(box)
    if sublabel:
        ax.text(x + w/2, y + h/2 + 0.15, label, ha='center', va='center',
                fontsize=fontsize, fontweight='bold', color='white', family='monospace')
        ax.text(x + w/2, y + h/2 - 0.2, sublabel, ha='center', va='center',
                fontsize=fontsize-2, color='white', alpha=0.85, style='italic')
    else:
        ax.text(x + w/2, y + h/2, label, ha='center', va='center',
                fontsize=fontsize, fontweight='bold', color='white', family='monospace')

def draw_arrow(ax, x1, y1, x2, y2, color='#6B7280'):
    ax.annotate('', xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle='->', color=color, lw=1.5, connectionstyle='arc3,rad=0'))

# Title
ax.text(7, 9.6, 'DFS Lineup Optimizer — Code Flow', ha='center', va='center',
         fontsize=16, fontweight='bold', color=DK_DARK, family='sans-serif')

# ── Top row: DraftKings API ──
draw_box(ax, 4.5, 8.5, 5, 0.7, 'DraftKings API', DK_GREEN, fontsize=10, sublabel='draft_kings package')

# ── Second row: Entry points ──
draw_box(ax, 0.5, 7.1, 3.2, 0.7, 'fetch_contests.py', ACCENT_BLUE)
draw_box(ax, 4.3, 7.1, 3.0, 0.7, 'list_contests.py', ACCENT_BLUE)
draw_box(ax, 7.9, 7.1, 4.0, 0.7, 'showdown_analyzer.py', ACCENT_ORANGE)

# Arrows from DK API to entry points
draw_arrow(ax, 5.5, 8.5, 2.1, 7.8)
draw_arrow(ax, 5.8, 8.5, 5.8, 7.8)
draw_arrow(ax, 7.0, 8.5, 9.9, 7.8)

# ── Middle: comprehensive_analyzer ──
draw_box(ax, 2.5, 5.3, 9, 1.4, 'comprehensive_analyzer.py', DK_DARK, fontsize=10)

# Inner boxes for comprehensive_analyzer
draw_box(ax, 3.0, 5.55, 2.2, 0.45, 'contest_detector', '#6366F1', fontsize=7)
draw_box(ax, 5.8, 5.55, 2.5, 0.45, 'draftkings_scoring', '#6366F1', fontsize=7)
draw_box(ax, 9.0, 5.55, 2.0, 0.45, 'nba_rotations', '#6366F1', fontsize=7)

# Arrow from showdown_analyzer to comprehensive_analyzer
draw_arrow(ax, 9.9, 7.1, 7.0, 6.7)

# ── Shared modules box ──
draw_box(ax, 0.3, 5.3, 2.0, 1.4, 'Shared', SHARED_COLOR, fontsize=9, sublabel='utils.py\nplayer_builder\nlineup_optimizer')

# Arrow from shared to comprehensive
draw_arrow(ax, 2.3, 6.0, 2.5, 6.0)

# ── Bottom: prediction_tracker ──
draw_box(ax, 2.5, 2.8, 9, 1.4, 'prediction_tracker.py', PREDICT_COLOR, fontsize=10)

# Inner boxes for prediction_tracker
draw_box(ax, 3.0, 3.0, 2.0, 0.45, 'game_results', '#C2185B', fontsize=7)
draw_box(ax, 5.5, 3.0, 1.8, 0.45, 'db.py', '#C2185B', fontsize=7)
draw_box(ax, 7.8, 3.0, 2.8, 0.45, 'draftkings_scoring', '#C2185B', fontsize=7)

# Arrow from comprehensive to prediction
draw_arrow(ax, 7.0, 5.3, 7.0, 4.2)

# ── Bottom labels ──
ax.text(7, 2.3, 'SQLite Database', ha='center', va='center',
         fontsize=9, color='#6B7280', style='italic')

draw_arrow(ax, 7, 2.8, 7, 2.45)

# ── Legend ──
legend_items = [
    (DK_GREEN, 'DK API / Data Source'),
    (ACCENT_BLUE, 'Entry Scripts'),
    (ACCENT_ORANGE, 'Main Pipeline'),
    (DK_DARK, 'Analysis Engine'),
    (SHARED_COLOR, 'Shared Modules'),
    (PREDICT_COLOR, 'Prediction Tracking'),
    ('#6366F1', 'Sub-Modules'),
    ('#C2185B', 'Supporting Modules'),
]

for i, (color, label) in enumerate(legend_items):
    x = 1.0 + (i % 4) * 3.2
    y = 0.3 + (i // 4) * 0.4
    rect = plt.Rectangle((x - 0.15, y - 0.08), 0.3, 0.25,
                          facecolor=color, edgecolor='white', linewidth=0.5, alpha=0.9)
    ax.add_patch(rect)
    ax.text(x + 0.25, y + 0.04, label, fontsize=7.5, va='center', color='#374151', family='sans-serif')

plt.tight_layout()
plt.savefig('C:/coding/sr_project/dfs_lineup_optimizer/code_flow.png', dpi=150, bbox_inches='tight',
            facecolor='white', edgecolor='none', pad_inches=0.2)
plt.close()
print('Saved code_flow.png')