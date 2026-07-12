import streamlit as st
import yfinance as yf
import pandas as pd

# Set up page config
st.set_page_config(page_title="Dip-Buying SIP Dashboard", layout="wide")
st.title("📉 52-Week Range Pullback Tracker — Floor-to-Cap Discount²-Weighted Buy Logic")
st.caption("Checks where each stock's current price sits within its 52-week high/low range and scales its "
           "own investment smoothly from a floor up to a cap as that stock's OWN dip deepens — a 2% dip and "
           "a 15% dip in the same stock will NOT get the same dollar amount.")

# Define the watchlist mapped to their market tickers
WATCHLIST = {
    "NVIDIA": "NVDA", "Google": "GOOGL", "Apple": "AAPL", "Microsoft": "MSFT",
    "Amazon": "AMZN", "Broadcom": "AVGO", "Tesla": "TSLA", "Meta": "META",
    "Micron": "MU", "Eli Lilly": "LLY", "Berkshire Hathaway": "BRK-A",
    "JPMorgan Chase": "JPM", "AMD": "AMD", "Intel": "INTC", "Exxon Mobil": "XOM",
    "Visa": "V", "Walmart": "WMT", "SpaceX": "SPCX", "Mastercard": "MA", "Intel": "INTC", "Johnson & Johnson": "JNJ"
}

# ---- Configurable budget parameters ----
TOTAL_BUDGET = 100.0   # $ hard cap across ALL stocks combined, this run

# ---- Per-stock floor + cap (no segment tiers here, so every stock shares the same band) ----
# MIN_ALLOCATION is what a stock gets the INSTANT it shows any discount at all (keeps orders
# from being fee-inefficient dust). MAX_ALLOCATION is the ceiling a single stock can reach -
# even if it's the ONLY stock dipping this month, it still can't swallow the whole budget.
MIN_ALLOCATION = 2.0    # $ floor per stock, the moment discount > 0
MAX_ALLOCATION = 25.0   # $ cap per stock (25% of the $100 budget)

# The Range Discount % (off the 52-week high) at which a stock's investment saturates to
# MAX_ALLOCATION. Discounts deeper than this are simply clipped at the cap; shallower ones
# scale down smoothly toward MIN_ALLOCATION. This is what makes a 5% dip and a 35% dip in the
# SAME stock land at very different dollar amounts instead of both jumping straight to (or
# near) the cap. Individual stocks are far more volatile than broad indices, so this is set
# higher than a typical index threshold - a 35% pullback off the 52-week high is a real,
# significant correction for a single name.
REFERENCE_DISCOUNT_PCT = 35.0


@st.cache_data(ttl=60)  # Caches data for 60 seconds to optimize performance
def fetch_stock_data(watchlist):
    data_list = []
    for company, ticker in watchlist.items():
        try:
            stock = yf.Ticker(ticker)
            # Last 1 year of daily prices
            hist = stock.history(period="1y")

            if hist.empty:
                continue

            close = hist['Close']
            current_price = close.iloc[-1]
            price_change = current_price - close.iloc[-2]
            pct_change = (price_change / close.iloc[-2]) * 100

            high_52w = close.max()
            low_52w = close.min()

            # Range Position: 100% = at the yearly high, 0% = at the yearly low
            span = high_52w - low_52w
            if span > 0:
                range_position = (current_price - low_52w) / span
            else:
                range_position = 1.0  # no range (flat price) -> treat as "at the high", no discount
            range_position = min(max(range_position, 0.0), 1.0)  # clip to [0, 1]

            # Range Discount = 1 - Range Position (closer to the 52W low -> bigger discount)
            range_discount_pct = (1.0 - range_position) * 100

            data_list.append({
                "Company": company,
                "Ticker": ticker,
                "Live Price": round(current_price, 2),
                "Daily Change": f"{price_change:+.2f} ({pct_change:+.2f}%)",
                "52W High": round(high_52w, 2),
                "52W Low": round(low_52w, 2),
                "Range Discount %": round(range_discount_pct, 2),
            })
        except Exception:
            # Catch errors for missing listings or down network tickers
            continue

    return pd.DataFrame(data_list)


def allocate_budget(df: pd.DataFrame, total_budget: float, min_allocation: float,
                     max_allocation: float, reference_discount_pct: float) -> pd.DataFrame:
    """
    Per-stock floor-to-cap allocation driven by each stock's OWN 52-week range discount:

      severity_fraction_i = min( (Range Discount %_i / reference_discount_pct)^2 , 1.0 )
      target_i             = min_allocation + severity_fraction_i * (max_allocation - min_allocation)

    1. Every stock has a discount score (0% at its 52-week high, up to 100% at its 52-week low).
       A stock is "active" as soon as its discount is > 0.
    2. Each active stock's target scales SMOOTHLY and MONOTONICALLY between min_allocation
       (what it gets the instant it shows any discount) and max_allocation (reached once its
       OWN discount hits reference_discount_pct). Squaring means the ramp is gentle at first
       and steep near the cap. Crucially, the curve depends ONLY on that stock's own discount -
       not on how many other stocks are dipping - so a lone discounted stock no longer grabs an
       outsized share of the budget just because nothing else qualified.
    3. Budget fit:
       - If every active stock's target fits within total_budget, each simply gets its own
         target; leftover budget is reported as unspent (nothing forces the full $100 out the
         door if the dips aren't deep enough to warrant it).
       - If targets collectively exceed total_budget (very possible with ~20 stocks watched),
         every active stock is first guaranteed its own min_allocation floor, then the
         remaining money is split across stocks in proportion to their discretionary need
         (target_i - min_allocation) - so the deeper dips still get priority on the scarce
         dollars.
       - If even the floors alone exceed total_budget (e.g. many stocks dip in the same week),
         floors themselves are scaled down proportionally, favoring deeper dips slightly.

    Returns only the ACTIVE (discount > 0) stocks, each with "SeverityFraction", "Target ($)"
    and "Invest ($)" columns, sorted by Invest ($) descending.
    """
    full = df.copy()
    full["SeverityFraction"] = ((full["Range Discount %"] / reference_discount_pct) ** 2).clip(upper=1.0)

    active = full[full["Range Discount %"] > 0].copy().reset_index(drop=True)
    if active.empty:
        active["Target ($)"] = []
        active["Invest ($)"] = []
        return active

    active["Target ($)"] = (
        min_allocation + active["SeverityFraction"] * (max_allocation - min_allocation)
    ).round(2)

    total_target = active["Target ($)"].sum()

    if total_target <= total_budget + 1e-6:
        # Budget comfortably covers every stock's own floor-to-cap target.
        final_invest = active["Target ($)"]
    else:
        sum_min = min_allocation * len(active)
        if sum_min >= total_budget:
            # Even guaranteeing every floor doesn't fit - scale floors down proportionally,
            # favoring the more severe dips slightly via severity-weighted scaling.
            weight = active["SeverityFraction"].clip(lower=0.01)
            final_invest = (min_allocation * weight) * (total_budget / (min_allocation * weight).sum())
        else:
            discretionary_pool = total_budget - sum_min
            discretionary_need = active["Target ($)"] - min_allocation
            total_need = discretionary_need.sum()
            if total_need > 1e-9:
                disc_scale = discretionary_pool / total_need
            else:
                disc_scale = 0.0
            final_invest = min_allocation + discretionary_need * disc_scale

    active["Invest ($)"] = final_invest.round(2)
    return active.sort_values("Invest ($)", ascending=False).reset_index(drop=True)


# Dashboard controls
col1, col2, col3 = st.columns([1, 1, 4])
with col1:
    if st.button("🔄 Force Refresh"):
        st.cache_data.clear()
with col2:
    st.metric("Total budget", f"${TOTAL_BUDGET:.0f}")

# Fetch and build data layout
with st.spinner("Fetching live market data..."):
    raw_df = fetch_stock_data(WATCHLIST)

if not raw_df.empty:
    df = allocate_budget(raw_df, TOTAL_BUDGET, MIN_ALLOCATION, MAX_ALLOCATION, REFERENCE_DISCOUNT_PCT)
    skipped = len(raw_df) - len(df)

    if df.empty:
        st.warning("No stocks are currently below their 52-week high. No buys this month.")
    else:
        total_deploy = df["Invest ($)"].sum()
        deep_dips = (df["Range Discount %"] >= 20).sum()

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total to deploy this run", f"${total_deploy:.2f}", delta=f"of ${TOTAL_BUDGET:.0f} budget")
        m2.metric("Budget remaining (uninvested)", f"${max(TOTAL_BUDGET - total_deploy, 0):.2f}")
        m3.metric("Stocks in this month's buy", f"{len(df)}/{len(raw_df)}")
        m4.metric("Deep dips (≥20% off 52W high)", int(deep_dips))

        if skipped:
            st.caption(f"ℹ️ {skipped} stock(s) excluded — trading right at their 52-week high, "
                       f"so no discount signal and no buy this run.")

        st.divider()

        # Highlight rows by allocation size (relative to this run's max)
        def highlight_actions(row):
            frac = row["Invest ($)"] / df["Invest ($)"].max()
            if frac < 0.34:
                color = 'background-color: #444444; color: #dddddd'
            elif frac < 0.67:
                color = 'background-color: #2e7d32; color: white'
            else:
                color = 'background-color: #1b5e20; color: white'
            return [color for _ in row]

        display_df = df[["Company", "Ticker", "Invest ($)", "Live Price", "Daily Change",
                          "52W High", "52W Low", "Range Discount %", "SeverityFraction"]].rename(
            columns={"SeverityFraction": "Floor→Cap %"}
        )
        display_df["Floor→Cap %"] = (display_df["Floor→Cap %"] * 100).round(0)
        styled_df = display_df.style.apply(highlight_actions, axis=1)

        st.dataframe(
            styled_df,
            use_container_width=True,
            height=750,
            column_config={
                "Company": st.column_config.TextColumn(width="medium", pinned=False),
                "Ticker": st.column_config.TextColumn(width="small", pinned=False),
                "Live Price": st.column_config.NumberColumn(format="$%.2f", width="small"),
                "Daily Change": st.column_config.TextColumn(width="medium"),
                "52W High": st.column_config.NumberColumn(format="$%.2f", width="small"),
                "52W Low": st.column_config.NumberColumn(format="$%.2f", width="small"),
                "Range Discount %": st.column_config.NumberColumn(format="%.2f%%", width="medium"),
                "Invest ($)": st.column_config.NumberColumn(format="$%.2f", width="small"),
                "Floor→Cap %": st.column_config.ProgressColumn(
                    format="%.0f%%", min_value=0, max_value=100, width="medium"
                ),
            }
        )

        st.divider()
        st.subheader("📌 How the floor-to-cap, 52-week-range allocation works")
        st.markdown(
            f"""
            - **Range Discount = 1 − (Current Price − 52W Low) / (52W High − 52W Low)** — 0% means
              trading at the 52-week high, 100% means trading at the 52-week low.
            - **Every stock has its own floor (${MIN_ALLOCATION:.0f}) and cap (${MAX_ALLOCATION:.0f})**
              — the floor is what it gets the instant it shows any discount at all; the cap is the
              ceiling it only reaches once its OWN discount hits **{REFERENCE_DISCOUNT_PCT:.0f}%** off
              the 52-week high.
            - **severity fraction = min((Range Discount% / {REFERENCE_DISCOUNT_PCT:.0f}%)², 1.0)** —
              computed purely from that stock's own dip, so it doesn't matter whether it's the only
              stock discounted this month or one of ten. A 5% dip and a 30% dip in the *same* stock
              will land at very different dollar amounts instead of both jumping to (or near) the cap.
            - **Target ($) = floor + severity fraction × (cap − floor)**, per stock, independently.
              Squaring means the ramp is gentle for shallow dips and steep as the discount deepens.
            - **Budget fit:** if every active stock's target fits within ${TOTAL_BUDGET:.0f}, each just
              gets its own target and any leftover stays unspent this run. If targets add up to more
              (common when several of the ~20 watchlist stocks dip at once), every active stock is
              first guaranteed its ${MIN_ALLOCATION:.0f} floor, then the remaining money is split in
              proportion to how much *more* than the floor each was asking for — so deeper dips still
              get priority on the scarce dollars.
            - Stocks trading right at their 52-week high (0% discount) are excluded entirely from
              this month's buy.
            """
        )
else:
    st.error("Unable to load data. Please check your internet connection or try again.")