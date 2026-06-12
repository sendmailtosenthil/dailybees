import os
import json
import datetime
import smtplib
from email.message import EmailMessage
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

def clean_indian_etf_data(series):
    adj = series.copy()
    for i in range(1, len(adj) - 5):
        prev = adj.iloc[i-1]
        curr = adj.iloc[i]
        if curr < prev * 0.5:
            if adj.iloc[i:i+5].max() > prev * 0.8:
                adj.iloc[i] = prev
                for j in range(i+1, i+5):
                    if adj.iloc[j] < prev * 0.5:
                        adj.iloc[j] = prev
                    else:
                        break
    for i in range(1, len(adj)):
        prev = adj.iloc[i-1]
        curr = adj.iloc[i]
        if curr < prev * 0.5:
            ratio = round(prev / curr)
            if ratio >= 2:
                adj.iloc[:i] = adj.iloc[:i] / ratio
    return adj

def get_clean_close(ticker, start_date, end_date):
    df = yf.download(ticker, start=start_date, end=end_date, progress=False)
    close = df['Close'] if 'Close' in df else df.iloc[:, 3]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    close = close[close > 0].dropna()
    return clean_indian_etf_data(close)

def create_chart(df, latest_date, pair_name, window, filename):
    plt.figure(figsize=(10, 5))
    plot_df = df.tail(60).copy()
    
    plt.plot(plot_df.index, plot_df['Ratio'], label=f'{pair_name} Ratio', color='#2b6cb0', linewidth=2)
    plt.plot(plot_df.index, plot_df['Upper'], label=f'{window}-day Upper', color='#48bb78', linestyle='--')
    plt.plot(plot_df.index, plot_df['Lower'], label=f'{window}-day Lower', color='#f56565', linestyle='--')
    
    plt.title(f'Donchian Signals: {pair_name} (As of {latest_date})')
    plt.legend()
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(filename)
    plt.close()

def process_strategy(key, strat, today_str):
    window = strat['window']
    pair_name = strat['pair_name']
    asset1 = strat['asset1']
    asset2 = strat['asset2']
    
    end_date_dt = datetime.datetime.today()
    start_date_dt = end_date_dt - datetime.timedelta(days=window * 2 + 30) # Enough data for rolling window
    
    end_date = end_date_dt.strftime("%Y-%m-%d")
    start_date = start_date_dt.strftime("%Y-%m-%d")
    
    adj1 = get_clean_close(asset1, start_date, end_date)
    adj2 = get_clean_close(asset2, start_date, end_date)
    
    df = pd.DataFrame({
        'ASSET1': adj1,
        'ASSET2': adj2
    }).dropna()
    
    if df.empty:
        print(f"No data for {pair_name}")
        return None
        
    df['Ratio'] = df['ASSET1'] / df['ASSET2']
    df['Upper'] = df['Ratio'].shift(1).rolling(window=window).max()
    df['Lower'] = df['Ratio'].shift(1).rolling(window=window).min()
    
    latest_row = df.iloc[-1]
    latest_date_str = df.index[-1].strftime("%Y-%m-%d")
    
    price1 = float(latest_row['ASSET1'])
    price2 = float(latest_row['ASSET2'])
    ratio = float(latest_row['Ratio'])
    upper = float(latest_row['Upper'])
    lower = float(latest_row['Lower'])
    
    # Process User Manual Inputs
    user_input = strat.get('user_input', {})
    fresh_cash = user_input.get('fresh_cash_added', 0.0)
    if fresh_cash != 0:
        strat['invested_amount'] += fresh_cash
        strat['cash_flows'].append({"date": today_str, "amount": -fresh_cash})
        
    strat['units_1'] += user_input.get('asset1_units_change', 0.0)
    strat['units_2'] += user_input.get('asset2_units_change', 0.0)
    
    if user_input.get('mark_switch_completed', False):
        strat['pending_switch'] = False
        
    # Reset User Inputs
    strat['user_input'] = {
        "fresh_cash_added": 0.0,
        "asset1_units_change": 0.0,
        "asset2_units_change": 0.0,
        "mark_switch_completed": False
    }

    # Check Strategy Signals
    if ratio > upper and strat['current_signal_target'] != 'ASSET1':
        strat['current_signal_target'] = 'ASSET1'
        strat['pending_switch'] = True
    elif ratio < lower and strat['current_signal_target'] != 'ASSET2':
        strat['current_signal_target'] = 'ASSET2'
        strat['pending_switch'] = True

    # Calculate Metrics
    current_value = (strat['units_1'] * price1) + (strat['units_2'] * price2)
    day_diff = current_value - strat.get('previous_close_value', current_value)
    strat['previous_close_value'] = current_value
    
    first_invest_date = datetime.datetime.strptime(strat['cash_flows'][0]['date'], "%Y-%m-%d")
    
    roi = ((current_value / strat['invested_amount']) - 1) * 100 if strat['invested_amount'] > 0 else 0
    
    # Calculate XIRR
    dates = [datetime.datetime.strptime(cf['date'], "%Y-%m-%d") for cf in strat['cash_flows']]
    amounts = [cf['amount'] for cf in strat['cash_flows']]
    
    dates.append(datetime.datetime.today())
    amounts.append(current_value)
    
    try:
        calculated_xirr = xirr(dates, amounts)
        if calculated_xirr is not None:
            calculated_xirr = calculated_xirr * 100
        else:
            calculated_xirr = 0.0
    except Exception as e:
        print(f"XIRR Calculation failed for {pair_name}: {e}")
        calculated_xirr = 0.0
        
    # Determine which instrument is primarily held
    current_instrument = asset1 if strat['units_1'] * price1 > strat['units_2'] * price2 else asset2
    total_units = strat['units_1'] if current_instrument == asset1 else strat['units_2']

    # Generate Chart
    chart_filename = f'chart_{key}.png'
    create_chart(df, latest_date_str, pair_name, window, chart_filename)
    
    # Generate action message
    target_asset = asset1 if strat['current_signal_target'] == 'ASSET1' else asset2
    alert_color = "#4a5568"
    if strat['pending_switch']:
        action_message = f"🚨 URGENT: PENDING SWITCH TO {target_asset}! Execute trades on broker, update state.json, and mark_switch_completed to true."
        alert_color = "#e53e3e"
    else:
        action_message = f"✅ No action required. Holding steady in {target_asset}."

    # Return section HTML data
    return {
        "html": f"""
        <div style="margin-bottom: 40px; border: 1px solid #ddd; padding: 20px; border-radius: 8px;">
            <h2 style="color: #2b6cb0; margin-top: 0;">{pair_name} ({window}-Day Channel)</h2>
            <div style="background-color: {alert_color}; color: white; padding: 10px; border-radius: 5px; text-align: center; font-weight: bold; margin-bottom: 15px;">
                {action_message}
            </div>
            <table style="width: 100%; border-collapse: collapse; margin-bottom: 15px;">
              <tr><td style="padding: 5px; border-bottom: 1px solid #eee;">Investment Instrument</td><td style="text-align: right; padding: 5px; border-bottom: 1px solid #eee;"><b>{current_instrument}</b></td></tr>
              <tr><td style="padding: 5px; border-bottom: 1px solid #eee;">Total Invested Cash</td><td style="text-align: right; padding: 5px; border-bottom: 1px solid #eee;">₹{strat['invested_amount']:,.2f}</td></tr>
              <tr><td style="padding: 5px; border-bottom: 1px solid #eee;">Current Value</td><td style="text-align: right; padding: 5px; border-bottom: 1px solid #eee;"><b>₹{current_value:,.2f}</b></td></tr>
              <tr><td style="padding: 5px; border-bottom: 1px solid #eee;">Units Held</td><td style="text-align: right; padding: 5px; border-bottom: 1px solid #eee;">{total_units:,.2f}</td></tr>
              <tr><td style="padding: 5px; border-bottom: 1px solid #eee;">Day's PnL</td><td style="text-align: right; padding: 5px; border-bottom: 1px solid #eee; color: {'green' if day_diff >= 0 else 'red'};">₹{day_diff:,.2f}</td></tr>
              <tr><td style="padding: 5px; border-bottom: 1px solid #eee;">Total ROI</td><td style="text-align: right; padding: 5px; border-bottom: 1px solid #eee; color: {'green' if roi >= 0 else 'red'};">{roi:.2f}%</td></tr>
              <tr><td style="padding: 5px; border-bottom: 1px solid #eee;"><b>Annualized XIRR</b></td><td style="text-align: right; padding: 5px; border-bottom: 1px solid #eee; color: {'green' if calculated_xirr >= 0 else 'red'};"><b>{calculated_xirr:.2f}%</b></td></tr>
            </table>
            
            <p style="font-size: 14px; margin-bottom: 5px;"><b>{asset1} Price:</b> ₹{price1:.2f}</p>
            <p style="font-size: 14px; margin-top: 0; margin-bottom: 15px;"><b>{asset2} Price:</b> ₹{price2:.2f}</p>
            
            <img src="cid:{key}_image" alt="Donchian Chart" style="width: 100%; border-radius: 8px; border: 1px solid #ddd;">
        </div>
        """,
        "image_cid": f"{key}_image",
        "image_path": chart_filename
    }


def main():
    state = load_state()
    today_str = datetime.datetime.today().strftime("%Y-%m-%d")
    
    if state.get('last_run_date') == today_str:
        print(f"Already ran today ({today_str}). Exiting.")
        return

    sections_html = ""
    images_to_attach = []

    for key, strat in state['strategies'].items():
        result = process_strategy(key, strat, today_str)
        if result:
            sections_html += result['html']
            images_to_attach.append((result['image_path'], result['image_cid']))
            
    if not sections_html:
        print("No valid data generated for any strategy.")
        return
        
    state['last_run_date'] = today_str

    # Build final email
    html_content = f"""
    <html>
      <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: auto;">
        <h1 style="color: #2c3e50; text-align: center;">Daily Portfolio Report</h1>
        {sections_html}
      </body>
    </html>
    """
    
    send_email(html_content, "Daily Donchian Multi-Strategy Update", images_to_attach)
    save_state(state)
    print("Report sent and state updated.")

def send_email(html_content, subject, images):
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
    
    for img_path, cid in images:
        with open(img_path, 'rb') as img:
            msg.get_payload()[1].add_related(img.read(), maintype='image', subtype='png', cid=f'<{cid}>')
            
    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(sender_email, sender_password)
            smtp.send_message(msg)
    except Exception as e:
        print(f"Failed to send email: {e}")

if __name__ == '__main__':
    main()
