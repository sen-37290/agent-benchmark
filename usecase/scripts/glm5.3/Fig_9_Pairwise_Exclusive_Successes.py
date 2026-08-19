# /// script
# dependencies = ["matplotlib==3.10.5", "google-api-python-client==2.179.0"]
# ///
from _common import *
def draw(data,out):
    fig,axes=plt.subplots(1,2,figsize=(19.8,9.8),dpi=150); fig.patch.set_facecolor(BG); fig.text(.02,.98,"PAIRWISE-EXCLUSIVE SUCCESSES — GLM-5.3 VS. KIMI-K3",va="top",fontsize=18,fontweight="bold",family="monospace")
    for ax,benchmark in zip(axes,("SWE-Bench Verified","Terminal-Bench 2.1")):
        rs=[r for r in data if r["benchmark"]==benchmark]; ys=range(len(rs)); h=.28
        for j,(key,color) in enumerate((("kimi_only",ORANGE),("glm_only",PURPLE))):
            vals=[int(r[key]) for r in rs]; pos=[y+(j-.5)*h for y in ys]; ax.barh(pos,vals,h,color=color)
            for y,v in zip(pos,vals): ax.text(v+.1,y,str(v),va="center",fontweight="bold")
        ax.set_yticks(list(ys),[f"{r['domain']}  (n={r['total']})" for r in rs],fontweight="bold"); ax.invert_yaxis(); ax.set_title(benchmark,fontweight="bold",family="monospace"); ax.set_xlabel("Pairwise-exclusive tasks solved (count)"); ax.set_xlim(0,13); ax.set_xticks(range(0,13,2)); ax.grid(axis="x",color=GRID); ax.set_axisbelow(True); ax.spines[["top","right"]].set_visible(False)
    fig.legend([Patch(color=ORANGE),Patch(color=PURPLE)],["Kimi-K3 only","GLM-5.3 only"],ncol=2,frameon=False,loc="upper center",bbox_to_anchor=(.5,.95))
    fig.subplots_adjust(left=.14,right=.98,top=.84,bottom=.08,wspace=.42); out.parent.mkdir(parents=True,exist_ok=True); fig.savefig(out,bbox_inches="tight",facecolor=BG); plt.close(fig)
if __name__ == "__main__": cli(9,"fig09_pairwise_exclusive_successes.csv",draw)
