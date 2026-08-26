// Minimal DOM good enough to exercise canvas_login's autofill: native value
// descriptor (so setNative's path is the real one), event dispatch, selector
// matching for the handful of selector forms we emit, and iframes.
function makeInput(attrs) {
  const el = Object.create(HTMLInputElement.prototype);
  Object.assign(el, {tagName: "INPUT", _value: "", type: "text", id: "", name: "",
                     disabled: false, readOnly: false, events: [], form: null,
                     _attrs: {}, placeholder: ""}, attrs);
  el.getBoundingClientRect = () => ({width: 200, height: 20});
  el.getAttribute = (k) => el._attrs[k] || null;
  el.focus = () => {}; el.blur = () => {};
  el.dispatchEvent = (e) => { el.events.push(e.type); return true; };
  return el;
}
global.HTMLInputElement = function () {};
HTMLInputElement.prototype = {};
// The real thing: a native accessor, exactly what a framework would shadow.
Object.defineProperty(HTMLInputElement.prototype, "value", {
  get() { return this._value; },
  set(v) { this._nativeSets = (this._nativeSets || 0) + 1; this._value = v; },
  configurable: true,
});
global.Event = class { constructor(t, o) { this.type = t; this.bubbles = !!(o && o.bubbles); } };
global.MutationObserver = class { constructor(cb) { this.cb = cb; } observe() {} disconnect() { this.disconnected = true; } };
global.location = {href: "https://shib.utah.edu/idp/profile/SAML2/Redirect/SSO?x=1"};
let timers = [];
global.setInterval = (fn, ms) => { timers.push(fn); return timers.length; };
global.clearInterval = () => {};
global.__tick = () => timers.forEach(fn => fn());

function matches(el, sel) {
  if (sel.startsWith("#")) return el.id === sel.slice(1);
  let m = sel.match(/^input\[(\w+)([*^]?)=([^\]]+?)(\s+i)?\]$/);
  if (!m) return false;
  let [, attr, op, want] = m;
  want = want.replace(/^["']|["']$/g, "");
  const have = String(attr === "autocomplete" ? (el.getAttribute("autocomplete") || "")
                      : (el[attr] || ""));
  if (op === "*") return have.toLowerCase().includes(want.toLowerCase());
  return have === want;
}
function makeDoc(inputs, frames) {
  const doc = {
    _inputs: inputs, _frames: frames || [],
    querySelector(sel) { return doc._inputs.find(el => matches(el, sel)) || null; },
    querySelectorAll(sel) {
      if (sel.includes("iframe")) return doc._frames;
      return doc._inputs.filter(el => ["input", "select", "textarea"].some(t => sel.includes(t)));
    },
  };
  doc.documentElement = {};
  return doc;
}
module.exports = {makeInput, makeDoc};
