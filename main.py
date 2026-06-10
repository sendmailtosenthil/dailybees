import os
import json
import datetime
import smtplib
from email.message import EmailMessage
from email.utils import make_msgid
import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt

STATE_FILE = 'state.json'

def load_state():
    with open(STATE_FILE, 'r') as f:
        return json.load(f)

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

def fetch_data():
    end_date = datetime.datetime.today()
    start_date = end_date - datetime.timedelta(days=90)
    
    nifty = yf.download("NIFTYBEES.NS", start=start_date.strftime("%Y-%m-%d"), end=end_date.strftime("%Y-%m-%d"))
    gold = yf.download("GOLDBEES.NS", start=start_date.strftime("%Y-%m-%d"), end=end_date.strftime("%Y-%m-%d"))
    
    nifty_close = nifty['Close'] if 'Close' in nifty else nifty.iloc[:, 3] 
    gold_close = gold['Close'] if 'Close' in gold else gold.iloc[:, 3]

    if isinstance(nifty_close, pd.DataFrame): nifty_close = nifty_close.iloc[:, 0]
    if isinstance(gold_close, pd.DataFrame): gold_close = gold_close.iloc[:, 0]
        
    df = pd.DataFrame({
        'NIFTY': nifty_close,
        'GOLD': gold_close
    }).dropna()
    
    df['Ratio'] = df['NIFTY'] / df['GOLD']
    window = 20
    df['Upper'] = df['Ratio'].shift(1).rolling(window=window).max()
    df['Lower'] = df['Ratio'].shift(1).rolling(window=window).min()
    
    return df

def create_chart(df, latest_date):
    plt.figure(figsize=(10, 5))
    plot_df = df.tail(60).copy()
    
    plt.plot(plot_df.index, plot_df['Ratio'], label='NIFTY/GOLD Ratio', color='#2b6cb0', linewidth=2)
    plt.plot(plot_df.index, plot_df['Upper'], label='20-day Upper Channel', color='#48bb78', linestyle='--')
    plt.plot(plot_df.index, plot_df['Lower'], label='20-day Lower Channel', color='#f56565', linestyle='--')
    
    plt.title(f'Donchian Channel Signals (As of {latest_date})')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig('chart.png')
    plt.close()

def main():
    state = load_state()
    df = fetch_data()
    
    if df.empty:
        print("No data fetched. Market might be closed or API is down.")
        return

    latest_row = df.iloc[-1]
    latest_date_str = df.index[-1].strftime("%Y-%m-%d")
    
    if state.get('last_run_date') == latest_date_str:
        print(f"Already ran for date {latest_date_str}. Exiting.")
        return

    nifty_price = float(latest_row['NIFTY'])
    gold_price = float(latest_row['GOLD'])
    ratio = float(latest_row['Ratio'])
    upper = float(latest_row['Upper'])
    lower = float(latest_row['Lower'])
    
    # Auto-adjust for splits (basic detection)
    if state.get('last_nifty_price') and nifty_price < state['last_nifty_price'] * 0.5:
        split_ratio = round(state['last_nifty_price'] / nifty_price)
        state['units_nifty'] *= split_ratio
    if state.get('last_gold_price') and gold_price < state['last_gold_price'] * 0.5:
        split_ratio = round(state['last_gold_price'] / gold_price)
        state['units_gold'] *= split_ratio
        
    state['last_nifty_price'] = nifty_price
    state['last_gold_price'] = gold_price

    # Check Signals
    new_target = state['target_asset']
    action_message = "No switch action required today. Holding steady."
    alert_color = "#4a5568"
    
    if ratio > upper:
        new_target = 'NIFTY'
    elif ratio < lower:
        new_target = 'GOLD'
        
    if new_target != state['target_asset']:
        state['target_asset'] = new_target
        state['transition_days_left'] = 3
        state['switch_count'] += 1
        if state['target_asset'] == 'NIFTY':
            state['sell_asset'] = 'GOLD'
            state['buy_asset'] = 'NIFTY'
        else:
            state['sell_asset'] = 'NIFTY'
            state['buy_asset'] = 'GOLD'
        
        action_message = f"🚨 URGENT: SIGNAL GENERATED TO SWITCH TO {state['target_asset']}! Execute your first 50% transfer today."
        alert_color = "#e53e3e"
        
    elif state['transition_days_left'] > 0:
        if state['transition_days_left'] == 2:
            action_message = f"⚠️ ONGOING TRANSITION: Execute your second 25% transfer to {state['target_asset']} today."
            alert_color = "#dd6b20"
        elif state['transition_days_left'] == 1:
            action_message = f"⚠️ ONGOING TRANSITION: Execute your final 25% transfer to {state['target_asset']} today."
            alert_color = "#dd6b20"

    # We assume the user executes the trade, so we automatically update the tracker based on the 50-25-25 model
    if state['transition_days_left'] > 0:
        fraction_to_sell = 0.50 if state['transition_days_left'] in [3, 2] else 1.00
        
        if state['sell_asset'] == 'NIFTY':
            units_to_sell = state['units_nifty'] * fraction_to_sell
            sell_value = units_to_sell * nifty_price
            state['units_nifty'] -= units_to_sell
        else:
            units_to_sell = state['units_gold'] * fraction_to_sell
            sell_value = units_to_sell * gold_price
            state['units_gold'] -= units_to_sell
            
        sell_charges = (sell_value * 0.0005) + 35
        net_proceeds = sell_value - sell_charges
        buy_invested = net_proceeds / 1.0005
        
        if state['buy_asset'] == 'NIFTY':
            units_bought = buy_invested / nifty_price
            state['units_nifty'] += units_bought
        else:
            units_bought = buy_invested / gold_price
            state['units_gold'] += units_bought
            
        state['transition_days_left'] -= 1

    # Calculate Metrics
    current_value = (state['units_nifty'] * nifty_price) + (state['units_gold'] * gold_price)
    day_diff = current_value - state.get('previous_close_value', current_value)
    state['previous_close_value'] = current_value
    state['last_run_date'] = latest_date_str
    
    invested_date = datetime.datetime.strptime(state['invested_date'], "%Y-%m-%d")
    invested_days = (end_date - invested_date).days
    
    roi = ((current_value / state['invested_amount']) - 1) * 100
    
    # Determine which instrument is primarily held
    current_instrument = "Gold Bees" if state['units_gold'] * gold_price > state['units_nifty'] * nifty_price else "Nifty Bees"
    total_units = state['units_gold'] if current_instrument == "Gold Bees" else state['units_nifty']

    # Generate Chart
    create_chart(df, latest_date_str)
    
    # Send Email
    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: auto;">
        <h2 style="color: #2b6cb0; text-align: center;">Daily Donchian Strategy Report</h2>
        
        <div style="background-color: {alert_color}; color: white; padding: 15px; border-radius: 8px; text-align: center; font-weight: bold; margin-bottom: 20px;">
            {action_message}
        </div>
        
        <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
          <tr><th style="text-align: left; padding: 8px; border-bottom: 1px solid #ddd;">Metric</th><th style="text-align: right; padding: 8px; border-bottom: 1px solid #ddd;">Value</th></tr>
          <tr><td style="padding: 8px; border-bottom: 1px solid #eee;">Investment Instrument</td><td style="text-align: right; padding: 8px; border-bottom: 1px solid #eee;"><b>{current_instrument}</b></td></tr>
          <tr><td style="padding: 8px; border-bottom: 1px solid #eee;">Invested Date</td><td style="text-align: right; padding: 8px; border-bottom: 1px solid #eee;">{state['invested_date']}</td></tr>
          <tr><td style="padding: 8px; border-bottom: 1px solid #eee;">Initial Invested Amount</td><td style="text-align: right; padding: 8px; border-bottom: 1px solid #eee;">₹{state['invested_amount']:,.2f}</td></tr>
          <tr><td style="padding: 8px; border-bottom: 1px solid #eee;">Current Value</td><td style="text-align: right; padding: 8px; border-bottom: 1px solid #eee;"><b>₹{current_value:,.2f}</b></td></tr>
          <tr><td style="padding: 8px; border-bottom: 1px solid #eee;">Units Held</td><td style="text-align: right; padding: 8px; border-bottom: 1px solid #eee;">{total_units:,.2f}</td></tr>
          <tr><td style="padding: 8px; border-bottom: 1px solid #eee;">Day's PnL</td><td style="text-align: right; padding: 8px; border-bottom: 1px solid #eee; color: {'green' if day_diff >= 0 else 'red'};">₹{day_diff:,.2f}</td></tr>
          <tr><td style="padding: 8px; border-bottom: 1px solid #eee;">Invested Days</td><td style="text-align: right; padding: 8px; border-bottom: 1px solid #eee;">{invested_days}</td></tr>
          <tr><td style="padding: 8px; border-bottom: 1px solid #eee;">Return on Investment (ROI)</td><td style="text-align: right; padding: 8px; border-bottom: 1px solid #eee; color: {'green' if roi >= 0 else 'red'};">{roi:.2f}%</td></tr>
          <tr><td style="padding: 8px; border-bottom: 1px solid #eee;">Number of Switches</td><td style="text-align: right; padding: 8px; border-bottom: 1px solid #eee;">{state['switch_count']}</td></tr>
        </table>
        
        <h3 style="color: #2b6cb0;">Current Prices (As of {latest_date_str})</h3>
        <ul>
            <li><b>NIFTYBEES:</b> ₹{nifty_price:.2f}</li>
            <li><b>GOLDBEES:</b> ₹{gold_price:.2f}</li>
        </ul>
        
        <h3 style="color: #2b6cb0;">Technical Chart</h3>
        <p>If the blue line crosses the red line downwards, switch to GOLD. If it crosses the green line upwards, switch to NIFTY.</p>
        <img src="cid:chart_image" alt="Donchian Chart" style="width: 100%; border-radius: 8px; border: 1px solid #ddd;">
        
      </body>
    </html>
    """
    
    send_email(html_content, "Daily Donchian Strategy Update", ['chart.png'])
    save_state(state)
    print("Report sent and state updated.")

def send_email(html_content, subject, image_paths):
    sender_email = os.environ.get('GMAIL_USER')
    sender_password = os.environ.get('GMAIL_PASS')
    receiver_email = 'sendmailtosenthil@gmail.com'
    
    if not sender_email or not sender_password:
        print("Email credentials not found in environment variables. Skipping email send.")
        return
        
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = sender_email
    msg['To'] = receiver_email
    
    msg.set_content("Please enable HTML to view this report.")
    msg.add_alternative(html_content, subtype='html')
    
    with open('chart.png', 'rb') as img:
        maintype, subtype = 'image', 'png'
        msg.get_payload()[1].add_related(img.read(), maintype=maintype, subtype=subtype, cid='<chart_image>')
        
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(sender_email, sender_password)
            smtp.send_message(msg)
    except Exception as e:
        print(f"Failed to send email: {e}")

if __name__ == '__main__':
    main()
