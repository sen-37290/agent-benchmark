# /// script
# dependencies = ["matplotlib==3.10.5", "google-api-python-client==2.179.0"]
# ///
from _common import *
def draw(data,out):
    fig,ax=setup("COST VS. SOLVE RATE — SWE-BENCH VERIFIED + TERMINAL-BENCH 2.1",(15.0,8.0))
    for benchmark,marker in [("SWE-bench Verified","o"),("Terminal-Bench 2.1","D")]:
        rs=[r for r in data if r["benchmark"]==benchmark]; rs.sort(key=lambda r:float(r["average_cost_usd"]))
        base=[r for r in rs if r["strategy"]!="Routing"]
        ax.plot([float(r["average_cost_usd"]) for r in base],[float(r["solve_rate_pct"]) for r in base],color=GREY)
        for r in rs:
            x,y=float(r["average_cost_usd"]),float(r["solve_rate_pct"]); color=GREEN if r["strategy"]=="Routing" else model_color(r["strategy"])
            ax.scatter(x,y,s=120,marker=marker,color=color,edgecolor=BG,zorder=3); ax.text(x,y+.5,f"{y:.1f}%   ${x:.3f}",ha="center",family="monospace")
    ax.set_xlabel("Average generation cost per selected task (USD)"); ax.set_ylabel("Task solver rate (%)",fontweight="bold"); ax.xaxis.set_major_formatter(lambda x,pos:f"${x:.2f}"); ax.set_xlim(.15,.795); ax.set_ylim(80,98.2)
    model_handles=[Patch(color=PURPLE),Patch(color=ORANGE),Patch(color=GREEN)]
    model_legend=ax.legend(model_handles,[*MODELS,"Routing"],title="Model",frameon=False,loc="lower right")
    benchmark_handles=[Line2D([],[],marker="o",linestyle="None",color=GREY),Line2D([],[],marker="D",linestyle="None",color=GREY)]
    ax.add_artist(model_legend); ax.legend(benchmark_handles,["SWE-bench Verified","Terminal-Bench 2.1"],title="Benchmark",frameon=False,ncol=2,loc="lower center",bbox_to_anchor=(.5,-.005))
    finish(fig,ax,out)
if __name__ == "__main__": cli(7,"fig07_combined_cost_accuracy_routing.csv",draw)
