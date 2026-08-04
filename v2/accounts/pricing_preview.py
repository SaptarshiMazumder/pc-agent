"""Print what a sale is actually worth, at the dials currently configured.

WHY THIS EXISTS. The price of the product is spread across four environment variables and one
derived constant, and none of them mean anything on their own -- AGENTD_CREDITS_PER_USD only
makes sense next to AGENTD_CREDIT_MARKUP, and the creator's share applies to margin rather than
to the price on the tin. Reading the code tells you the formula; this tells you the answer.

It imports the real ledger module, so it cannot drift from what the service actually charges.
No database, no AWS, no service running.

    python accounts/pricing_preview.py                 # 5 / 20 / 100 at current settings
    python accounts/pricing_preview.py 20 50           # specific prices
    AGENTD_CREATOR_SHARE_PCT=0.70 python accounts/pricing_preview.py 20

ASCII only: this prints to Windows consoles that are frequently cp1252.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys


def _ledger():
    spec = importlib.util.spec_from_file_location(
        "agentd_ledger_preview", pathlib.Path(__file__).with_name("ledger.py")
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    led = _ledger()
    amounts = [float(a) for a in sys.argv[1:]] or [5.0, 20.0, 100.0]
    usd = led.micros_to_usd

    rate = led.credits_per_usd()
    markup = led.credit_markup()
    fee = led.processing_fee_pct()
    share = led.creator_share_pct()

    print("dials in use (all overridable by environment variable)")
    print(f"  AGENTD_CREDITS_PER_USD      {rate:>12,.0f}   credits per $1 of PROVIDER COST")
    print(f"  AGENTD_CREDIT_MARKUP        {markup:>12.4f}   price / cost  ->  cost ratio "
          f"{1 / markup:.1%}")
    print(f"  AGENTD_PROCESSING_FEE_PCT   {fee:>12.4f}   what the card rail takes")
    print(f"  AGENTD_CREATOR_SHARE_PCT    {share:>12.4f}   creator's cut OF MARGIN, not of gross")
    print()

    head = (f"{'sale':>8}  {'credits':>12}  {'inference':>10}  {'fee':>7}  {'margin':>9}  "
            f"{'creator':>9}  {'you':>8}  {'you, no creator':>16}")
    print(head)
    print("-" * len(head))
    for amount in amounts:
        credits = led.credits_for_usd(amount)
        s = led.split_purchase(led.usd_to_micros(amount), credits)
        print(f"{amount:>8,.2f}  {credits:>12,}  {usd(s['reserve_micros']):>10,.2f}  "
              f"{usd(s['fee_micros']):>7,.2f}  {usd(s['margin_micros']):>9,.2f}  "
              f"{usd(s['creator_micros']):>9,.2f}  {usd(s['platform_micros']):>8,.2f}  "
              f"{usd(s['margin_micros']):>16,.2f}")

    # BREAKAGE. The platform figure above is a FLOOR, not an expectation: the inference column is
    # money set aside, and whatever the user never spends is released back (see post_expiry --
    # currently all of it to the platform, which is open question O2). The creator's number does
    # not move, because they were paid in full at purchase regardless of usage.
    focus = amounts[-1]
    credits = led.credits_for_usd(focus)
    s = led.split_purchase(led.usd_to_micros(focus), credits)
    base = usd(s["platform_micros"])
    reserve = usd(s["reserve_micros"])
    print()
    print(f"what YOU keep on a ${focus:,.2f} sale, by how much of it the user actually spends")
    print(f"  (creator keeps ${usd(s['creator_micros']):,.2f} in every row -- paid at purchase, "
          f"carries no usage risk)")
    for spent in (1.0, 0.75, 0.5, 0.25, 0.0):
        print(f"  spends {spent:>4.0%} of credits  ->  inference ${reserve * spent:>7,.2f}  "
              f"breakage ${reserve * (1 - spent):>7,.2f}  you keep ${base + reserve * (1 - spent):>8,.2f}")

    if markup <= 1.0:
        print()
        print("WARNING: markup <= 1.0 sells credits at or below cost. Every sale loses the fee, "
              "and post_purchase will refuse unless AGENTD_ALLOW_NEGATIVE_MARGIN=1.")


if __name__ == "__main__":
    main()
