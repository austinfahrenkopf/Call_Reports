//@ qnlQ
q=>({'03':1,'06':2,'09':3,'12':4}[String(q).slice(5,7)]||4)
// makeKPI(rv,C,qnlQ) -- B2: the shared per-quarter KPI getter builder. Behaviourally identical to the
// three hand-written per-form getter sets it replaces (Class-B: different bytes, same values -- proven
// bit-exact over real panel data + a synthetic branch battery by _verify/b2_kpi_golden.py). Contracts:
//   rv    the form's OWN value accessor, bound: Call getV(codeArray,q) [RCFD->RCON fallback], Y-9C
//         get(code,q) [single BHCK], 002 getR(base,q) [RCFD??RCON??RCFN coalesce]. Every C entry is a
//         CODE SPEC in whatever shape that form's rv takes -- makeKPI never constructs a code string
//         itself, so no form can be made to read another form's codes.
//   C     {loans, npl:[..], ncur:[..], nco:{co,rec}, eff:{nii,noninc,nonexp}} -- the per-form literal.
//         Only the keys a form's own getters read need be present (002 has no nco/eff: it files no
//         income data). ncur is currently UNUSED by all three forms (002 dropped its ncurQ at
//         AQ-B2-1 -- the corrected nplQ IS the noncurrent number). AQ-B2-4 (fixed 2026-07-19,
//         ultracoder S3): makeKPI defines ONLY the getters whose inputs exist (annQ needs qnlQ;
//         ncoQ needs C.nco+C.loans+qnlQ; effQ needs C.eff; nplQ needs C.npl+C.loans; ncurQ needs
//         C.ncur+C.loans) -- a mis-wired call site now fails loud and local (TypeError: not a
//         function at the call) instead of throwing deep inside a getter at render time; getter
//         BODIES are byte-identical to the unconditional originals (b2_kpi_golden nplfix is the
//         value-equivalence proof). COMPONENT ARRAY ORDER: keep it as the form originally summed it -- the
//         numerator adds left-to-right with (v||0), preserving the original association (float64
//         addition is not associative; for 2-element sums it is commutative and safe either way).
//         AQ-B2-1 (2026-07-16): npl is the STANDARD definition -- nonaccrual (1403) + 90+ days past
//         due (1407) -- 30-89 PD (1406) is deliberately NOT a component; see the fix contract in
//         _orchestration/ANALYST_QA.md. AQ-B2-2 (same date): nplQ/ncurQ return null when EVERY
//         numerator component is null (no data), instead of a false 0.00% -- a filed zero still
//         renders 0.00% because a present 0 is not null.
//   qnlQ  the quarter-of-year helper above, injected (annQ/ncoQ annualise by 4/qnlQ). 002 passes none:
//         nplQ/ncurQ do not annualise, and 002 has no annQ/ncoQ getter to call.
//@ makeKPI
function makeKPI(rv,C,qnlQ){const gsum=(ss,q)=>{const vs=ss.map(s=>rv(s,q));return vs.every(v=>v==null)?null:vs.map(v=>v||0).reduce((a,b)=>a+b);};const G={};if(qnlQ)G.annQ=(n,d,q)=>{const nv=rv(n,q),dv=rv(d,q);return nv!=null&&dv!=null&&dv>0?100*nv/dv*(4/qnlQ(q)):null;};if(C.nco&&C.loans&&qnlQ)G.ncoQ=q=>{const c=rv(C.nco.co,q),r=rv(C.nco.rec,q),l=rv(C.loans,q);return c!=null&&r!=null&&l!=null&&l>0?100*(c-r)/l*(4/qnlQ(q)):null;};if(C.eff)G.effQ=q=>{const n=rv(C.eff.nii,q),nc=rv(C.eff.noninc,q),x=rv(C.eff.nonexp,q);return n!=null&&nc!=null&&x!=null&&(n+nc)>0?100*x/(n+nc):null;};if(C.npl&&C.loans)G.nplQ=q=>{const l=rv(C.loans,q);const np=gsum(C.npl,q);return l&&l>0&&np!=null?100*np/l:null;};if(C.ncur&&C.loans)G.ncurQ=q=>{const l=rv(C.loans,q);const nc=gsum(C.ncur,q);return l&&l>0&&nc!=null?100*nc/l:null;};return G;}
