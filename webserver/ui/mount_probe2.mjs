import { JSDOM } from 'jsdom';
import fs from 'fs';
const appJs = fs.readFileSync('/tmp/served_app.js', 'utf8');
const dom = new JSDOM('<!doctype html><html><body><div id="root"></div></body></html>', {
  url: 'http://127.0.0.1:5050/', runScripts: 'outside-only', pretendToBeVisual: true,
});
const { window } = dom;
const g = window;
g.matchMedia = () => ({ matches:false, media:'', onchange:null, addEventListener(){}, removeEventListener(){}, addListener(){}, removeListener(){}, dispatchEvent(){return false;} });
g.EventSource = class { constructor(){this.readyState=0;} addEventListener(){} removeEventListener(){} close(){} };
g.scrollTo = () => {}; g.scroll = () => {};
g.fetch = () => new Promise(()=>{});
g.IntersectionObserver = class { observe(){} unobserve(){} disconnect(){} };
g.ResizeObserver = class { observe(){} unobserve(){} disconnect(){} };
window.document.queryCommandSupported = () => false;
window.document.execCommand = () => false;
window.document.queryCommandState = () => false;
if (!g.crypto) g.crypto = {};
if (!g.crypto.randomUUID) g.crypto.randomUUID = () => '00000000-0000-4000-8000-000000000000';
if (!g.crypto.getRandomValues) g.crypto.getRandomValues = (a)=>{for(let i=0;i<a.length;i++)a[i]=Math.floor(Math.random()*256);return a;};
const errors = [];
g.addEventListener('error', e => errors.push('window.error: ' + (e.error?.stack || e.message)));
g.onerror = (m,s,l,c,err) => errors.push('onerror: ' + (err?.stack || m));
const oe = console.error; console.error = (...a)=>errors.push('console.error: '+a.map(String).join(' ').slice(0,400));
try { window.eval(appJs); } catch (e) { errors.push('THROW at eval: ' + (e.stack||e.message)); }
await new Promise(r=>setTimeout(r,2000));
console.error = oe;
const root = window.document.getElementById('root').innerHTML;
console.log('MOUNTED?', root.length>0, '| root length:', root.length);
console.log('root head:', JSON.stringify(root.slice(0,180)));
console.log('--- errors ('+errors.length+') ---');
errors.slice(0,10).forEach(e=>console.log(e.split('\n').slice(0,3).join(' | ').slice(0,500)));
