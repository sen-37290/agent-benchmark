# /// script
# dependencies = ["matplotlib==3.10.5", "google-api-python-client==2.179.0"]
# ///
from _common import *
MARKERS={"Software":"o","ML":"s","System":"D","Science":"^","Media":"P"}
def draw(data,out):
    fig,ax=setup("Terminal-Bench 2.1: accuracy vs. average recorded cost by official domain",(15.4,8.2))
    for domain,marker in MARKERS.items():
        pair=[r for r in data if r["domain"]==domain]; pair.sort(key=lambda r:MODELS.index(r["model"]))
        xs=[float(r["average_cost_usd"]) for r in pair]; ys=[100*int(r["solved"])/int(r["total"]) for r in pair]
        ax.plot(xs,ys,color=GREY,lw=1.8)
        for r,x,y in zip(pair,xs,ys): ax.scatter(x,y,s=110,marker=marker,color=model_color(r["model"]),edgecolor=BG,zorder=3)
        ax.text(sum(xs)/2,sum(ys)/2+.5,domain,ha="center",fontweight="bold")
    ax.set_xlabel("Average recorded generation cost per selected task (USD)"); ax.set_ylabel("Accuracy (%)")
    ax.xaxis.set_major_formatter(lambda x,pos:f"${x:.2f}"); ax.set_xlim(.175,1.575); ax.set_ylim(61,95.3); ax.set_yticks([65,70,75,80,85,90,95])
    handles=[Line2D([],[],marker="o",linestyle="None",color=c,markersize=8) for c in (PURPLE,ORANGE)]
    ax.legend(handles,MODELS,frameon=False,loc="upper right"); finish(fig,ax,out)
if __name__ == "__main__": cli(4,"fig04_terminal_domain_cost_accuracy.csv",draw)
