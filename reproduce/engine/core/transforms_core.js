//@ deltaSeriesPP
function deltaSeriesPP(pctSeries,ws,qtrFn,tag){return deltaSeries(pctSeries,ws,qtrFn).map(s=>({...s,pct:true,label:s.label+' '+tag+' (pp)'}));}
//@ roll4qSeriesPP
function roll4qSeriesPP(pctSeries,ws){return roll4qSeries(pctSeries,ws,false).map(s=>({...s,pct:true,label:s.label.replace(/ \(4Q avg\)$/,' (4Q avg, pp)')}));}
//@ seriesBeginNote
function seriesBeginNote(series,win){if(!win||win.length<3)return '';const wIdx=Object.fromEntries(win.map((q,i)=>[q,i]));const lines=[];for(const s of series){let firstQ=null;for(const r of s.rows){if(r[1]!=null){firstQ=r[0];break;}}if(!firstQ)continue;const i=wIdx[firstQ];if(i==null||i<=2)continue;lines.push(`${_esc(short(s.label)||s.label)}: begins ${firstQ.slice(0,4)}`);}if(!lines.length)return '';return `<div style="font-size:11px;color:var(--muted,#9aa3b2);padding:2px 14px 6px;font-style:italic">${lines.join(' · ')}</div>`;}
