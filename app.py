import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np

# Set up page config
st.set_page_config(page_title="Dip-Buying SIP Dashboard", layout="wide")
st.title("Yearly-Low AVWAP Staircase Tracker")

# Define the watchlist mapped to their market tickers
WATCHLIST = {
    "NVIDIA": "NVDA", "Google": "GOOGL", "Apple": "AAPL", "Microsoft": "MSFT",
    "Amazon": "AMZN", "Broadcom": "AVGO", "Tesla": "TSLA", "Meta": "META",
    "Micron": "MU", "Eli Lilly": "LLY", "Berkshire Hathaway": "BRK-A",
    "JPMorgan Chase": "JPM", "AMD": "AMD", "Intel": "INTC", "Exxon Mobil": "XOM",
    "Visa": "V", "Walmart": "WMT", "SpaceX": "SPCX", "Mastercard": "MA", "Intel": "INTC", "Johnson & Johnson": "JNJ"
}

# ---- Configurable budget parameters ----
TOTAL_BUDGET = 50   # $ hard cap across ALL stocks combined, this run

# ---- Per-stock floor + cap (no segment tiers here, so every stock shares the same band) ----
# MIN_ALLOCATION is what a stock gets the INSTANT it has breached even ONE yearly-low AVWAP
# line (keeps orders from being fee-inefficient dust). MAX_ALLOCATION is the ceiling a single
# stock can reach - even if it's the ONLY stock dipping this month, it still can't swallow the
# whole budget.
MIN_ALLOCATION = 2.0    # $ floor per stock, the moment 1+ lines are breached
MAX_ALLOCATION = 0.25 * TOTAL_BUDGET  # $ cap per stock (25% of the $100 budget), reached once ALL lines are breached

# ---- Yearly-low AVWAP config (mirrors the "Yearly-Low AVWAPs" Pine Script indicator) ----
NUM_YEARS = 6              # current year + up to 5 prior years, same max as the indicator
LOOKBACK_PERIOD = "7y"     # fetch a little extra so the oldest year's anchor has full context


def get_yearly_low_anchors(df: pd.DataFrame, num_years: int = NUM_YEARS) -> list:
    """
    For each of the last `num_years` calendar years (current year back to current_year -
    num_years + 1), find that year's lowest LOW price and the first bar that touched it -
    exactly the anchor rule used by the "Yearly-Low AVWAPs" indicator. Returns oldest-first.
    """
    current_year = df.index[-1].year
    target_years = [current_year - i for i in range(num_years)]
    anchors = []
    for yr in target_years:
        year_mask = df.index.year == yr
        if not year_mask.any():
            continue
        year_data = df[year_mask]
        low_val = year_data['Low'].min()
        anchor_date = year_data[year_data['Low'] == low_val].index[0]  # first occurrence
        anchor_idx = df.index.get_loc(anchor_date)
        anchors.append({'year': yr, 'date': anchor_date, 'idx': anchor_idx, 'low_price': low_val})
    return sorted(anchors, key=lambda a: a['date'])


def compute_avwap_series(df: pd.DataFrame, anchor_idx: int) -> pd.Series:
    """
    Volume-weighted average price from the anchor bar to today. Falls back to an equal-weight
    (TWAP) average if volume is missing/zero for the window.
    """
    sub = df.iloc[anchor_idx:]
    typical_price = (sub['High'] + sub['Low'] + sub['Close']) / 3.0
    vol = sub['Volume']
    weight = vol.fillna(0) if vol.sum() > 0 else pd.Series(1.0, index=sub.index)
    cum_weight = weight.cumsum().replace(0, np.nan)
    return ((typical_price * weight).cumsum() / cum_weight).ffill()


@st.cache_data(ttl=300)
def fetch_and_score(watchlist: dict):
    rows = []
    anchor_tables = {}

    for company, ticker in watchlist.items():
        try:
            stock = yf.Ticker(ticker)
            hist = stock.history(period=LOOKBACK_PERIOD)

            if hist.empty or len(hist) < 200:
                continue

            close = hist['Close']
            current_price = close.iloc[-1]
            price_change = current_price - close.iloc[-2]
            pct_change = (price_change / close.iloc[-2]) * 100

            anchors = get_yearly_low_anchors(hist, NUM_YEARS)
            total_lines = len(anchors)

            breached_count = 0
            anchor_details = []
            for a in anchors:
                avwap_series = compute_avwap_series(hist, a['idx'])
                avwap_value = avwap_series.iloc[-1]
                is_below = current_price < avwap_value
                discount_pct = ((avwap_value - current_price) / avwap_value * 100.0) if is_below else 0.0
                if is_below:
                    breached_count += 1
                anchor_details.append({
                    "Year": a['year'],
                    "Anchor Date": a['date'].date().isoformat(),
                    "Year Low": round(a['low_price'], 2),
                    "AVWAP Today": round(avwap_value, 2),
                    "Status": "🔴 Price Below (line acts as resistance)" if is_below else "🟢 Price Above (reclaimed)",
                    "Discount %": round(discount_pct, 2),
                })

            breached_fraction = (breached_count / total_lines) if total_lines > 0 else 0.0
            avg_discount = (
                sum(a["Discount %"] for a in anchor_details if a["Status"].startswith("🔴")) / breached_count
                if breached_count > 0 else 0.0
            )

            rows.append({
                "Company": company,
                "Ticker": ticker,
                "Live Price": round(current_price, 2),
                "Daily Change": f"{price_change:+.2f} ({pct_change:+.2f}%)",
                "Lines Breached": breached_count,
                "Total Lines": total_lines,
                "Breached Fraction": round(breached_fraction, 3),
                "Avg Discount Below Breached Lines %": round(avg_discount, 2),
            })
            anchor_tables[company] = anchor_details

        except Exception:
            continue

    return pd.DataFrame(rows), anchor_tables


def allocate_budget(df: pd.DataFrame, total_budget: float, min_allocation: float,
                     max_allocation: float) -> pd.DataFrame:
    """
    Per-stock floor-to-cap STAIRCASE allocation driven by how many yearly-low AVWAP lines a
    stock's price is currently below:

      target_i = min_allocation + (Lines Breached_i / Total Lines_i) * (max_allocation - min_allocation)

    1. Every stock with 1+ breached lines is "active" this run.
    2. Each active stock's target scales in EVEN STEPS between min_allocation (1 line breached)
       and max_allocation (every line breached) - independent of how many lines any OTHER
       stock has breached. A stock below only 1 of 6 lines lands near its floor; one below all
       6 lands at its cap.
    3. Budget fit:
       - If every active stock's target fits within total_budget, each simply gets its own
         target; leftover budget is reported as unspent.
       - If targets collectively exceed total_budget (very possible with ~20 stocks watched),
         every active stock is first guaranteed its own min_allocation floor, then the
         remaining money is split across stocks in proportion to their discretionary need
         (target_i - min_allocation) - so stocks with more lines breached still get priority.
       - If even the floors alone exceed total_budget, floors themselves are scaled down
         proportionally, favoring more-breached stocks slightly.

    Returns only the ACTIVE (Lines Breached > 0) stocks, each with "Target ($)" and
    "Invest ($)" columns, sorted by Invest ($) descending.
    """
    full = df.copy()
    active = full[full["Lines Breached"] > 0].copy().reset_index(drop=True)
    if active.empty:
        active["Target ($)"] = []
        active["Invest ($)"] = []
        return active

    active["Target ($)"] = (
        min_allocation + active["Breached Fraction"] * (max_allocation - min_allocation)
    ).round(2)

    total_target = active["Target ($)"].sum()

    if total_target <= total_budget + 1e-6:
        final_invest = active["Target ($)"]
    else:
        sum_min = min_allocation * len(active)
        if sum_min >= total_budget:
            weight = active["Breached Fraction"].clip(lower=0.01)
            final_invest = (min_allocation * weight) * (total_budget / (min_allocation * weight).sum())
        else:
            discretionary_pool = total_budget - sum_min
            discretionary_need = active["Target ($)"] - min_allocation
            total_need = discretionary_need.sum()
            disc_scale = (discretionary_pool / total_need) if total_need > 1e-9 else 0.0
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

with st.spinner("Fetching data, anchoring VWAPs at each year's low..."):
    raw_df, anchor_tables = fetch_and_score(WATCHLIST)

if not raw_df.empty:
    df = allocate_budget(raw_df, TOTAL_BUDGET, MIN_ALLOCATION, MAX_ALLOCATION)
    skipped = len(raw_df) - len(df)

    if df.empty:
        st.warning("No stocks are currently below ANY of their yearly-low AVWAP lines. No buys this month.")
    else:
        total_deploy = df["Invest ($)"].sum()
        strong_confluence = (df["Lines Breached"] >= df["Total Lines"] * 0.67).sum()

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total to deploy this run", f"${total_deploy:.2f}", delta=f"of ${TOTAL_BUDGET:.0f} budget")
        m2.metric("Budget remaining (uninvested)", f"${max(TOTAL_BUDGET - total_deploy, 0):.2f}")
        m3.metric("Stocks in this month's buy", f"{len(df)}/{len(raw_df)}")
        m4.metric("Strong confluence (≥2/3 lines breached)", int(strong_confluence))

        if skipped:
            st.caption(f"ℹ️ {skipped} stock(s) excluded — trading above EVERY one of their yearly-low AVWAP "
                       f"lines, so no rungs breached and no buy signal this run.")

    st.divider()

    # ---------------- Mobile-friendly card layout (replaces the wide table) ----------------
    st.subheader("This month's buy plan")

    max_invest = df["Invest ($)"].max() if not df.empty else 0

    for _, row in df.iterrows():
        invest_amt = row["Invest ($)"]
        frac = invest_amt / max_invest if max_invest > 0 else 0
        border_color = "#1b5e20" if frac >= 0.67 else ("#2e7d32" if frac >= 0.34 else "#455a64")

        with st.container(border=True):
            top_left, top_right = st.columns([3, 2])
            with top_left:
                st.markdown(f"**{row['Company']}**  \n`{row['Ticker']}`")
                st.caption(f"🟢 {row['Lines Breached']}/{row['Total Lines']} yearly-low AVWAP lines breached")
            with top_right:
                st.markdown(
                    f"<div style='text-align:right; font-size:1.4rem; font-weight:700; color:{border_color};'>"
                    f"${invest_amt:,.2f}</div>",
                    unsafe_allow_html=True,
                )
                st.caption(f"Range: ${MIN_ALLOCATION:.0f} → ${MAX_ALLOCATION:.0f}")

            d1, d2, d3 = st.columns(3)
            d1.metric("Price", f"${row['Live Price']:,.2f}", row["Daily Change"])
            d2.metric("Avg Discount", f"{row['Avg Discount Below Breached Lines %']:.1f}%")
            d3.metric("Floor→Cap", f"{row['Breached Fraction']*100:.0f}%")

            st.progress(row["Breached Fraction"],
                        text=f"Staircase progress: {row['Lines Breached']}/{row['Total Lines']} rungs "
                             f"→ ${invest_amt:,.2f}")

            anchors = anchor_tables.get(row["Company"], [])
            if anchors:
                with st.expander(f"AVWAP anchor breakdown ({len(anchors)} lines)"):
                    st.dataframe(pd.DataFrame(anchors), use_container_width=True, hide_index=True)

                    st.markdown("**Staircase preview** — what this stock would receive at each rung:")
                    total_lines = row["Total Lines"]
                    preview_rows = []
                    for rung in range(0, total_lines + 1):
                        amt = 0.0 if rung == 0 else MIN_ALLOCATION + (rung / total_lines) * (MAX_ALLOCATION - MIN_ALLOCATION)
                        preview_rows.append({
                            "Rungs Breached": f"{rung} / {total_lines}",
                            "Investment ($)": round(amt, 2),
                            "This is current level": "👉" if rung == row["Lines Breached"] else "",
                        })
                    st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True)
                    st.caption("This preview shows this stock's standalone target at each rung. The actual "
                               "amount above may be lower if the budget is being squeezed across several "
                               "deeply-discounted stocks at once (common with a ~20-stock watchlist).")
else:
    st.error("Unable to load data. Please check your internet connection or try again.")