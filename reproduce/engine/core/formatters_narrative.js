//@ fA
const fA=v=>v==null?null:v>=1e9?'$'+(v/1e9).toFixed(1)+'T':v>=1e6?'$'+(v/1e6).toFixed(1)+'B':'$'+Math.round(v/1e3)+'M';
//@ fP
const fP=v=>v==null?null:v.toFixed(2)+'%';
