#!/usr/bin/env python3
"""Reconnaissance tool for an authenticated AFAS InSite session.

Reuses the profile written by ``bootstrap_session.py`` and dumps what a
selector strategy needs to know. InSite renders form controls as custom
elements whose real ``<input>`` lives in a shadow root, so a plain
``document.querySelectorAll`` sees the wrappers and nothing useful -- every
walk here descends through open shadow roots.

Read-only. It never clicks a submit control.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from afas_declaraties.session import open_session  # noqa: E402

logger = logging.getLogger("explore")

DEFAULT_PROFILE = REPO_ROOT / "browser-profile"
OUT_DIR = REPO_ROOT / "artifacts"

# Walks light DOM and open shadow roots together, so a control rendered inside
# <afas-text-input> is reported with the id its shadow <input> actually carries.
JS_DUMP = r"""
() => {
  const out = {links: [], fields: [], customElements: [], frames: [], text: ''};
  const seen = new Set();

  function walk(root, depth) {
    if (!root || depth > 40) return;
    let nodes;
    try { nodes = root.querySelectorAll('*'); } catch (e) { return; }
    for (const el of nodes) {
      if (seen.has(el)) continue;
      seen.add(el);
      const tag = el.tagName.toLowerCase();

      if (tag.includes('-')) {
        const rec = out.customElements.find(c => c.tag === tag);
        if (rec) rec.count++; else out.customElements.push({tag, count: 1});
      }
      if (tag === 'a' && el.href) {
        out.links.push({
          href: el.href,
          text: (el.innerText || el.textContent || '').trim().slice(0, 120),
        });
      }
      if (tag === 'iframe') {
        out.frames.push({src: el.src || '', name: el.name || '', id: el.id || ''});
      }
      if (['input', 'select', 'textarea'].includes(tag)) {
        const opts = tag === 'select'
          ? Array.from(el.options || []).slice(0, 60).map(o => ({value: o.value, label: (o.text || '').trim()}))
          : undefined;
        out.fields.push({
          tag,
          id: el.id || '',
          name: el.name || '',
          type: el.type || '',
          ariaLabel: el.getAttribute('aria-label') || '',
          placeholder: el.placeholder || '',
          value: el.type === 'password' ? '<redacted>' : String(el.value ?? '').slice(0, 80),
          disabled: !!el.disabled,
          readOnly: !!el.readOnly,
          required: !!el.required,
          inShadow: root instanceof ShadowRoot,
          hostTag: root instanceof ShadowRoot ? root.host.tagName.toLowerCase() : '',
          hostId: root instanceof ShadowRoot ? (root.host.id || '') : '',
          options: opts,
        });
      }
      if (el.shadowRoot) walk(el.shadowRoot, depth + 1);
    }
  }

  walk(document, 0);
  out.text = (document.body ? document.body.innerText : '').slice(0, 6000);
  out.title = document.title;
  out.url = location.href;
  return out;
}
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default="/", help="path or full URL to visit")
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--out", type=Path, default=None, help="write JSON here")
    parser.add_argument("--shot", type=Path, default=None, help="write a screenshot here")
    parser.add_argument("--wait", type=int, default=6, help="settle seconds after load")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--net", action="store_true", help="record XHR/fetch traffic")
    args = parser.parse_args()

    logging.basicConfig(level="INFO", format="%(levelname)-7s %(message)s")
    OUT_DIR.mkdir(exist_ok=True)

    host = os.environ.get("INSITE_HOST")
    if not host:
        logger.error("INSITE_HOST is required")
        return 2
    url = args.path if args.path.startswith("http") else f"https://{host}{args.path}"

    calls: list[dict] = []
    with open_session(host, profile=args.profile, headless=not args.headed) as (context, page):
        if args.net:

            def on_response(resp):
                if resp.request.resource_type in ("xhr", "fetch"):
                    calls.append(
                        {
                            "method": resp.request.method,
                            "url": resp.url,
                            "status": resp.status,
                            "type": resp.request.resource_type,
                        }
                    )

            page.on("response", on_response)

        logger.info("visiting %s", url)
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_load_state("networkidle", timeout=args.wait * 1000)
        except Exception:
            logger.debug("networkidle not reached; continuing")
        page.wait_for_timeout(1500)

        data = page.evaluate(JS_DUMP)
        data["xhr"] = calls

        if args.shot:
            page.screenshot(path=str(args.shot), full_page=True)
            logger.info("screenshot -> %s", args.shot)

        out = args.out or (OUT_DIR / "dump.json")
        out.write_text(json.dumps(data, indent=1, ensure_ascii=False))
        logger.info(
            "%s | %d links, %d fields, %d custom elements, %d frames, %d xhr -> %s",
            data.get("title"),
            len(data["links"]),
            len(data["fields"]),
            len(data["customElements"]),
            len(data["frames"]),
            len(calls),
            out,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
