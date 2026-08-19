# /// script
# dependencies = ["matplotlib==3.10.5", "google-api-python-client==2.179.0"]
# ///
from _common import *
def draw(data,out):
    fig,ax=setup("PER-TASK ROUTING VS. EACH MODEL ALONE",(14.5,7.6)); benches=list(dict.fromkeys(r["benchmark"] for r in data)); labels=[*MODELS,"Route per task"]
    for j,label in enumerate(labels):
        rs=[next(r for r in data if r["benchmark"]==b and r["strategy"]==label) for b in benches]; xs=[i+(j-1)*.22 for i in range(len(benches))]; vals=[float(r["solve_rate_pct"]) for r in rs]; color=[PURPLE,ORANGE,GREEN][j]
        ax.bar(xs,vals,.2,color=color)
        for x,v,r in zip(xs,vals,rs): ax.text(x,v+1.2,f"{v:.1f}"+(f"  (+{r['gain_pp']})" if r["gain_pp"] else ""),ha="center",color=color,fontweight="bold")
    ax.set_xticks(range(len(benches)),benches,fontsize=15,fontweight="bold"); ax.set_yticks([0,25,50,75,100]); ax.set_ylim(0,105); ax.set_ylabel("Task solve rate (%)",fontweight="bold"); legend_models(ax,True,loc="upper left",bbox_to_anchor=(0,1.13),ncol=3)
    fig.subplots_adjust(left=.06,right=.98,top=.84,bottom=.10)
    finish(fig,ax,out)
if __name__ == "__main__": cli(6,"fig06_per_task_routing.csv",draw)
