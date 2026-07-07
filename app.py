import streamlit as st
import yfinance as yf
import pandas as pd

# Set up page config
st.set_page_config(page_title="Dip-Buying SIP Dashboard", layout="wide")
st.title("📉 50-Day MA Pullback Tracker — Discount²-Weighted Buy Logic")
st.caption("Checks each stock's discount from its 50-day moving average and allocates your monthly budget "
           "so deeper dips get disproportionately more money.")

# Define the watchlist mapped to their market tickers
WATCHLIST = {
    "NVIDIA": "NVDA", "Google": "GOOGL", "Apple": "AAPL", "Microsoft": "MSFT",
    "Amazon": "AMZN", "Broadcom": "AVGO", "Tesla": "TSLA", "Meta": "META",
    "Micron": "MU", "Eli Lilly": "LLY", "Berkshire Hathaway": "BRK-A",
    "JPMorgan Chase": "JPM", "AMD": "AMD", "Intel": "INTC", "Netflix": "NFLX",
    "Visa": "V", "Accenture": "ACN", "SpaceX": "SPCX"
}

# ---- Configurable budget parameters ----
TOTAL_BUDGET = 100.0   # $ hard cap across ALL stocks combined, this run
MIN_ALLOCATION = 2.0   # $ floor - if a stock's share falls below this, drop it and
                       # redistribute its money among the remaining discounted stocks
MA_WINDOW = 50          # moving average window (days) used as the "fair value" reference


@st.cache_data(ttl=60)  # Caches data for 60 seconds to optimize performance
def fetch_stock_data(watchlist, ma_window):
    data_list = []
    for company, ticker in watchlist.items():
        try:
            stock = yf.Ticker(ticker)
            # Fetch extra history so the rolling MA_WINDOW average is fully populated
            hist = stock.history(period=f"{ma_window + 20}d")

            if len(hist) < ma_window:
                continue

            close = hist['Close']

            # N-day simple moving average (using all closes up to and including today)
            ma_value = close.rolling(window=ma_window).mean().iloc[-1]

            # Get current market price
            current_price = close.iloc[-1]
            price_change = current_price - close.iloc[-2]
            pct_change = (price_change / close.iloc[-2]) * 100

            # % discount from the moving average (positive number = how far below the MA)
            pct_discount = ((ma_value - current_price) / ma_value) * 100
            pct_discount = max(pct_discount, 0.0)  # clip negative (price above MA) to 0

            data_list.append({
                "Company": company,
                "Ticker": ticker,
                "Live Price": round(current_price, 2),
                "Daily Change": f"{price_change:+.2f} ({pct_change:+.2f}%)",
                f"{ma_window}-Day MA": round(ma_value, 2),
                "Discount From MA %": round(pct_discount, 2),
            })
        except Exception:
            # Catch errors for missing listings or down network tickers
            continue

    return pd.DataFrame(data_list)


def allocate_budget(df: pd.DataFrame, total_budget: float, min_allocation: float) -> pd.DataFrame:
    """
    Squared-discount weighting:
      weight_i = (discount%_i)^2
      allocation_i = (weight_i / sum(weights)) * total_budget
    Stocks with 0% discount (at/above their moving average) get weight 0 -> no buy this month.
    Stocks whose share falls below min_allocation are dropped and their money is
    redistributed proportionally among the remaining stocks.
    """
    df = df.copy()
    df["Weight"] = df["Discount From MA %"] ** 2

    # Drop stocks with zero weight (no dip at all -> not part of this month's buy)
    df = df[df["Weight"] > 0].reset_index(drop=True)

    if df.empty:
        df["Invest ($)"] = []
        return df

    while True:
        total_weight = df["Weight"].sum()
        df["Invest ($)"] = (df["Weight"] / total_weight) * total_budget

        below_min = df[df["Invest ($)"] < min_allocation]
        if below_min.empty or len(df) == 1:
            break
        df = df[df["Invest ($)"] >= min_allocation].reset_index(drop=True)

    df["Invest ($)"] = df["Invest ($)"].round(2)
    return df.sort_values("Invest ($)", ascending=False).reset_index(drop=True)


# Dashboard controls
col1, col2, col3 = st.columns([1, 1, 4])
with col1:
    if st.button("🔄 Force Refresh"):
        st.cache_data.clear()
with col2:
    st.metric("Total budget", f"${TOTAL_BUDGET:.0f}")

# Fetch and build data layout
with st.spinner("Fetching live market data..."):
    raw_df = fetch_stock_data(WATCHLIST, MA_WINDOW)

if not raw_df.empty:
    df = allocate_budget(raw_df, TOTAL_BUDGET, MIN_ALLOCATION)
    skipped = len(raw_df) - len(df)

    if df.empty:
        st.warning(f"No stocks are currently trading below their {MA_WINDOW}-day moving average. No buys this month.")
    else:
        total_deploy = df["Invest ($)"].sum()
        deep_dips = (df["Discount From MA %"] >= 8).sum()

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total to deploy this run", f"${total_deploy:.2f}", delta=f"of ${TOTAL_BUDGET:.0f} budget")
        m2.metric("Budget remaining", f"${max(TOTAL_BUDGET - total_deploy, 0):.2f}")
        m3.metric("Stocks in this month's buy", f"{len(df)}/{len(raw_df)}")
        m4.metric("Deep dips (≥8% drop)", int(deep_dips))

        if skipped:
            st.caption(f"ℹ️ {skipped} stock(s) excluded — either at/above their {MA_WINDOW}-day MA, "
                       f"or their share fell below the ${MIN_ALLOCATION:.0f} minimum allocation.")

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

        ma_col = f"{MA_WINDOW}-Day MA"
        display_df = df[["Company", "Ticker", "Invest ($)", "Live Price", "Daily Change",
                          ma_col, "Discount From MA %"]]
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
                ma_col: st.column_config.NumberColumn(format="$%.2f", width="medium"),
                "Discount From MA %": st.column_config.NumberColumn(format="%.2f%%", width="medium"),
                "Invest ($)": st.column_config.NumberColumn(format="$%.2f", width="small"),
            }
        )

        st.divider()
        st.subheader("📌 How the discount²-weighted allocation works")
        st.markdown(
            f"""
            - **weight = (% discount from {MA_WINDOW}-day MA)²** — squaring means a 20% discount gets ~4x
              the allocation of a 10% discount, not just 2x. Deep dips are rewarded disproportionately.
            - Stocks trading at or above their {MA_WINDOW}-day MA (0% discount) are **excluded** from
              this month's buy.
            - Weights are normalized so the total spend always equals exactly **${TOTAL_BUDGET:.0f}**.
            - Any stock whose computed share falls below **${MIN_ALLOCATION:.0f}** is dropped, and its
              money is redistributed proportionally among the remaining stocks — so you're not placing
              tiny, fee-inefficient orders.
            """
        )
else:
    st.error("Unable to load data. Please check your internet connection or try again.")