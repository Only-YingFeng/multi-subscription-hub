"use strict";

// Offline only: synthetic node names and endpoints; never reads private profiles.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const sourcePath = path.join(__dirname, "..", "enhancement.js");
const sandbox = {};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(sourcePath, "utf8"), sandbox, { filename: sourcePath });
const transform = (config, metadata) => JSON.parse(JSON.stringify(sandbox.czoTransform(config, metadata)));
const NS = "czo.v1.";
const clone = x => JSON.parse(JSON.stringify(x));
const entry = (id, name, port, active = true) => ({ id, nodeName: name, port, active });

function fixture() {
  return {
    config: {
      mode: "rule",
      proxies: [
        { name: "synthetic,node-A", type: "socks5", server: "192.0.2.10", port: 1080 },
        { name: "synthetic-node-B", type: "socks5", server: "192.0.2.20", port: 1080 },
      ],
      "proxy-groups": [{ name: "OriginalGroup", type: "select", proxies: ["synthetic-node-B"] }],
      listeners: [{ name: "unrelated-listener", type: "socks", listen: "127.0.0.1", port: 12000 }],
      rules: [
        "DOMAIN,blocked.example,REJECT",
        "DOMAIN,proxy.example,OriginalGroup",
        "DOMAIN-SUFFIX,domestic.example,DIRECT",
        "IP-CIDR,192.0.2.0/24,OriginalGroup,no-resolve",
        "GEOIP,CN,DIRECT,no-resolve",
        "MATCH,OriginalGroup",
      ],
    },
    metadata: {
      fallback: "PROXY",
      policy: { OriginalGroup: "PROXY" },
      entries: [entry("aaaaaaaaaaaaaaaaaaaaaaaa", "synthetic,node-A", 13001), entry("bbbbbbbbbbbbbbbbbbbbbbbb", "synthetic-node-B", 13002)],
    },
  };
}

// Independent evaluator for the subset exercised here, using Mihomo's target
// positions rather than the enhancement's depth-based splitter.
function parseRule(line, needTarget = true) {
  const parts = line.split(",").map(x => x.trim());
  const type = parts.shift().toUpperCase();
  if (type === "MATCH") return { type, target: parts[0], payload: "" };
  if (["SUB-RULE", "AND", "OR", "NOT", "DOMAIN-REGEX", "PROCESS-NAME-REGEX", "PROCESS-PATH-REGEX"].includes(type)) {
    return { type, target: needTarget ? parts.pop() : "", payload: parts.join(",") };
  }
  const payload = parts.shift();
  return { type, payload, target: needTarget ? parts.shift() : "", params: parts };
}

function children(payload) {
  assert.equal(payload[0], "(");
  assert.equal(payload.at(-1), ")");
  const inside = payload.slice(1, -1);
  const result = [];
  let depth = 0, start = -1;
  for (let i = 0; i < inside.length; i++) {
    if (inside[i] === "(") { if (depth++ === 0) start = i + 1; }
    else if (inside[i] === ")") { if (--depth === 0) result.push(inside.slice(start, i)); }
    else if (depth === 0) assert.match(inside[i], /[,\s]/);
    assert.ok(depth >= 0);
  }
  assert.equal(depth, 0);
  return result;
}

function condition(rule, request) {
  const { type, payload } = rule;
  if (type === "MATCH") return true;
  if (type === "DOMAIN") return request.host === payload;
  if (type === "DOMAIN-SUFFIX") return request.host === payload || request.host.endsWith("." + payload);
  if (type === "DOMAIN-KEYWORD") return request.host.includes(payload);
  if (type === "PROCESS-NAME") return request.process === payload;
  if (type === "IN-NAME") return payload.split("/").includes(request.inName);
  if (type === "NETWORK") return request.network.toLowerCase() === payload.toLowerCase();
  if (type === "GEOIP") return request.country === payload;
  if (type === "IP-CIDR" || type === "IP-CIDR6") return (request.cidrs || []).includes(payload);
  if (type === "AND") return children(payload).every(x => condition(parseRule(x, false), request));
  if (type === "OR") return children(payload).some(x => condition(parseRule(x, false), request));
  if (type === "NOT") { const xs = children(payload); assert.equal(xs.length, 1); return !condition(parseRule(xs[0], false), request); }
  throw new Error("Evaluator unsupported type: " + type);
}

function resolve(config, port, details = {}, unsupportedUDP = new Set()) {
  const listener = config.listeners.find(x => x.port === port);
  assert.ok(listener, "expected synthetic listener exists");
  const request = { host: "unknown.example", network: "tcp", inName: listener.name, ...details };
  const groups = new Map(config["proxy-groups"].map(x => [x.name, x]));
  const realTarget = target => groups.has(target) ? groups.get(target).proxies[0] : target;
  function match(rules, depth = 0) {
    assert.ok(depth < 20, "sub-rule graph must be acyclic");
    for (const line of rules) {
      const rule = parseRule(line);
      let target;
      if (rule.type === "SUB-RULE") {
        const xs = children("(" + rule.payload + ")");
        assert.equal(xs.length, 1);
        if (!condition(parseRule(xs[0], false), request)) continue;
        target = match(config["sub-rules"][rule.target], depth + 1);
        if (!target) continue;
      } else {
        if (!condition(rule, request)) continue;
        target = realTarget(rule.target);
      }
      if (request.network === "udp" && unsupportedUDP.has(target)) continue;
      return target;
    }
    return null;
  }
  return match(config["sub-rules"][listener.rule]) || "DIRECT";
}

const tests = [];
function test(name, fn) { tests.push([name, fn]); }
function assertBlockedCases(cases) {
  const accepted = [];
  for (const [name, action] of cases) {
    const f = fixture(); action(f);
    try { transform(f.config, f.metadata); accepted.push(name); }
    catch (_) { /* The transformer must block before producing config. */ }
  }
  assert.deepEqual(accepted, [], "generation unexpectedly accepted: " + accepted.join(", "));
}
test("main rules, original groups and input remain unchanged", () => {
  const { config, metadata } = fixture(), before = clone(config), result = transform(config, metadata);
  assert.deepEqual(config, before);
  assert.deepEqual(result.rules, before.rules);
  assert.deepEqual(result["proxy-groups"].find(x => x.name === "OriginalGroup"), before["proxy-groups"][0]);
  assert.deepEqual(result.listeners.find(x => x.name === "unrelated-listener"), before.listeners[0]);
});
test("dedicated listeners bind IPv4 loopback, use rules and disable UDP", () => {
  const { config, metadata } = fixture(), result = transform(config, metadata);
  const xs = result.listeners.filter(x => x.name.startsWith(NS));
  assert.equal(xs.length, metadata.entries.length);
  xs.forEach(x => { assert.equal(x.type, "socks"); assert.equal(x.listen, "127.0.0.1"); assert.equal(x.udp, false); assert.equal(x.proxy, undefined); });
});
test("each entry has one immutable-choice alias even when node name contains comma", () => {
  const { config, metadata } = fixture(), result = transform(config, metadata);
  for (const e of metadata.entries) {
    const group = result["proxy-groups"].find(x => x.name === NS + "bind." + e.id.slice(0, 20));
    assert.equal(group.type, "select"); assert.equal(group.hidden, true); assert.deepEqual(group.proxies, [e.nodeName]);
    assert.equal(resolve(result, e.port, { host: "proxy.example" }), e.nodeName);
  }
});
test("explicit DIRECT and REJECT retain order and meaning", () => {
  const { config, metadata } = fixture(), result = transform(config, metadata);
  assert.equal(resolve(result, 13001, { host: "blocked.example" }), "REJECT");
  assert.equal(resolve(result, 13001, { host: "x.domestic.example" }), "DIRECT");
  assert.equal(resolve(result, 13001, { host: "foreign.example" }), "synthetic,node-A");
});
test("proxy rule wins over later overlapping DIRECT", () => {
  const { config, metadata } = fixture();
  config.rules = ["DOMAIN-SUFFIX,example,OriginalGroup", "DOMAIN,proxy.example,DIRECT", "MATCH,OriginalGroup"];
  assert.equal(resolve(transform(config, metadata), 13001, { host: "proxy.example" }), "synthetic,node-A");
});
test("unsupported UDP proxy does not fall through to later DIRECT", () => {
  const { config, metadata } = fixture();
  config.rules = ["DOMAIN-SUFFIX,example,OriginalGroup", "DOMAIN,proxy.example,DIRECT", "MATCH,OriginalGroup"];
  assert.equal(resolve(transform(config, metadata), 13001, { host: "proxy.example", network: "udp" }, new Set(["synthetic,node-A"])), "REJECT");
});
test("IP rule modifiers survive conversion in predicate and rejection guard", () => {
  const { config, metadata } = fixture(), rules = transform(config, metadata)["sub-rules"][NS + "rules"];
  assert.ok(rules.includes("SUB-RULE,(IP-CIDR,192.0.2.0/24,no-resolve)," + NS + "dispatch"));
  assert.ok(rules.includes("IP-CIDR,192.0.2.0/24,REJECT,no-resolve"));
});
test("all real rule types convert and route correctly", () => {
  const { config, metadata } = fixture();
  config.rules = ["PROCESS-NAME,synthetic.exe,DIRECT", "DOMAIN-KEYWORD,needle,OriginalGroup", "DOMAIN-SUFFIX,domestic.example,DIRECT", "DOMAIN,blocked.example,REJECT", "IP-CIDR,192.0.2.0/24,OriginalGroup,no-resolve", "IP-CIDR6,2001:db8::/32,OriginalGroup,no-resolve", "GEOIP,CN,DIRECT", "MATCH,OriginalGroup"];
  const result = transform(config, metadata);
  assert.equal(resolve(result, 13001, { process: "synthetic.exe" }), "DIRECT");
  assert.equal(resolve(result, 13001, { host: "needle.example" }), "synthetic,node-A");
  assert.equal(resolve(result, 13001, { cidrs: ["2001:db8::/32"] }), "synthetic,node-A");
  assert.equal(resolve(result, 13001, { country: "CN" }), "DIRECT");
});
test("AND OR NOT conversion syntax is valid and retains predicates", () => {
  const { config, metadata } = fixture();
  config.rules = ["AND,((DOMAIN,proxy.example),(NETWORK,tcp)),OriginalGroup", "OR,((DOMAIN,blocked.example),(DOMAIN,other-blocked.example)),REJECT", "NOT,((DOMAIN,domestic.example)),OriginalGroup", "MATCH,OriginalGroup"];
  const result = transform(config, metadata);
  assert.equal(resolve(result, 13001, { host: "proxy.example" }), "synthetic,node-A");
  assert.equal(resolve(result, 13001, { host: "blocked.example" }), "REJECT");
  assert.equal(resolve(result, 13002, { host: "foreign.example" }), "synthetic-node-B");
});
test("missing and inactive bindings reject even formerly direct destinations", () => {
  for (const action of [f => { f.config.proxies.shift(); }, f => { f.metadata.entries[0].active = false; }]) {
    const f = fixture(); action(f); const result = transform(f.config, f.metadata);
    assert.equal(resolve(result, 13001, { host: "x.domestic.example" }), "REJECT");
    assert.equal(resolve(result, 13001, { host: "foreign.example" }), "REJECT");
  }
});
test("a changed endpoint with the same node name rejects until its binding is regenerated", () => {
  const { config, metadata } = fixture();
  metadata.entries[0].endpointTag = sandbox.czoEndpointTag(config.proxies[0]);
  assert.equal(resolve(transform(config, metadata), 13001), "synthetic,node-A");
  config.proxies[0].server = "192.0.2.30";
  const stale = transform(config, metadata);
  assert.equal(resolve(stale, 13001, { host: "x.domestic.example" }), "REJECT");
  assert.equal(resolve(stale, 13001, { host: "foreign.example" }), "REJECT");
  metadata.entries[0].endpointTag = sandbox.czoEndpointTag(config.proxies[0]);
  assert.equal(resolve(transform(config, metadata), 13001), "synthetic,node-A");
});
test("proxy list reordering cannot reassign existing ports", () => {
  const { config, metadata } = fixture(); config.proxies.reverse();
  const result = transform(config, metadata);
  metadata.entries.forEach(e => assert.equal(resolve(result, e.port), e.nodeName));
});
test("repeated application is idempotent", () => {
  const { config, metadata } = fixture(), first = transform(config, metadata);
  assert.deepEqual(transform(first, metadata), first);
});
test("unknown rule types and unaudited group targets block generation", () => {
  for (const rule of ["UNKNOWN,x,OriginalGroup", "DOMAIN,x,UnauditedGroup"]) {
    const { config, metadata } = fixture(); config.rules = [rule];
    assert.throws(() => transform(config, metadata));
  }
});
test("global and direct modes block generation", () => {
  for (const mode of ["global", "direct"]) {
    const { config, metadata } = fixture(); config.mode = mode;
    assert.throws(() => transform(config, metadata), /rule mode/);
  }
});
test("MATCH fallback always uses fixed node despite frozen DIRECT group leaf", () => {
  const { config, metadata } = fixture(); metadata.policy.OriginalGroup = "DIRECT";
  config.rules = ["DOMAIN-SUFFIX,domestic.example,OriginalGroup", "MATCH,OriginalGroup"];
  const result = transform(config, metadata);
  assert.equal(resolve(result, 13001, { host: "x.domestic.example" }), "DIRECT");
  assert.equal(resolve(result, 13001, { host: "foreign.example" }), "synthetic,node-A");
  config.rules = ["MATCH,DIRECT"];
  assert.equal(resolve(transform(config, metadata), 13001), "synthetic,node-A");
});
test("69 nodes and 1289 source rules grow additively", () => {
  const { config, metadata } = fixture();
  config.proxies = Array.from({ length: 69 }, (_, i) => ({ name: "synthetic-scale-" + i, type: "socks5", server: "192.0.2.10", port: 1080 }));
  metadata.entries = config.proxies.map((x, i) => entry(i.toString(16).padStart(4, "0") + "a".repeat(20), x.name, 14000 + i));
  config.rules = Array.from({ length: 1288 }, (_, i) => "DOMAIN,synthetic-" + i + ".example,OriginalGroup").concat("MATCH,OriginalGroup");
  const result = transform(config, metadata);
  const ruleCount = Object.entries(result["sub-rules"]).filter(([name]) => name.startsWith(NS)).reduce((sum, [, rules]) => sum + rules.length, 0);
  assert.equal(result.rules.length, 1289);
  assert.equal(result.listeners.filter(x => x.name.startsWith(NS)).length, 69);
  assert.ok(ruleCount <= 2 * 1289 + 69 + 4, "sub-rules must grow with R + N rather than R * N");
});
test("duplicate ports and truncated entry-id collisions block generation", () => {
  assertBlockedCases([
    ["duplicate port", f => { f.metadata.entries[1].port = 13001; }],
    ["duplicate id", f => { f.metadata.entries[1].id = f.metadata.entries[0].id; }],
    ["truncated-id collision", f => { f.metadata.entries[1].id = f.metadata.entries[0].id.slice(0, 20) + "cccc"; }],
  ]);
});
test("invalid policy values and inherited property targets block generation", () => {
  assertBlockedCases([
    ["unknown policy string", f => { f.metadata.policy.OriginalGroup = "UNEXPECTED"; }],
    ["boolean policy", f => { f.metadata.policy.OriginalGroup = true; }],
    ["object policy", f => { f.metadata.policy.OriginalGroup = { leaf: "DIRECT" }; }],
    ["inherited policy property", f => { f.config.rules = ["DOMAIN,x,constructor"]; }],
  ]);
});
test("malformed rules, invalid ports and non-map sub-rules block generation", () => {
  assertBlockedCases([
    ["empty rule payload", f => { f.config.rules = ["DOMAIN,,OriginalGroup"]; }],
    ["zero port", f => { f.metadata.entries[0].port = 0; }],
    ["out-of-range port", f => { f.metadata.entries[0].port = 65536; }],
    ["string port", f => { f.metadata.entries[0].port = "13001"; }],
    ["array sub-rules", f => { f.config["sub-rules"] = []; }],
  ]);
});

let failed = 0;
for (const [name, fn] of tests) {
  try { fn(); process.stdout.write("PASS " + name + "\n"); }
  catch (error) { failed++; process.stdout.write("FAIL " + name + ": " + error.message + "\n"); }
}
process.stdout.write(`${tests.length - failed}/${tests.length} passed; ${failed} failed\n`);
process.exitCode = failed ? 1 : 0;
