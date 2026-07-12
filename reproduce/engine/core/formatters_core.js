//@ short
const short=lbl=>{const i=lbl.indexOf('▸');return (i>=0?lbl.slice(i+1):lbl).replace(/\s*\(.*\)\s*$/,'').trim();};
//@ fmtUnit
const fmtUnit=(v,pct)=>v==null?'—':pct?(+v).toFixed(2)+'%':(Math.abs(v)>=1e9?'$'+(v/1e9).toFixed(1)+'T':Math.abs(v)>=1e6?'$'+(v/1e6).toFixed(1)+'B':'$'+Math.round(v/1e3)+'M');
//@ ordSuffix
function ordSuffix(n){const a=Math.abs(n)%100;if(a>=11&&a<=13)return n+'th';switch(Math.abs(n)%10){case 1:return n+'st';case 2:return n+'nd';case 3:return n+'rd';default:return n+'th';}}
