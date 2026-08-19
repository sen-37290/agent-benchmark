# /// script
# dependencies = ["matplotlib==3.10.5", "google-api-python-client==2.179.0"]
# ///
from _common import *

def draw(data, out):
    fig, ax = setup("BENCHMARK FAMILY ACCURACY", (15.0, 8.3))
    benchmarks = list(dict.fromkeys(r["benchmark"] for r in data)); centers = [0.0,1.55]
    for j, model in enumerate(MODELS):
        vals = [next(r for r in data if r["benchmark"] == b and r["model"] == model) for b in benchmarks]
        xs = [x + (j-.5)*.21 for x in centers]
        rates = [100*int(r["solved"])/int(r["total"]) for r in vals]
        ax.bar(xs, rates, .185, color=model_color(model))
        for x, y, r in zip(xs, rates, vals):
            ax.text(x,y+1.3,f"{y:.1f}",ha="center",color=model_color(model),fontweight="bold")
            ax.text(x,-2.5,f"{r['solved']}/{r['total']}",ha="center",color="#777")
    ax.set_xticks(list(centers), benchmarks, fontsize=15, fontweight="bold"); ax.set_xlim(-.292,1.843); ax.set_ylim(-10,108)
    ax.set_yticks([0,25,50,75,100]); ax.set_ylabel("Accuracy (%)",fontweight="bold")
    ax.tick_params(axis="x",pad=24); legend_models(ax,loc="upper right",bbox_to_anchor=(1,1.12),ncol=2)
    fig.subplots_adjust(left=.09,right=.98,top=.84,bottom=.18)
    finish(fig,ax,out)

if __name__ == "__main__": cli(1,"fig01_benchmark_family_accuracy.csv",draw)
