"""Append final five-class performance plots to the Figure 2 notebook."""

from __future__ import annotations

import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
NOTEBOOK = HERE / "fig2_swebench_verified_glm_5_2_vs_kimi_k3_vs_claude_opus_5.ipynb"
MARKER = "## Final five-class solver-demand comparison"


def markdown(source: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": source.splitlines(keepends=True)}


def code(source: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source.splitlines(keepends=True),
    }


def main() -> None:
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    notebook["cells"] = [
        cell for cell in notebook["cells"]
        if MARKER not in "".join(cell.get("source", []))
        and cell.get("metadata", {}).get("final_five_plot") is not True
    ]

    cells = [
        markdown(
            MARKER
            + "\n\nThe original repository domains are replaced with the final, mutually exclusive "
            "solver-demand taxonomy. Denominators vary by class, so every label shows both the "
            "percentage and solved/total count."
        ),
        code(
            """FINAL_TAXONOMY_PATH = REPO / 'notebooks/swebench_classification/outputs/solver_demand_v1/case_classifications.csv'

CLASS_LABELS = {
    'DATA_FIDELITY_PROBLEMS': 'Data fidelity problems',
    'TRACING_AND_OBSERVABILITY_PROBLEMS': 'Tracing & observability problems',
    'COMPATIBILITY_PROBLEMS': 'Compatibility problems',
    'RENDERING_AND_VISUAL_PROBLEMS': 'Rendering & visual problems',
    'PARSING_PROBLEMS': 'Parsing problems',
}
CLASS_ORDER = list(CLASS_LABELS.values())

taxonomy = pd.read_csv(FINAL_TAXONOMY_PATH, encoding='utf-8-sig', usecols=['case_id', 'primary_solver_demand_class'])
taxonomy['domain'] = taxonomy.primary_solver_demand_class.map(CLASS_LABELS)
assert len(taxonomy) == 150 and taxonomy.case_id.is_unique
assert taxonomy.domain.notna().all()
assert set(taxonomy.case_id) == set(pool)

taxonomy_rows = []
for model, config in MODELS.items():
    resolved, _ = load_resolved(config['summary'])
    model_cases = taxonomy.assign(resolved=taxonomy.case_id.isin(resolved))
    grouped = model_cases.groupby('domain', sort=False).resolved.agg(['sum', 'count'])
    for domain, row in grouped.iterrows():
        taxonomy_rows.append({
            'model': model, 'domain': domain,
            'resolved': int(row['sum']), 'total': int(row['count']),
            'rate_pct': 100 * row['sum'] / row['count'],
        })

taxonomy_scores = pd.DataFrame(taxonomy_rows)
taxonomy_scores.pivot(index='domain', columns='model', values='rate_pct').loc[CLASS_ORDER].round(1)

PLOT_MODELS = {model: config for model, config in MODELS.items() if model in ('GLM-5.2 xhigh', 'Kimi-K3 max')}
"""
        ),
        markdown("## Plot 1 — three-model accuracy by final solver-demand class"),
        code(
            """fig, ax = plt.subplots(figsize=(13.2, 7.4), dpi=150)
fig.patch.set_facecolor(SURFACE); ax.set_facecolor(SURFACE)

# Reverse because barh renders the first category at the bottom.
plot_domains = list(reversed(CLASS_ORDER))
height = 0.34
offsets = [height / 2, -height / 2]
for yi, domain in enumerate(plot_domains):
    for (model, config), offset in zip(PLOT_MODELS.items(), offsets):
        row = taxonomy_scores[(taxonomy_scores.model == model) & (taxonomy_scores.domain == domain)].iloc[0]
        ax.barh(yi + offset, row.rate_pct, height=height - 0.03, color=config['color'], zorder=3)
        ax.text(row.rate_pct + 1.0, yi + offset,
                f'{row.rate_pct:.1f}% ({row.resolved}/{row.total})',
                va='center', ha='left', fontsize=9.1, color=INK,
                fontfamily=MONO, fontweight='bold')

ax.set_yticks(range(len(plot_domains)))
ax.set_yticklabels(plot_domains, fontsize=11.5, fontweight='bold', color=INK)
ax.set_xlim(0, 132); ax.set_xticks([0, 25, 50, 75, 100])
ax.set_xticklabels(['0', '25', '50', '75', '100'], fontsize=10, color=MUTED, fontfamily=MONO)
ax.set_xlabel('Official resolve rate (%)', fontsize=10.5, color=MUTED)
ax.xaxis.grid(True, color=GRID, lw=0.8, zorder=0); ax.set_axisbelow(True)
for side in ('top', 'right', 'left'): ax.spines[side].set_visible(False)
ax.spines['bottom'].set_color(GRID); ax.tick_params(length=0)
ax.legend(handles=[Patch(color=config['color'], label=model) for model, config in PLOT_MODELS.items()],
          loc='lower center', bbox_to_anchor=(0.5, 1.01), ncol=2,
          frameon=False, fontsize=10.5, prop={'family': MONO}, labelcolor=INK)
fig.text(0.005, 0.955, 'SWE-bench Verified: accuracy by final solver-demand class',
         fontsize=15, fontweight='bold', fontfamily=MONO, color=INK, va='top')
fig.subplots_adjust(left=0.27, right=0.97, top=0.88, bottom=0.11)

out = REPO / 'notebooks/fig2_swebench_verified_final_taxonomy_grouped_glm_5_2_vs_kimi_k3.png'
fig.savefig(out, facecolor=SURFACE, bbox_inches='tight')
print(f'saved -> {out}')
plt.show()
"""
        ),
        markdown(
            "## Plot 2 — pairwise domain advantages\n\n"
            "Bars show percentage-point margin. Left means the first named model leads; right means "
            "the second named model leads. Each panel uses the same scale."
        ),
        code(
            """def pairwise_frame(left_model, right_model):
    pivot = taxonomy_scores.pivot(index='domain', columns='model', values=['rate_pct', 'resolved', 'total'])
    rows = []
    for domain in CLASS_ORDER:
        left_rate = float(pivot.loc[domain, ('rate_pct', left_model)])
        right_rate = float(pivot.loc[domain, ('rate_pct', right_model)])
        rows.append({
            'domain': domain,
            'left_model': left_model, 'right_model': right_model,
            'left_rate': left_rate, 'right_rate': right_rate,
            'left_resolved': int(pivot.loc[domain, ('resolved', left_model)]),
            'right_resolved': int(pivot.loc[domain, ('resolved', right_model)]),
            'total': int(pivot.loc[domain, ('total', left_model)]),
            'margin': right_rate - left_rate,
        })
    return pd.DataFrame(rows)

comparisons = [
    ('GLM-5.2 xhigh', 'Kimi-K3 max'),
]
pairwise = {pair: pairwise_frame(*pair) for pair in comparisons}
max_margin = max(abs(frame.margin).max() for frame in pairwise.values())
# Leave enough horizontal room for the winner and score annotations beyond the longest bar.
axis_limit = max(10, int((max_margin + 12) // 5 + 1) * 5)

fig, axes = plt.subplots(1, 1, figsize=(10, 7.2), dpi=150, sharey=True, squeeze=False)
fig.patch.set_facecolor(SURFACE)
for ax, (left_model, right_model) in zip(axes[0], comparisons):
    frame = pairwise[(left_model, right_model)].iloc[::-1].reset_index(drop=True)
    ax.set_facecolor(SURFACE)
    for yi, row in frame.iterrows():
        margin = row.margin
        winner = right_model if margin > 0 else left_model if margin < 0 else 'Tie'
        color = MODELS[right_model]['color'] if margin > 0 else MODELS[left_model]['color'] if margin < 0 else MUTED
        ax.barh(yi, margin, height=0.55, color=color, zorder=3)
        label_x = margin + (0.65 if margin >= 0 else -0.65)
        ha = 'left' if margin >= 0 else 'right'
        margin_text = f'{winner.split()[0]} {abs(margin):+.1f}' if winner != 'Tie' else 'Tie 0.0'
        ax.text(label_x, yi + 0.10, margin_text, ha=ha, va='bottom', color=color,
                fontsize=11, fontweight='bold', fontfamily=MONO)
        ax.text(label_x, yi - 0.10,
                f'{row.left_rate:.1f} vs {row.right_rate:.1f}  (n={row.total})',
                ha=ha, va='top', color=INK, fontsize=8.8, fontfamily=MONO)

    ax.axvline(0, color=GRID, lw=1.2, zorder=1)
    ax.set_xlim(-axis_limit, axis_limit)
    ticks = [-axis_limit, -axis_limit / 2, 0, axis_limit / 2, axis_limit]
    ax.set_xticks(ticks)
    ax.set_xticklabels([f'{abs(t):g}' for t in ticks], color=MUTED, fontfamily=MONO)
    ax.xaxis.grid(True, color=GRID, lw=0.8, zorder=0)
    ax.set_axisbelow(True); ax.tick_params(length=0)
    for side in ('top', 'right', 'left', 'bottom'): ax.spines[side].set_visible(False)
    ax.set_title(f'{left_model}  vs  {right_model}', fontsize=12.5,
                 fontweight='bold', fontfamily=MONO, color=INK, pad=24)
    ax.text(0.0, 1.01, f'← {left_model} ahead     {right_model} ahead →',
            transform=ax.transAxes, ha='left', va='bottom', color=MUTED,
            fontsize=8.5, fontfamily=MONO)

axes[0, 0].set_yticks(range(len(plot_domains)))
axes[0, 0].set_yticklabels(plot_domains, fontsize=10.5, fontweight='bold', color=INK)
fig.text(0.01, 0.965, 'Where each model leads across final solver-demand classes',
         fontsize=15, fontweight='bold', fontfamily=MONO, color=INK, va='top')
fig.text(0.50, 0.025, 'Percentage-point margin', ha='center', color=MUTED,
         fontsize=10, fontfamily=MONO)
fig.subplots_adjust(left=0.20, right=0.98, top=0.84, bottom=0.10, wspace=0.16)

out = REPO / 'notebooks/fig2_swebench_verified_final_taxonomy_pairwise_glm_5_2_vs_kimi_k3.png'
fig.savefig(out, facecolor=SURFACE, bbox_inches='tight')
print(f'saved -> {out}')
plt.show()

pairwise_table = pd.concat(
    [frame.assign(comparison=f'{left} vs {right}') for (left, right), frame in pairwise.items()],
    ignore_index=True,
)
pairwise_table[['comparison', 'domain', 'left_rate', 'right_rate', 'margin']].round(1)
"""
        ),
    ]
    for cell in cells:
        cell["metadata"]["final_five_plot"] = True
    notebook["cells"].extend(cells)
    NOTEBOOK.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
