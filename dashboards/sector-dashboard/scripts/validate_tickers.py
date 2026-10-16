"""Verify every GS basket symbol still resolves on Yahoo. Run after editing gs_baskets.py."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import gs_baskets as g
import yahoo_http as y

syms = g.all_symbols()
res = y.fetch_many(syms)
bad = sorted(s for s, v in res.items() if v is None or v.get("day") is None)
retry = {s: y.fetch_returns(s) for s in bad}          # rule out transient throttling
bad = sorted(s for s, v in retry.items() if v is None or v.get("day") is None)

print(f"{len(syms)} symbols checked, {len(bad)} unresolved")
if bad:
    print("UNRESOLVED:", ", ".join(bad))
    sys.exit(1)
print("all symbols resolve")
