import streamlit as st
import yfinance as yf
import pandas as pd

# Set up page config
st.set_page_config(page_title="Institutional Breakout Dashboard", layout="wide")
st.title("📈 20-Day High Breakout Tracker")
st.caption("Auto-refreshes to monitor technical breakouts across major global tickers.")

# Define the watchlist mapped to their market tickers
WATCHLIST = {
    "NVIDIA": "NVDA", "Google": "GOOGL", "Apple": "AAPL", "Microsoft": "MSFT", 
    "Amazon": "AMZN", "Broadcom": "AVGO", "Tesla": "TSLA", "Meta": "META", 
    "Micron": "MU", "Eli Lilly": "LLY", "Berkshire Hathaway": "BRK-A", 
    "JPMorgan Chase": "JPM", "AMD": "AMD", "Intel": "INTC", "Netflix": "NFLX", 
    "Visa": "V", "Accenture": "ACN", "SpaceX": "SPCX"
}

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
                
            # Isolate the trailing 20 trading days (excluding today's live candle for high calculation)
            trailing_20_days = hist.iloc[-21:-1]
            highest_high_20d = trailing_20_days['High'].max()
            
            # Get current market price
            current_price = hist['Close'].iloc[-1]
            price_change = current_price - hist['Close'].iloc[-2]
            pct_change = (price_change / hist['Close'].iloc[-2]) * 100
            
            # Technical condition check
            crossed_high = current_price >= highest_high_20d
            
            data_list.append({
                "Company": company,
                "Ticker": ticker,
                "Live Price": round(current_price, 2),
                "Daily Change": f"{price_change:+.2f} ({pct_change:+.2f}%)",
                "20-Day High Target": round(highest_high_20d, 2),
                "Breakout Status": "🚀 BREAKOUT" if crossed_high else "🔒 Below High",
                "Distance to High": f"{((current_price - highest_high_20d) / highest_high_20d) * 100:.2f}%"
            })
        except Exception as e:
            # Catch errors for missing listings or down network tickers
            pass
            
    return pd.DataFrame(data_list)

# Dashboard controls
col1, col2 = st.columns([1, 5])
with col1:
    if st.button("🔄 Force Refresh"):
        st.cache_data.clear()

# Fetch and build data layout
with st.spinner("Fetching live market data..."):
    df = fetch_stock_data(WATCHLIST)

if not df.empty:
    # Highlight rows that have broken past their 20-day high ceiling
    def highlight_breakouts(row):
        return ['background-color: #2e7d32; color: white' if row['Breakout Status'] == "🚀 BREAKOUT" else '' for _ in row]
    
    styled_df = df.style.apply(highlight_breakouts, axis=1)
    
    # Render Dashboard Grid
    st.dataframe(styled_df, use_container_width=True, height=650)
else:
    st.error("Unable to load data. Please check your internet connection or try again.")