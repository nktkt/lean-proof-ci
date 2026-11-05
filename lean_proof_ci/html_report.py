"""Self-contained, offline audit reports. No CDN, network calls, or embedded user JavaScript."""

from __future__ import annotations

from difflib import SequenceMatcher
from hashlib import sha256
from html import escape
import json
import re
from typing import Any


def text(value: Any) -> str:
    if value is None:
        return "—"
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, indent=2)
    return escape(value, quote=True)


def anchor(name: str) -> str:
    return "decl-" + sha256(name.encode()).hexdigest()[:16]


def statement_diff(before: str, after: str) -> tuple[str, str]:
    """Display-only word diff; the audit decision uses the elaborated type hashes."""
    if len(before) + len(after) > 50000:
        return text(before), text(after)
    a, b = re.findall(r"\s+|\S+", before), re.findall(r"\s+|\S+", after)
    old, new = [], []
    for tag, i, j, k, l in SequenceMatcher(a=a, b=b).get_opcodes():
        left, right = text("".join(a[i:j])), text("".join(b[k:l]))
        old.append(f"<del>{left}</del>" if tag in {"delete", "replace"} else left)
        new.append(f"<ins>{right}</ins>" if tag in {"insert", "replace"} else right)
    return "".join(old), "".join(new)


def html_report(report: dict[str, Any]) -> str:
    snapshot = report.get("snapshot") or {}
    declarations = snapshot.get("declarations", [])
    findings, changes = report.get("findings", []), report.get("changes", [])
    affected = {f["declaration"] for f in findings}
    changed = {c["declaration"] for c in changes}
    status = report["status"] if report["status"] in {"pass", "fail", "error"} else "error"
    heading = {"pass": "Audit passed", "fail": "Review required", "error": "Inspection failed"}[status]
    baseline = report.get("baseline")
    parts = [f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{heading} · Lean Proof CI</title><style>{STYLE}</style></head><body>
<header><div class="wrap"><div class="brand"><span class="symbol" aria-hidden="true">⊢</span> Lean Proof CI <span class="eyebrow">PROOF AUDIT</span></div>
<div class="hero"><div><p class="eyebrow">COMPILED DECLARATIONS · EXPLICIT ASSUMPTIONS</p><h1>{heading}</h1>
<p class="subtitle">{'Review the evidence and changes below.' if status != 'pass' else 'No violations of the configured policy were found.'}</p></div>
<span class="status {status}">{status.upper()}</span></div>
<div class="meta"><span>Lean <strong>{text(snapshot.get('lean_version'))}</strong></span>
<span>Scope <strong>{text(', '.join(snapshot.get('roots', [])) or 'unavailable')}</strong></span>
<span>{'Compared with a baseline' if baseline else 'Axiom audit only · no baseline comparison'}</span></div></div></header>
<main class="wrap"><div class="stats">
<div><span class="stat-value">{len(declarations)}</span><span>Declarations inspected</span></div>
<div><span class="stat-value">{len(findings)}</span><span>Policy violations</span></div>
<div><span class="stat-value">{len(changes)}</span><span>Baseline changes</span></div>
<div><span class="stat-value">{len(snapshot.get('modules', []))}</span><span>Modules in scope</span></div></div>''']
    if baseline:
        parts.append(f'<div class="baseline"><strong>Compared against</strong><code>{text(baseline)}</code></div>')
    if report.get("error"):
        parts.append(f'<section class="panel"><h2>Operational error</h2><p>The audit did not complete. This is not a passing result.</p><pre>{text(report["error"])}</pre></section>')
    if findings:
        parts.append('<section id="findings" class="panel"><div class="section-title"><h2>Policy violations</h2><span>Follow a dependency to its assumption</span></div>')
        for f in findings:
            parts.append(f'<article class="finding"><a class="decl-name" href="#{anchor(f["declaration"])}">{text(f["declaration"])}</a><span class="badge issue">{text(f["code"].replace("_", " "))}</span><p>{text(f["message"])}</p>')
            targets = f.get("axioms", ["sorryAx"])
            for axiom in targets:
                path = f.get("paths", {}).get(axiom)
                if path:
                    parts.append('<ol class="path" aria-label="Dependency path">' + ''.join(f'<li><code>{text(n)}</code></li>' for n in path) + '</ol>')
                else:
                    parts.append(f'<p class="muted">Dependency on {text(axiom)} was detected; no path was captured (older snapshot or explanation limit).</p>')
            parts.append('</article>')
        parts.append('</section>')
    if changes:
        parts.append('<section id="changes" class="panel"><div class="section-title"><h2>Changes from baseline</h2><span>Before and after</span></div>')
        for c in changes:
            before, after = text(c["before"]), text(c["after"])
            if c["code"] == "statement_changed":
                before, after = statement_diff(c["before"], c["after"])
            parts.append(f'<article class="change"><div class="change-title"><strong>{text(c["declaration"] or "Project configuration")}</strong><span class="badge {"issue" if c["fails"] else "info"}">{text(c["code"].replace("_", " "))}</span></div><div class="diff"><div><span class="eyebrow">BEFORE</span><pre>{before}</pre></div><div><span class="eyebrow">AFTER</span><pre>{after}</pre></div></div>')
            if c["code"] == "statement_changed" and c["before"] == c["after"]:
                parts.append('<p class="muted">The elaborated type changed even though its pretty-printed statement is unchanged. Inspect implicit arguments and universe levels.</p>')
                parts.append(f'<pre>Before: {text(c.get("before_hash"))}\nAfter:  {text(c.get("after_hash"))}</pre>')
            parts.append(f'<p class="muted">{"Requires review; fails CI." if c["fails"] else "Informational; does not fail CI."}</p></article>')
        parts.append('</section>')
    if declarations:
        parts.append('''<section class="panel" id="inventory"><div class="section-title"><h2>Declaration inventory</h2><span>Expand to inspect a statement</span></div>
<div class="toolbar"><div class="search"><label for="search">Find a declaration</label><input id="search" type="search" placeholder="Name, statement, module or axiom…" autocomplete="off"></div>
<div><label for="filter">Show</label><select id="filter"><option value="all">All declarations</option><option value="issues">With violations</option><option value="changed">Changed declarations</option><option value="theorem">Theorems</option><option value="definition">Definitions</option></select></div></div>
<p id="visible-count" class="muted" aria-live="polite"></p><div class="declarations">''')
        for d in declarations:
            issue, change = d["name"] in affected, d["name"] in changed
            search = ' '.join([d["name"], d["module"], d["statement"], *d["axioms"]])
            location = d["module"] + (f" : {d['line']}" if d.get("line") else "")
            badge = '<span class="badge issue">violation</span>' if issue else ('<span class="badge info">changed</span>' if change else '')
            parts.append(f'<details class="declaration" id="{anchor(d["name"])}" data-search="{text(search)}" data-kind="{text(d["kind"])}" data-issue="{str(issue).lower()}" data-changed="{str(change).lower()}"><summary><span class="disclosure" aria-hidden="true">›</span><div><span class="decl-name">{text(d["name"])}</span><span class="location">{text(location)}</span></div><span class="kind">{text(d["kind"])}</span>{badge}</summary><div class="declaration-body"><span class="eyebrow">STATEMENT</span><pre>{text(d["statement"])}</pre><p><strong>Axioms</strong> <code>{text(", ".join(d["axioms"]) or "none")}</code></p><details class="fingerprints"><summary>Structural fingerprints</summary><pre>Statement: {text(d["statement_hash"])}\nDefinition: {text(d["definition_hash"])}</pre></details></div></details>')
        parts.append('</div><p id="empty-state" hidden>No declarations match these filters.</p><noscript><p>Search and filters require JavaScript. All declarations remain readable below.</p></noscript></section>')
    parts.append(f'''<footer><strong>What this report establishes</strong><p>The compiled declarations were audited under the configured axiom policy. This does not establish agreement with an informal specification. Dependency paths explain violations; their search is bounded independently of the axiom audit.</p><span>Lean Proof CI · snapshot schema {text(snapshot.get('schema_version', '—'))} · offline report</span></footer></main><script>{SCRIPT}</script></body></html>''')
    return '\n'.join(parts)


STYLE = """
:root{color-scheme:light;--ink:#18243b;--muted:#5d687a;--line:#e1e6ee;--blue:#274ec2;--surface:#fff}
*{box-sizing:border-box}body{margin:0;background:#f4f6fa;color:var(--ink);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.wrap{width:min(1120px,calc(100% - 48px));margin:auto}header{background:#142139;color:#f6f8ff;padding:27px 0 32px}.brand{display:flex;align-items:center;gap:12px;font-size:17px;font-weight:650}.brand .eyebrow{margin-left:auto;color:#a2b2cd}.symbol{display:grid;place-items:center;width:34px;height:34px;border:1px solid #7183a1;border-radius:8px;font:24px Georgia,serif}.eyebrow{font-size:11px;font-weight:700;letter-spacing:.1em;color:var(--muted)}header .eyebrow{color:#aabbd6}.hero{display:flex;justify-content:space-between;align-items:center;gap:20px;margin:26px 0 22px}h1{font-size:38px;letter-spacing:-.035em;line-height:1.2;margin:8px 0 12px}.subtitle{margin:0;color:#bac7dc;font-size:16px}.status{padding:9px 20px;border:1px solid;border-radius:999px;font-weight:700;font-size:13px;letter-spacing:.08em}.status.pass{background:#173e38;color:#a7f0cf;border-color:#327063}.status.fail{background:#4a3425;color:#ffd4a1;border-color:#9b693b}.status.error{background:#4c2831;color:#ffc3d0;border-color:#a25568}.meta{display:flex;flex-wrap:wrap;gap:8px 28px;border-top:1px solid #34425b;padding-top:19px;color:#aebed5;font-size:13px}.meta strong{color:#eef3fc;font-weight:500;margin-left:5px}.stats{display:grid;grid-template-columns:repeat(4,1fr);background:var(--surface);border:1px solid var(--line);border-radius:12px;margin:26px 0 20px;padding:22px 0}.stats>div{display:flex;flex-direction:column;padding:0 26px;color:var(--muted);font-size:12px;border-left:1px solid var(--line)}.stats>div:first-child{border:0}.stat-value{font-size:30px;font-weight:650;line-height:1.4;color:var(--ink);font-variant-numeric:tabular-nums}.baseline{display:flex;gap:16px;align-items:baseline;padding:3px 3px 22px;font-size:12px;color:var(--muted)}.baseline strong{white-space:nowrap}.baseline code{overflow-wrap:anywhere}.panel{background:var(--surface);border:1px solid var(--line);border-radius:12px;margin:0 0 22px;padding:26px}.section-title{display:flex;justify-content:space-between;align-items:baseline;gap:16px;margin-bottom:20px}.section-title>span{font-size:12px;color:var(--muted)}h2{font-size:19px;font-weight:650;margin:0;letter-spacing:-.02em}.decl-name{font:600 14px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}.finding,.change{border-top:1px solid var(--line);padding:20px 0 0;margin-top:20px}.finding .badge{margin-left:12px}.finding p{margin:9px 0}.badge{display:inline-block;white-space:nowrap;padding:3px 9px;border-radius:5px;font-size:11px;line-height:1.6;font-weight:600}.badge.issue{color:#913a17;background:#fff0e5}.badge.info{color:#28489b;background:#edf2ff}.muted{color:var(--muted);font-size:12px}.path{list-style:none;display:flex;flex-wrap:wrap;gap:6px 0;background:#f8f9fc;border:1px solid var(--line);border-radius:7px;padding:12px 14px;margin:12px 0 0}.path li{font-size:12px;overflow-wrap:anywhere;max-width:100%}.path li+li:before{content:'→';display:inline-block;margin:0 10px;color:#7b889e}.change-title{display:flex;align-items:center;flex-wrap:wrap;gap:12px}.change-title strong{font-size:14px;overflow-wrap:anywhere}.diff{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}.diff>div{min-width:0}pre{font:12px/1.7 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f8fc;padding:14px;border-radius:7px;border:1px solid #e7ebf2;margin:7px 0 0}code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.92em}del{background:#fbdcde;color:#7d2336;text-decoration:line-through}ins{background:#d8f0e2;color:#155e3a;text-decoration:none}.toolbar{display:flex;gap:16px;align-items:end}.search{flex:1}label{display:block;font-weight:600;font-size:12px;margin-bottom:7px}input,select{width:100%;font:inherit;font-size:13px;padding:11px 13px;border:1px solid #cdd5e2;border-radius:7px;background:#fff;color:var(--ink)}select{min-width:190px}input:focus-visible,select:focus-visible,summary:focus-visible,a:focus-visible{outline:3px solid #759aff;outline-offset:3px}.declarations{border-top:1px solid var(--line)}.declaration{border-bottom:1px solid var(--line);scroll-margin-top:20px}.declaration>summary{list-style:none;display:flex;align-items:center;gap:12px;padding:17px 0;cursor:pointer}.declaration>summary::-webkit-details-marker{display:none}.declaration summary>div{flex:1;min-width:0}.location{display:block;color:var(--muted);font-size:11px;margin-top:3px}.kind{color:var(--muted);font-size:12px}.disclosure{font-size:24px;line-height:1;width:15px;color:#71829a;transition:transform .12s}.declaration[open] .disclosure{transform:rotate(90deg)}.declaration-body{padding:0 0 20px 27px}.declaration-body p{font-size:12px}.declaration-body strong{margin-right:8px}.fingerprints>summary{cursor:pointer;color:var(--muted);font-size:12px}.fingerprints pre{font-size:11px}footer{padding:10px 3px 40px;color:var(--muted);font-size:12px}footer strong{color:#3d4c64}footer p{max-width:850px;margin:7px 0 15px}footer span{font-size:11px}[hidden]{display:none!important}
@media(max-width:700px){.wrap{width:calc(100% - 28px)}header{padding:20px 0 24px}h1{font-size:30px}.hero{align-items:flex-start}.status{padding:7px 12px;font-size:11px}.brand .eyebrow{display:none}.stats{grid-template-columns:repeat(2,1fr);row-gap:22px}.stats>div:nth-child(3){border:0}.panel{padding:19px}.section-title{display:block}.section-title>span{display:block;margin-top:5px}.toolbar{display:block}.toolbar>div+div{margin-top:12px}.diff{grid-template-columns:1fr}.baseline{display:block}.baseline code{display:block;margin-top:4px}.kind{display:none}.declaration>summary{gap:8px}.finding .badge{margin:8px 0 0;display:table}}
@media print{body{background:#fff}header{color:var(--ink);background:#fff;border-bottom:1px solid var(--line)}header .eyebrow,.subtitle,.meta,.meta strong{color:var(--muted)}.toolbar,#visible-count{display:none}.wrap{width:100%}.panel,.stats{break-inside:avoid}.declaration-body{display:block}.status{color:var(--ink)!important;background:none!important}}
"""


SCRIPT = """
(() => {
  const search = document.querySelector('#search');
  const filter = document.querySelector('#filter');
  if (!search || !filter) return;
  const rows = [...document.querySelectorAll('.declaration')];
  const apply = () => {
    const query = search.value.trim().toLocaleLowerCase();
    const mode = filter.value;
    let visible = 0;
    for (const row of rows) {
      const match = row.dataset.search.toLocaleLowerCase().includes(query);
      const selected = mode === 'all' || (mode === 'issues' && row.dataset.issue === 'true') ||
        (mode === 'changed' && row.dataset.changed === 'true') || mode === row.dataset.kind;
      row.hidden = !(match && selected);
      if (!row.hidden) visible++;
    }
    document.querySelector('#visible-count').textContent = `Showing ${visible} of ${rows.length} declarations`;
    document.querySelector('#empty-state').hidden = visible !== 0;
  };
  search.addEventListener('input', apply);
  filter.addEventListener('change', apply);
  const reveal = () => {
    const target = document.getElementById(location.hash.slice(1));
    if (target && target.classList.contains('declaration')) {
      search.value = ''; filter.value = 'all'; apply(); target.open = true;
      target.scrollIntoView({block: 'start'});
    }
  };
  window.addEventListener('hashchange', reveal);
  apply(); reveal();
})();
"""
