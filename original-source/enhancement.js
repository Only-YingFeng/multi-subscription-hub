/* Shared routing engine. No network access, file access, or node credentials. */
function czoTransform(config, metadata) {
  const ns = "czo.v1.";
  const clone = x => JSON.parse(JSON.stringify(x));
  config = clone(config);
  if (config.mode && config.mode !== "rule") throw new Error("CZO requires rule mode");
  if (!metadata || !Array.isArray(metadata.entries) || !metadata.policy || typeof metadata.policy !== "object") throw new Error("Invalid metadata");
  const ports = new Set(), ids = new Set();
  for (const e of metadata.entries) {
    const id = typeof e.id === "string" ? e.id.slice(0, 20) : "";
    if (!id || !Number.isInteger(e.port) || e.port < 1 || e.port > 65535 || ports.has(e.port) || ids.has(id)) throw new Error("Invalid or duplicate identity/port");
    if (typeof e.nodeName !== "string" || typeof e.active !== "boolean") throw new Error("Invalid node binding");
    ports.add(e.port); ids.add(id);
  }
  const actions = new Set(["DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", "PROXY"]);
  for (const key of Object.keys(metadata.policy)) if (!actions.has(metadata.policy[key])) throw new Error("Invalid routing policy");
  if (metadata.fallback && !["PROXY", "DIRECT"].includes(metadata.fallback)) throw new Error("Invalid fallback");
  config.listeners = (config.listeners || []).filter(x => !x.name.startsWith(ns));
  config["proxy-groups"] = (config["proxy-groups"] || []).filter(x => !x.name.startsWith(ns));
  const sub = config["sub-rules"] || {};
  if (typeof sub !== "object" || Array.isArray(sub)) throw new Error("Invalid sub-rule map");
  Object.keys(sub).filter(k => k.startsWith(ns)).forEach(k => delete sub[k]);
  config["sub-rules"] = sub;
  const available = new Map((config.proxies || []).map(x => [x.name, x]));
  const dispatch = [];
  for (const e of metadata.entries) {
    const alive = e.active && available.has(e.nodeName) && (!e.endpointTag || czoEndpointTag(available.get(e.nodeName)) === e.endpointTag);
    const listener = ns + "in." + e.id.slice(0, 20);
    const binding = ns + "bind." + e.id.slice(0, 20);
    config["proxy-groups"].push({name: binding, type: "select", proxies: [alive ? e.nodeName : "REJECT"], hidden: true});
    config.listeners.push({name: listener, type: "socks", listen: "127.0.0.1", port: e.port, udp: false, rule: alive ? ns + "rules" : ns + "dead"});
    dispatch.push("IN-NAME," + listener + "," + binding);
  }
  dispatch.push("MATCH,REJECT");
  sub[ns + "dispatch"] = dispatch;
  sub[ns + "dead"] = ["MATCH,REJECT"];

  function splitRule(text) {
    let depth = 0, start = 0;
    const out = [];
    for (let i = 0; i < text.length; i++) {
      const c = text[i];
      if (c === "(") depth++;
      if (c === ")") depth--;
      if (depth < 0) throw new Error("Unbalanced rule");
      if (c === "," && depth === 0) { out.push(text.slice(start, i).trim()); start = i + 1; }
    }
    if (depth !== 0) throw new Error("Unbalanced rule");
    out.push(text.slice(start).trim());
    return out;
  }
  const ordinary = new Set(["DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "IP-CIDR", "IP-CIDR6", "GEOIP", "GEOSITE", "IP-ASN", "SRC-IP-CIDR", "SRC-PORT", "DST-PORT", "PROCESS-NAME", "PROCESS-PATH", "NETWORK", "IN-NAME", "IN-TYPE", "RULE-SET", "UID", "DSCP"]);
  const complex = new Set(["AND", "OR", "NOT", "DOMAIN-REGEX", "PROCESS-NAME-REGEX", "PROCESS-PATH-REGEX"]);
  const originalSubs = clone(sub);
  const seen = new Set();
  let serial = 0;
  function convertRules(rules, path) {
    if (!Array.isArray(rules)) throw new Error("Invalid rules");
    const result = [];
    for (const text of rules) {
      if (typeof text !== "string") throw new Error("Non-string rule");
      if (/REGEX/.test(text)) throw new Error("Regex rules require a separate syntax audit");
      const p = splitRule(text), type = p[0];
      if (type === "SUB-RULE") {
        if (p.length !== 3 || !originalSubs[p[2]] || seen.has(p[2])) throw new Error("Invalid or cyclic sub-rule");
        const newName = ns + "nested." + (++serial);
        seen.add(p[2]); sub[newName] = convertRules(originalSubs[p[2]], path + "/" + p[2]); seen.delete(p[2]);
        result.push("SUB-RULE," + p[1] + "," + newName);
        continue;
      }
      let targetIndex;
      if (type === "MATCH") { if (p.length !== 2) throw new Error("Invalid MATCH"); targetIndex = 1; }
      else if (ordinary.has(type)) { if (p.length < 3 || !p[1] || !p[2]) throw new Error("Invalid rule"); targetIndex = 2; }
      else if (complex.has(type)) { if (p.length !== 3) throw new Error("Invalid complex rule"); targetIndex = 2; }
      else throw new Error("Unsupported rule type: " + type);
      const target = p[targetIndex];
      let direct = ["DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE"].includes(target) ? target : Object.prototype.hasOwnProperty.call(metadata.policy, target) ? metadata.policy[target] : null;
      if (!direct) throw new Error("Unaudited rule target: " + target);
      if (type === "MATCH" && !["REJECT", "REJECT-DROP", "PASS"].includes(direct)) direct = metadata.fallback || "PROXY";
      if (direct !== "PROXY") {
        p[targetIndex] = direct;
        result.push(p.join(","));
      } else {
        const predicate = type === "MATCH" ? "OR,((NETWORK,tcp),(NETWORK,udp))" : p.filter((_, i) => i !== targetIndex).join(",");
        result.push("SUB-RULE,(" + predicate + ")," + ns + "dispatch");
        // Guard against unsupported protocols/adapters falling through to DIRECT.
        p[targetIndex] = "REJECT";
        result.push(p.join(","));
      }
    }
    result.push("MATCH,REJECT");
    return result;
  }
  sub[ns + "rules"] = convertRules(config.rules || [], "main");
  return config;
}

// Endpoint change detector, excludes passwords/UUIDs/URLs. Not an authentication hash.
function czoEndpointTag(node) {
  const text = [node.type || "", node.server || "", String(node.port || "")].join("\u0000");
  let a = 2166136261, b = 2246822507;
  for (let i = 0; i < text.length; i++) {
    const c = text.charCodeAt(i);
    a = Math.imul(a ^ c, 16777619) >>> 0;
    b = Math.imul(b ^ c, 3266489909) >>> 0;
  }
  return a.toString(16).padStart(8, "0") + b.toString(16).padStart(8, "0");
}
