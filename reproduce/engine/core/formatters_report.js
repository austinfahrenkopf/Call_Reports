//@ pctD
const pctD=(a,b)=>(a!=null&&b!=null&&b!==0)?100*(a-b)/b:null;
//@ fA
const fA=v=>v==null?'—':v>=1e9?'$'+(v/1e9).toFixed(1)+'T':v>=1e6?'$'+(v/1e6).toFixed(1)+'B':'$'+Math.round(v/1e3)+'M';
//@ fP
const fP=v=>v==null?'—':v.toFixed(2)+'%';
//@ rnk
const rnk=assetRank?`Rank #${assetRank} of ${assetCount}${assetPct!=null?' ('+ordSuffix(assetPct)+' %ile)':''}`:null;
//@ pctileBar
const pctileBar=(p,betterWhen)=>{if(p==null)return '';const disp=betterWhen==='lower'?100-p:p;const c=disp>=75?'#1b7f3b':disp>=50?'#2980b9':disp>=25?'#e67e22':'#c0392b';return `<div style="margin-top:4px"><div style="height:5px;background:var(--border,#e0e4ea);border-radius:3px;overflow:hidden"><div style="height:100%;width:${disp}%;background:${c};border-radius:3px"></div></div><div style="font-size:11px;color:var(--fg2,#888);margin-top:1px">${ordSuffix(disp)} %ile among reporters</div></div>`;};
