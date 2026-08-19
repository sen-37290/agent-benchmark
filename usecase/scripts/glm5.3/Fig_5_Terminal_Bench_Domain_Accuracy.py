# /// script
# dependencies = ["matplotlib==3.10.5", "google-api-python-client==2.179.0"]
# ///
from _common import *
def draw(data,out):
    fig,ax=setup("Terminal-Bench 2.1: accuracy by official task domain",(15.4,8.2)); domains=list(dict.fromkeys(r["domain"] for r in data)); h=.24
    for j,m in enumerate(MODELS):
        rs=[next(r for r in data if r["domain"]==d and r["model"]==m) for d in domains]; ys=[i+(j-.5)*h for i in range(len(domains))]
        vals=[100*int(r["solved"])/int(r["total"]) for r in rs]; ax.barh(ys,vals,h*.92,color=model_color(m))
        for y,v,r in zip(ys,vals,rs): ax.text(v+.8,y,f"{v:.1f}% ({r['solved']}/{r['total']})",va="center",fontweight="bold",family="monospace")
    ax.set_yticks(range(len(domains)),[f"{d}  (n={next(r['total'] for r in data if r['domain']==d)})" for d in domains],fontweight="bold"); ax.invert_yaxis(); ax.set_xlim(0,125)
    ax.set_xticks([0,25,50,75,100]); ax.set_xlabel("Task success rate (%)"); legend_models(ax,loc="lower center",bbox_to_anchor=(.5,1.02),ncol=2); fig.subplots_adjust(left=.20,right=.98,top=.86,bottom=.08); finish(fig,ax,out)
if __name__ == "__main__": cli(5,"fig05_terminal_domain_accuracy.csv",draw)
