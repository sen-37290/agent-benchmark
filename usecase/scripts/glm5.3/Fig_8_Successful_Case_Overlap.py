# /// script
# dependencies = ["matplotlib==3.10.5", "google-api-python-client==2.179.0", "matplotlib-venn==1.1.2"]
# ///
from _common import *
from matplotlib_venn import venn2
from matplotlib_venn.layout.venn2 import DefaultLayoutAlgorithm
def draw(data,out):
    fig,axes=plt.subplots(1,2,figsize=(18.5,8.2),dpi=150); fig.patch.set_facecolor(BG); fig.text(.008,.978,"SUCCESSFUL-CASE OVERLAP",va="top",fontsize=18,fontweight="bold",family="monospace")
    for ax,r in zip(axes,data):
        venn=venn2((int(r["glm_only"]),int(r["kimi_only"]),int(r["both"])),set_labels=(None,None),set_colors=(PURPLE,ORANGE),alpha=.55,ax=ax,layout_algorithm=DefaultLayoutAlgorithm(fixed_subset_sizes=(1,1,1)))
        for label in venn.subset_labels:
            if label: label.set_fontsize(24); label.set_fontweight("bold")
        ax.text(.31,.86,"GLM-5.3",transform=ax.transAxes,ha="center",color=PURPLE,fontsize=13,fontweight="bold",family="monospace")
        ax.text(.69,.86,"Kimi-K3",transform=ax.transAxes,ha="center",color=ORANGE,fontsize=13,fontweight="bold",family="monospace")
        ax.set_title(r["benchmark"],fontweight="bold",family="monospace",fontsize=14,pad=30); ax.text(.5,-.05,f"Neither: {r['neither']}",transform=ax.transAxes,ha="center",color="#777",family="monospace")
    fig.subplots_adjust(left=.03,right=.97,top=.86,bottom=.10,wspace=.18)
    out.parent.mkdir(parents=True,exist_ok=True); fig.savefig(out,bbox_inches="tight",facecolor=BG); plt.close(fig)
if __name__ == "__main__": cli(8,"fig08_success_overlap.csv",draw)
