// WCAG contrast check for the design tokens in src/app/globals.css. Run: node scripts/contrast.mjs (exit code 1 on any FAIL).
const P = {
  paper:"#F6F3EC", paper2:"#EEEAE0", surface:"#FFFDF8", ink:"#14201B", ink2:"#3A4A43", muted:"#5E6D66", rule:"#D9D3C4", ruleStrong:"#857D68", brassLight:"#E3C27F",
  brand:"#0F3D2E", brand2:"#1B5E46", brandTint:"#E3EFE8", brass:"#B8893B", brassInk:"#7A5A1C", brassTint:"#F3E8CF",
  high:"#A8231B", highTint:"#F8E4E1", medium:"#9A5B00", mediumTint:"#FBEBD0", low:"#2A7050", lowTint:"#E1F0E8", info:"#3D5A80", infoTint:"#E4EBF3",
  onBrand:"#F6F3EC", onBrandMuted:"#B7CDC1", onBrandBrass:"#E3C27F", white:"#FFFFFF",
};
const lin = c => { c/=255; return c<=0.03928? c/12.92 : ((c+0.055)/1.055)**2.4; };
const L = h => { const n=parseInt(h.slice(1),16); return 0.2126*lin(n>>16&255)+0.7152*lin(n>>8&255)+0.0722*lin(n&255); };
const cr = (a,b)=>{ const x=L(P[a]),y=L(P[b]); return (Math.max(x,y)+0.05)/(Math.min(x,y)+0.05); };
const pairs = [
 ["ink","paper",4.5],["ink","paper2",4.5],["ink","surface",4.5],["ink2","paper",4.5],["ink2","surface",4.5],["ink2","paper2",4.5],
 ["muted","paper",4.5],["muted","paper2",4.5],["muted","surface",4.5],["muted","brandTint",4.5],
 ["brand","paper",4.5],["brand","surface",4.5],["brand2","surface",4.5],["brand2","paper",4.5],["brand","brandTint",4.5],["brand2","brandTint",4.5],
 ["surface","brand",4.5],["surface","brand2",4.5],["onBrand","brand",4.5],["onBrandMuted","brand",4.5],["onBrandBrass","brand",4.5],["brass","brand",3],
 ["brassInk","paper",4.5],["brassInk","surface",4.5],["brassInk","brassTint",4.5],["ink","brass",4.5],["ink","brassTint",4.5],
 ["high","surface",4.5],["high","paper",4.5],["high","highTint",4.5],["surface","high",4.5],
 ["medium","surface",4.5],["medium","paper",4.5],["medium","mediumTint",4.5],
 ["low","surface",4.5],["low","paper",4.5],["low","lowTint",4.5],
 ["info","surface",4.5],["info","paper",4.5],["info","infoTint",4.5],
 ["ink2","highTint",4.5],["ink2","mediumTint",4.5],["ink2","lowTint",4.5],["ink2","infoTint",4.5],["ink2","brandTint",4.5],["ink","paper",4.5],["white","high",4.5],["ruleStrong","surface",3],["ruleStrong","paper",3],["brassLight","brand",4.5],["brand2","surface",3],["brand2","paper2",3],
];
let failed = 0;
for (const [a,b,t] of pairs){ const r=cr(a,b); if (r<t) failed++; console.log((r>=t?"PASS":"FAIL"), a.padEnd(13),"on",b.padEnd(10), r.toFixed(2), "(min",t+")", P[a],P[b]); }

process.exit(failed ? 1 : 0);
