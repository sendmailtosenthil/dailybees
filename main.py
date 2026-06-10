import os
import json
import datetime
import smtplib
from email.message import EmailMessage
from email.utils import make_msgid
import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
from pyxirr import xirr

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
    today_str = datetime.datetime.today().strftime("%Y-%m-%d")
    
    if state.get('last_run_date') == latest_date_str:
        print(f"Already ran for date {latest_date_str}. Exiting.")
        return

    nifty_price = float(latest_row['NIFTY'])
    gold_price = float(latest_row['GOLD'])
    ratio = float(latest_row['Ratio'])
    upper = float(latest_row['Upper'])
    lower = float(latest_row['Lower'])
    
    # Process User Manual Inputs
    user_input = state.get('user_input', {})
    fresh_cash = user_input.get('fresh_cash_added', 0.0)
    if fresh_cash != 0:
        state['invested_amount'] += fresh_cash
        state['cash_flows'].append({"date": today_str, "amount": -fresh_cash})
        
    state['units_gold'] += user_input.get('gold_units_change', 0.0)
    state['units_nifty'] += user_input.get('nifty_units_change', 0.0)
    
    if user_input.get('mark_switch_completed', False):
        state['pending_switch'] = False
        
    # Reset User Inputs
    state['user_input'] = {
        "fresh_cash_added": 0.0,
        "gold_units_change": 0.0,
        "nifty_units_change": 0.0,
        "mark_switch_completed": False
    }

    # Check Strategy Signals
    if ratio > upper and state['current_signal_target'] != 'NIFTY':
        state['current_signal_target'] = 'NIFTY'
        state['pending_switch'] = True
    elif ratio < lower and state['current_signal_target'] != 'GOLD':
        state['current_signal_target'] = 'GOLD'
        state['pending_switch'] = True

    # Generate Action Message
    alert_color = "#4a5568"
    if state['pending_switch']:
        target = state['current_signal_target']
        action_message = f"🚨 URGENT: PENDING SWITCH TO {target}! Please execute your trades on your broker. Then update 'nifty_units_change' and 'gold_units_change' in state.json. Set 'mark_switch_completed' to true once you are fully done."
        alert_color = "#e53e3e"
    else:
        action_message = f"✅ No action required. Holding steady in {state['current_signal_target']}."

    # Calculate Metrics
    current_value = (state['units_nifty'] * nifty_price) + (state['units_gold'] * gold_price)
    day_diff = current_value - state.get('previous_close_value', current_value)
    state['previous_close_value'] = current_value
    state['last_run_date'] = latest_date_str
    
    first_invest_date = datetime.datetime.strptime(state['cash_flows'][0]['date'], "%Y-%m-%d")
    invested_days = (datetime.datetime.today() - first_invest_date).days
    
    roi = ((current_value / state['invested_amount']) - 1) * 100 if state['invested_amount'] > 0 else 0
    
    # Calculate XIRR
    dates = [datetime.datetime.strptime(cf['date'], "%Y-%m-%d") for cf in state['cash_flows']]
    amounts = [cf['amount'] for cf in state['cash_flows']]
    
    dates.append(datetime.datetime.today())
    amounts.append(current_value)
    
    try:
        calculated_xirr = xirr(dates, amounts)
        if calculated_xirr is not None:
            calculated_xirr = calculated_xirr * 100
        else:
            calculated_xirr = 0.0
    except Exception as e:
        print(f"XIRR Calculation failed: {e}")
        calculated_xirr = 0.0
        
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
          <tr><td style="padding: 8px; border-bottom: 1px solid #eee;">Total Invested Cash</td><td style="text-align: right; padding: 8px; border-bottom: 1px solid #eee;">₹{state['invested_amount']:,.2f}</td></tr>
          <tr><td style="padding: 8px; border-bottom: 1px solid #eee;">Current Value</td><td style="text-align: right; padding: 8px; border-bottom: 1px solid #eee;"><b>₹{current_value:,.2f}</b></td></tr>
          <tr><td style="padding: 8px; border-bottom: 1px solid #eee;">Units Held</td><td style="text-align: right; padding: 8px; border-bottom: 1px solid #eee;">{total_units:,.2f}</td></tr>
          <tr><td style="padding: 8px; border-bottom: 1px solid #eee;">Day's PnL</td><td style="text-align: right; padding: 8px; border-bottom: 1px solid #eee; color: {'green' if day_diff >= 0 else 'red'};">₹{day_diff:,.2f}</td></tr>
          <tr><td style="padding: 8px; border-bottom: 1px solid #eee;">Total ROI</td><td style="text-align: right; padding: 8px; border-bottom: 1px solid #eee; color: {'green' if roi >= 0 else 'red'};">{roi:.2f}%</td></tr>
          <tr><td style="padding: 8px; border-bottom: 1px solid #eee;"><b>Annualized XIRR</b></td><td style="text-align: right; padding: 8px; border-bottom: 1px solid #eee; color: {'green' if calculated_xirr >= 0 else 'red'};"><b>{calculated_xirr:.2f}%</b></td></tr>
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
