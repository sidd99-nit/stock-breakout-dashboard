import streamlit as st
import yfinance as yf
import pandas as pd

# Set up page config
st.set_page_config(page_title="Dip-Buying SIP Dashboard", layout="wide")
st.title("📉 20-Day High Pullback Tracker — Tiered SIP Buy Logic")
st.caption("Checks each stock's drop from its 20-trading-day high and tells you what to buy this month.")

# Define the watchlist mapped to their market tickers
WATCHLIST = {
    "NVIDIA": "NVDA", "Google": "GOOGL", "Apple": "AAPL", "Microsoft": "MSFT",
    "Amazon": "AMZN", "Broadcom": "AVGO", "Tesla": "TSLA", "Meta": "META",
    "Micron": "MU", "Eli Lilly": "LLY", "Berkshire Hathaway": "BRK-A",
    "JPMorgan Chase": "JPM", "AMD": "AMD", "Intel": "INTC", "Netflix": "NFLX",
    "Visa": "V", "Accenture": "ACN", "SpaceX": "SPCX"
}

# ---- Configurable SIP parameters ----
MIN_INVEST = 1.0     # $ floor - invested even on a very shallow/no dip (keeps it a true SIP)
MAX_INVEST = 10.0    # $ ceiling - your hard cap per stock per month
MID_INVEST = 5.0     # $ midpoint - crossing above this is reserved for a "very good" dip
TOTAL_BUDGET = 100.0 # $ hard cap across ALL stocks combined, this run

# Drop-% bands that map to the $ amount. Tweak these to taste.
# Below LOW_BAND    -> near MIN_INVEST (barely off the peak)
# LOW_BAND..HI_BAND -> scales from MIN_INVEST up to MID_INVEST
# Above HI_BAND      -> scales from MID_INVEST up to MAX_INVEST (only "very good" dips reach here)
LOW_BAND = 3.0    # % drop
HI_BAND = 8.0     # % drop
DEEP_BAND = 15.0  # % drop at which you hit the full $10 cap


def classify_action(pct_drop: float):
    """
    pct_drop is a POSITIVE number representing % below the 20-day high.
    Returns (action_label, amount_to_invest), amount always between MIN_INVEST and MAX_INVEST.
    """
    if pct_drop <= LOW_BAND:
        amount = MIN_INVEST
        label = f"🔹 BUY ${amount:.0f} (shallow dip)"
    elif pct_drop <= HI_BAND:
        # scale linearly from MIN_INVEST -> MID_INVEST across LOW_BAND -> HI_BAND
        frac = (pct_drop - LOW_BAND) / (HI_BAND - LOW_BAND)
        amount = MIN_INVEST + frac * (MID_INVEST - MIN_INVEST)
        label = f"✅ BUY ${amount:.0f}"
    elif pct_drop <= DEEP_BAND:
        # scale linearly from MID_INVEST -> MAX_INVEST across HI_BAND -> DEEP_BAND
        frac = (pct_drop - HI_BAND) / (DEEP_BAND - HI_BAND)
        amount = MID_INVEST + frac * (MAX_INVEST - MID_INVEST)
        label = f"💰 BUY ${amount:.0f} (very good dip)"
    else:
        amount = MAX_INVEST
        label = f"🚀 BUY ${amount:.0f} (deep dip, capped)"

    amount = round(min(max(amount, MIN_INVEST), MAX_INVEST), 2)
    return label, amount


@st.cache_data(ttl=60)  # Caches data for 60 seconds to optimize performance
def fetch_stock_data(watchlist):
    data_list = []
    for company, ticker in watchlist.items():
        try:
            stock = yf.Ticker(ticker)
            # Fetch 30 days to safely ensure 20 distinct trading days are captured
            hist = stock.history(period="30d")

            if len(hist) < 20:
                continue

            # Trailing 20 trading days, EXCLUDING today's live candle
            trailing_20_days = hist.iloc[-21:-1]

            # FIX: this must be the 20-day HIGH (max), not min.
            # The original code used .min() on the 'High' column, which is a bug -
            # it found the lowest of the daily highs, not the actual 20-day high.
            high_20d = trailing_20_days['High'].max()

            # Get current market price
            current_price = hist['Close'].iloc[-1]
            price_change = current_price - hist['Close'].iloc[-2]
            pct_change = (price_change / hist['Close'].iloc[-2]) * 100

            # % drop from the 20-day high (positive number = how far below the high)
            pct_drop_from_high = ((high_20d - current_price) / high_20d) * 100
            pct_drop_from_high = max(pct_drop_from_high, 0.0)  # clip negative (new high) to 0

            action_label, invest_amount = classify_action(pct_drop_from_high)

            data_list.append({
                "Company": company,
                "Ticker": ticker,
                "Live Price": round(current_price, 2),
                "Daily Change": f"{price_change:+.2f} ({pct_change:+.2f}%)",
                "20-Day High": round(high_20d, 2),
                "Drop From High %": round(pct_drop_from_high, 2),
                "Action": action_label,
                "Invest ($)": invest_amount
            })
        except Exception:
            # Catch errors for missing listings or down network tickers
            continue

    return pd.DataFrame(data_list)


# Dashboard controls
col1, col2, col3 = st.columns([1, 1, 4])
with col1:
    if st.button("🔄 Force Refresh"):
        st.cache_data.clear()
with col2:
    st.metric("Max $/stock", f"${MAX_INVEST:.0f}")

# Fetch and build data layout
with st.spinner("Fetching live market data..."):
    df = fetch_stock_data(WATCHLIST)

if not df.empty:
    raw_total = df["Invest ($)"].sum()

    # ---- Enforce the hard $100 total budget cap ----
    # If raw amounts exceed the budget, scale every stock's amount down proportionally
    # so the total lands exactly at TOTAL_BUDGET. Deeper dips still get more $ than
    # shallow ones - the ratio between stocks is preserved, only the overall size shrinks.
    was_scaled = raw_total > TOTAL_BUDGET
    if was_scaled:
        scale_factor = TOTAL_BUDGET / raw_total
        df["Invest ($)"] = (df["Invest ($)"] * scale_factor).round(2)

    total_deploy = df["Invest ($)"].sum()
    deep_dips = df["Action"].str.contains("very good dip|deep dip, capped").sum()
    shallow = df["Action"].str.contains("shallow dip").sum()

    if was_scaled:
        st.warning(
            f"⚠️ Raw allocation was ${raw_total:.2f}, over your ${TOTAL_BUDGET:.0f} budget. "
            f"All amounts scaled down proportionally (×{TOTAL_BUDGET/raw_total:.2f}) to fit ${TOTAL_BUDGET:.0f} total."
        )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total to deploy this run", f"${total_deploy:.2f}", delta=f"of ${TOTAL_BUDGET:.0f} budget")
    m2.metric("Budget remaining", f"${max(TOTAL_BUDGET - total_deploy, 0):.2f}")
    m3.metric("Very good dips (>$5 raw)", f"{deep_dips}/{len(df)}")
    m4.metric("Shallow dips ($1 raw)", shallow)

    st.divider()

    # Highlight rows by amount tier
    def highlight_actions(row):
        amt = row["Invest ($)"]
        if amt <= MIN_INVEST + 0.01:
            color = 'background-color: #444444; color: #dddddd'
        elif amt <= MID_INVEST:
            color = 'background-color: #2e7d32; color: white'
        else:
            color = 'background-color: #1b5e20; color: white'
        return [color for _ in row]

    styled_df = df.style.apply(highlight_actions, axis=1)

    st.dataframe(
        styled_df,
        use_container_width=True,
        height=750,
        column_order=[
            "Company", "Ticker", "Invest ($)", "Live Price", "Daily Change",
            "20-Day High", "Drop From High %", "Action"
        ],
        column_config={
            "Company": st.column_config.TextColumn(width="medium", pinned=False),
            "Ticker": st.column_config.TextColumn(width="small", pinned=False),
            "Live Price": st.column_config.NumberColumn(format="$%.2f", width="small"),
            "Daily Change": st.column_config.TextColumn(width="medium"),
            "20-Day High": st.column_config.NumberColumn(format="$%.2f", width="medium"),
            "Drop From High %": st.column_config.NumberColumn(format="%.2f%%", width="medium"),
            "Action": st.column_config.TextColumn(width="medium"),
            "Invest ($)": st.column_config.NumberColumn(format="$%.2f", width="small"),
        }
    )

    st.divider()
    st.subheader("📌 How the $1–$10 scale works")
    st.markdown(
        f"""
        - **0–{LOW_BAND:.0f}% drop** → ${MIN_INVEST:.0f} (barely off the peak, still buy something — keeps this a true SIP)
        - **{LOW_BAND:.0f}–{HI_BAND:.0f}% drop** → scales ${MIN_INVEST:.0f} → ${MID_INVEST:.0f}
        - **{HI_BAND:.0f}–{DEEP_BAND:.0f}% drop** → scales ${MID_INVEST:.0f} → ${MAX_INVEST:.0f} ("very good" dip territory)
        - **{DEEP_BAND:.0f}%+ drop** → capped at ${MAX_INVEST:.0f} per stock (before budget scaling)

        Every stock gets *something* every month — nothing is ever skipped — but the size scales up
        only when the dip is genuinely deep, so you're not spending $10 at the top.

        **Total budget cap:** all amounts above are calculated per-stock first, then if the sum across
        every stock exceeds **${TOTAL_BUDGET:.0f}**, every amount is scaled down proportionally so the
        total never crosses ${TOTAL_BUDGET:.0f}. Stocks with deeper dips still get more than shallow
        ones — the cap just shrinks everyone's slice equally in ratio terms.
        """
    )
else:
    st.error("Unable to load data. Please check your internet connection or try again.")