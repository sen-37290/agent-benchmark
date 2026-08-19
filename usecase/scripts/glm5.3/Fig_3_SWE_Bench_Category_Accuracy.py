# /// script
# dependencies = ["matplotlib==3.10.5", "google-api-python-client==2.179.0"]
# ///
from _common import *
def draw(data,out):
    fig,ax=setup("SWE-Bench Verified: accuracy by solver-demand class",(15.5,8.2))
    cats=list(dict.fromkeys(r["category"] for r in data)); h=.24
    for j,m in enumerate(MODELS):
        rs=[next(r for r in data if r["category"]==c and r["model"]==m) for c in cats]
        ys=[i+(j-.5)*h for i in range(len(cats))]; vals=[100*int(r["solved"])/int(r["total"]) for r in rs]
        ax.barh(ys,vals,h*.92,color=model_color(m))
        for y,v,r in zip(ys,vals,rs): ax.text(v+.8,y,f"{v:.1f}%  ({r['solved']}/{r['total']})",va="center",fontweight="bold",family="monospace")
    ax.set_yticks(range(len(cats)),[f"{c}  (n={next(r['total'] for r in data if r['category']==c)})" for c in cats],fontweight="bold")
    ax.invert_yaxis(); ax.set_xlim(0,110); ax.set_xticks([0,20,40,60,80,100]); ax.set_xlabel("Task success rate (%)")
    legend_models(ax,loc="lower right",bbox_to_anchor=(1,.005),ncol=2)
    fig.subplots_adjust(left=.27,right=.98,top=.90,bottom=.08)
    finish(fig,ax,out)
if __name__ == "__main__": cli(3,"fig03_swebench_category_accuracy.csv",draw)
