const {makeInput, makeDoc} = require("./dom_stub.js");
const S = require(process.argv[2]);
let fails = 0;
function check(name, cond, extra) {
  console.log((cond ? "PASS " : "FAIL ") + name + (cond ? "" : "  <-- " + JSON.stringify(extra)));
  if (!cond) fails++;
}
function run(js, doc) {
  global.document = doc; global.window = {HTMLInputElement, document: doc};
  global.window.__lumenAutofill = undefined;
  return JSON.parse(eval("(" + js + ")"));
}

// --- 1. classic two-field CAS form, submit allowed -------------------------
let clicked = 0;
const btn = makeInput({tagName: "BUTTON", type: "submit"});
btn.click = () => { clicked++; };
let u = makeInput({id: "username"});
let p = makeInput({id: "password", type: "password"});
let form = {submit: () => { clicked += 100; }};
u.form = form; p.form = form;
let r = run(S.submit, makeDoc([u, p, btn]));
check("fills username", u.value === "u1234567", u.value);
check("fills password", p.value === "s3cret", p.value);
check("used the NATIVE setter", u._nativeSets === 1 && p._nativeSets === 1, [u._nativeSets, p._nativeSets]);
check("dispatched input+change", JSON.stringify(u.events) === '["input","change"]', u.events);
check("clicked submit once", clicked === 1, clicked);
check("reports selectors not values", r.user === "#username" && r.pass === "#password", r);
check("report carries no secret", !JSON.stringify(r).includes("s3cret"), r);
check("submitted_password true", r.submitted_password === true, r);
check("done after a hit", r.done === true, r);

// --- 2. same form, submit NOT allowed (budget spent) -----------------------
clicked = 0; u = makeInput({id: "username"}); p = makeInput({id: "password", type: "password"});
r = run(S.nosubmit, makeDoc([u, p, btn]));
check("no-submit mode still fills", p.value === "s3cret", p.value);
check("no-submit mode does NOT click", clicked === 0, clicked);
check("no-submit reports pass selector (drives FILL_ONLY)", r.pass === "#password", r);

// --- 3. drifted selectors: only input[type=password] + a name*=user match --
clicked = 0;
u = makeInput({name: "j_user_id"});
p = makeInput({name: "wholly_unexpected", type: "password"});
r = run(S.submit, makeDoc([u, p, btn]));
check("fallback selector finds drifted username", u.value === "u1234567", [u.value, r.user]);
check("fallback input[type=password] finds drifted password", p.value === "s3cret", [p.value, r.pass]);

// --- 4. form inside a same-origin iframe -----------------------------------
clicked = 0;
u = makeInput({id: "username"}); p = makeInput({id: "password", type: "password"});
const inner = makeDoc([u, p, btn]);
const frame = {contentDocument: inner};
const blockedFrame = {get contentDocument() { throw new Error("cross-origin"); }};
r = run(S.submit, makeDoc([], [frame, blockedFrame]));
check("reaches into a same-origin iframe", p.value === "s3cret", p.value);
check("counts frames", r.frames === 2, r.frames);
check("counts the cross-origin frame as blocked", r.blocked_frames === 1, r.blocked_frames);

// --- 5. username-only page (step 1 of two-page CAS) ------------------------
clicked = 0;
u = makeInput({id: "username"});
r = run(S.useronly, makeDoc([u, btn]));
check("username-only page fills", u.value === "u1234567", u.value);
check("username-only page advances", r.submitted_user === true && clicked === 1, [r, clicked]);
check("username-only page does NOT spend the password budget",
      r.submitted_password === false, r);

// --- 6. form mounts LATE: nothing at load, appears on a later tick ---------
clicked = 0;
u = makeInput({id: "username"}); p = makeInput({id: "password", type: "password"});
const late = makeDoc([]);
r = run(S.submit, late);
check("empty page reports no hit yet", r.done === false && r.user === "", r);
late._inputs = [u, p, btn];          // the SPA mounts its form
global.__tick();
const settled = JSON.parse(eval("(" + S.probe + ")")) && global.window.__lumenAutofill.report;
check("retry tick fills the late form", p.value === "s3cret", p.value);
check("retry tick submits it", clicked === 1, clicked);
check("settled report is done", settled.done === true, settled);
check("tries counted > 1", settled.tries > 1, settled.tries);

// --- 7. idempotence: re-injection must not double-submit -------------------
clicked = 0;
u = makeInput({id: "username"}); p = makeInput({id: "password", type: "password"});
const doc7 = makeDoc([u, p, btn]);
global.document = doc7; global.window = {HTMLInputElement, document: doc7};
global.window.__lumenAutofill = undefined;
eval("(" + S.submit + ")");
const first = p._nativeSets, firstClicks = clicked;
eval("(" + S.submit + ")");          // loadFinished fires twice
check("re-injection does not refill", p._nativeSets === first, [first, p._nativeSets]);
check("re-injection does not double-submit", clicked === firstClicks, clicked);

// --- 8. probe never leaks a value -----------------------------------------
u = makeInput({id: "username"}); u._value = "typed-by-hand";
p = makeInput({id: "password", type: "password"}); p._value = "TOP-SECRET";
global.document = makeDoc([u, p]); global.window = {HTMLInputElement};
const probe = eval("(" + S.probe + ")");
check("probe reports fields", JSON.parse(probe).inputs.length === 2, probe);
check("probe leaks NO typed value",
      !probe.includes("TOP-SECRET") && !probe.includes("typed-by-hand"), probe);

// --- 9. read_fields_js DOES return values (it is the capture path) ---------
const read = JSON.parse(eval("(" + S.read + ")"));
check("capture reads what the user typed", read.u === "typed-by-hand" && read.p === "TOP-SECRET", read);

console.log(fails ? "\n" + fails + " FAILING" : "\nall green");
process.exitCode = fails ? 1 : 0;
