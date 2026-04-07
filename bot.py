import os
import logging
import requests
import threading
from datetime import datetime, timedelta
import pytz
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from skyfield.api import Topos, load, EarthSatellite
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from flask import Flask, jsonify, request, send_file
from flask_cors import CORS

# O an botu kullananları aklında tutması için geçici bir küme
active_chats = set()

# Loglama ayarları
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Zaman Dilimi
TURKEY_TZ = pytz.timezone('Europe/Istanbul')

# Bellek (Veritabanı niyetine)
user_data = {}

# Skyfield Zaman Ölçeği
ts = load.timescale()

# ==========================================
# --- WEB SUNUCUSU (API) KURULUMU ---
# ==========================================
app = Flask(__name__)
CORS(app) # Tarayıcımızın bu API'den veri çekebilmesi için güvenlik izni (Cross-Origin)

@app.route('/')
def home():
    return "🛰️ Satellite Tracker API is running smoothly!"

@app.route('/view')
def view_tracker():
    """index.html dosyasını doğrudan sunucu üzerinden yayınlar"""
    try:
        return send_file('index.html')
    except Exception as e:
        return f"Hata: index.html dosyası bulunamadı. Detay: {e}", 404

@app.route('/api/data')
def api_data():
    """Web sitemizin çağıracağı ana köprü. SADECE İSTENEN KİŞİNİN (chat_id) uydularını JSON olarak döndürür."""
    chat_id = request.args.get('chat_id', type=int)
    
    # Kullanıcı yoksa veya ID gönderilmediyse hata dön
    if not chat_id or chat_id not in user_data:
        return jsonify({'error': 'User not found or no satellites tracked.'}), 404
        
    data = user_data[chat_id]
    main_gs = data.get('global_gs', {'lat': 39.89110, 'lon': 32.77870, 'alt': 925, 'name': 'TUBITAK UZAY ANKARA'})
    
    satellites = []
    
    # Sadece o kullanıcının hafızasındaki uyduları topla
    for sat_id, sat_info in data['satellites'].items():
        satellites.append({
            'id': sat_id,
            'name': sat_info['tle'][2],
            'line1': sat_info['tle'][0],
            'line2': sat_info['tle'][1]
        })
    
    return jsonify({
        'ground_station': main_gs,
        'satellites': satellites
    })

def run_api():
    """Render.com'un atayacağı portu (veya yerelde 8080'i) dinler"""
    port = int(os.environ.get('PORT', 8080))
    # use_reloader=False çok önemli, aksi takdirde Telegram botuyla çakışır
    app.run(host='0.0.0.0', port=port, use_reloader=False)

# ==========================================
# --- YARDIMCI FONKSİYONLAR ---
# ==========================================

def get_tle_enhanced(norad_id):
    """3 Kademeli ve Raporlamalı TLE Çekme Sistemi."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Connection': 'keep-alive',
    }
    
    try:
        url = f'https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=tle'
        resp = requests.get(url, headers=headers, timeout=8)
        if resp.status_code == 200 and not resp.text.strip().startswith('<'):
            lines = resp.text.strip().split('\n')
            if len(lines) >= 3:
                return lines[1].strip(), lines[2].strip(), lines[0].strip(), "CelesTrak"
    except Exception: pass

    try:
        alt_url = f'https://tle.ivanstanojevic.me/api/tle/{norad_id}'
        resp = requests.get(alt_url, headers=headers, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            if 'line1' in data and 'line2' in data:
                return data['line1'], data['line2'], data.get('name', f"SAT-{norad_id}"), "Ivan API"
    except Exception: pass

    st_user = os.environ.get("SPACE_TRACK_USER")
    st_pass = os.environ.get("SPACE_TRACK_PASSWORD")
    
    if st_user and st_pass:
        try:
            session = requests.Session()
            login_url = "https://www.space-track.org/ajaxauth/login"
            login_data = {'identity': st_user, 'password': st_pass}
            session.post(login_url, data=login_data, timeout=10)
            
            query_url = f"https://www.space-track.org/basicspacedata/query/class/gp/NORAD_CAT_ID/{norad_id}/format/tle/emptyresult/show"
            resp = session.get(query_url, timeout=10)
            
            if resp.status_code == 200 and len(resp.text) > 20:
                lines = resp.text.strip().split('\n')
                if len(lines) >= 2:
                    line1 = lines[0].strip()
                    line2 = lines[1].strip()
                    return line1, line2, f"SAT-{norad_id}", "Space-Track"
        except Exception as e:
            logger.error(f"Space-Track Hatası ({norad_id}): {e}")

    return None, None, None, None

def calculate_passes(chat_id, sat_id, days=2):
    data = user_data.get(chat_id)
    if not data or sat_id not in data['satellites']:
        return []

    sat_info = data['satellites'][sat_id]
    line1, line2, sat_name = sat_info['tle']
    sat = EarthSatellite(line1, line2, sat_name, ts)
    
    gs = sat_info.get('custom_gs') or data['global_gs']
    station = Topos(latitude_degrees=gs['lat'], longitude_degrees=gs['lon'], elevation_m=gs['alt'])
    
    min_el_threshold = data.get('min_elevation', 0)

    t0 = ts.now()
    t1 = ts.utc(t0.utc_datetime() + timedelta(days=days))
    
    t, events = sat.find_events(station, t0, t1, altitude_degrees=0.0)
    
    passes = []
    current_pass = {}
    
    for ti, event in zip(t, events):
        event_time = ti.utc_datetime().replace(tzinfo=pytz.utc).astimezone(TURKEY_TZ)
        if event == 0: 
            current_pass['aos'] = event_time
            current_pass['tca'] = None
            current_pass['los'] = None
        elif event == 1 and 'aos' in current_pass: 
            current_pass['tca'] = event_time
            difference = sat - station
            topocentric = difference.at(ti)
            alt, az, distance = topocentric.altaz()
            current_pass['max_el'] = alt.degrees
        elif event == 2 and 'aos' in current_pass: 
            current_pass['los'] = event_time
            if current_pass.get('max_el', 0) >= min_el_threshold:
                passes.append(current_pass)
            current_pass = {}
            
    return passes

async def send_pass_schedule(chat_id, sat_id, context: ContextTypes.DEFAULT_TYPE):
    data = user_data.get(chat_id)
    if not data or sat_id not in data['satellites']:
        return

    passes = calculate_passes(chat_id, sat_id, days=1)
    
    sat_info = data['satellites'][sat_id]
    sat_name = sat_info['tle'][2]
    gs = sat_info.get('custom_gs') or data['global_gs']
    gs_name = gs['name']

    if not passes:
        await context.bot.send_message(
            chat_id=chat_id, 
            text=f"⚠️ No passes found above your {data.get('min_elevation', 0)}° filter for <b>{sat_name}</b> ({sat_id}) from <b>{gs_name}</b> in the next 24 hours.",
            parse_mode='HTML'
        )
        return

    msg = f"📅 <b>24-Hour Pass Schedule for {sat_name} ({sat_id})</b>\n"
    msg += f"📍 Ground Station: {gs_name}\n\n"

    for i, p in enumerate(passes):
        aos = p['aos'].strftime('%d %b %H:%M:%S')
        tca = p['tca'].strftime('%H:%M:%S')
        los = p['los'].strftime('%H:%M:%S')
        max_el = p['max_el']
        
        dur_m, dur_s = divmod((p['los'] - p['aos']).total_seconds(), 60)

        msg += f"<b>Pass {i+1}:</b>\n"
        msg += f"• 🟢 AOS: {aos}\n"
        msg += f"• ⭐ TCA: {tca} (Max El: {max_el:.1f}°)\n"
        msg += f"• 🔴 LOS: {los}\n"
        msg += f"• ⏱️ Duration: {int(dur_m)}m {int(dur_s)}s\n\n"

    if len(msg) > 4000:
        for x in range(0, len(msg), 4000):
            await context.bot.send_message(chat_id=chat_id, text=msg[x:x+4000], parse_mode='HTML')
    else:
        await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode='HTML')

async def schedule_pass_alerts(chat_id, sat_id, context: ContextTypes.DEFAULT_TYPE):
    data = user_data.get(chat_id)
    if not data or sat_id not in data['satellites']:
        return

    scheduler = context.application.job_queue.scheduler
    
    for job in scheduler.get_jobs():
        if job.id.startswith(f"{chat_id}_{sat_id}_"):
            job.remove()

    passes = calculate_passes(chat_id, sat_id, days=2)
    
    sat_info = data['satellites'][sat_id]
    sat_name = sat_info['tle'][2]
    gs = sat_info.get('custom_gs') or data['global_gs']
    gs_name = gs['name']
    
    if not passes:
        return 

    remind_mins = sat_info.get('custom_remind') if sat_info.get('custom_remind') is not None else data['remind_time']
    now = datetime.now(TURKEY_TZ)
    
    next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    for i, p in enumerate(passes):
        aos = p.get('aos')
        tca = p.get('tca')
        los = p.get('los')
        max_el = p.get('max_el', 0)
        
        if not aos or not tca or not los:
            continue

        aos_str = aos.strftime('%H:%M:%S')
        tca_str = tca.strftime('%H:%M:%S')
        los_str = los.strftime('%H:%M:%S')
        
        duration = los - aos
        dur_m, dur_s = divmod(duration.total_seconds(), 60)
        
        tca_dur = tca - aos
        tca_m, tca_s = divmod(tca_dur.total_seconds(), 60)

        if warning_time := aos - timedelta(minutes=remind_mins):
            if warning_time > now:
                msg = (
                    f"🛰️ <b>INFO: {sat_name} is approaching {gs_name}!</b>\n"
                    f"AOS in just <b>{remind_mins} minutes</b>.\n\n"
                    f"📍 <b>Pass Summary:</b>\n"
                    f"• Station: <b>{gs_name}</b>\n"
                    f"• Max Elevation: <b>{max_el:.1f}°</b>\n"
                    f"• Max Elevation Time (TCA): <b>{tca_str}</b>\n"
                    f"• Total Visibility: {int(dur_m)}m {int(dur_s)}s"
                )
                scheduler.add_job(send_telegram_msg, 'date', run_date=warning_time, args=[chat_id, msg, context], id=f"{chat_id}_{sat_id}_warn_{aos.timestamp()}")

        if aos > now:
            msg = (
                f"🟢 <b>AOS: {sat_name} is now in the footprint of {gs_name}!</b>\n\n"
                f"⏱️ <b>Timeline:</b>\n"
                f"• AOS: <b>{aos_str}</b>\n"
                f"• Max Elevation Time (TCA): <b>{tca_str}</b> <i>(Elevation: {max_el:.1f}°)</i>\n"
                f"• LOS: <b>{los_str}</b>\n\n"
                f"Total pass duration: <b>{int(dur_m)}m {int(dur_s)}s</b>"
            )
            scheduler.add_job(send_telegram_msg, 'date', run_date=aos, args=[chat_id, msg, context], id=f"{chat_id}_{sat_id}_aos_{aos.timestamp()}")

        if tca > now:
            msg = (
                f"⭐ <b>TCA: {sat_name} is currently at its highest point!</b>\n"
                f"Current Elevation: <b>{max_el:.1f}°</b>\n"
                f"Pass will end in {int(dur_m - tca_m)}m {int(dur_s - tca_s)}s."
            )
            scheduler.add_job(send_telegram_msg, 'date', run_date=tca, args=[chat_id, msg, context], id=f"{chat_id}_{sat_id}_tca_{aos.timestamp()}")

        if los > now:
            msg = f"🔴 <b>LOS: {sat_name} has completed its pass over {gs_name}.</b>\nSatellite is out of the footprint."
            
            if i + 1 < len(passes):
                np = passes[i+1]
                n_aos = np['aos']
                n_dur = np['los'] - n_aos
                n_m, n_s = divmod(n_dur.total_seconds(), 60)
                
                msg += (
                    f"\n\n📅 <b>NEXT UPCOMING PASS ({n_aos.strftime('%b %d')}):</b>\n"
                    f"• AOS: <b>{n_aos.strftime('%H:%M:%S')}</b>\n"
                    f"• TCA: <b>{np['tca'].strftime('%H:%M:%S')}</b> <i>({np['max_el']:.1f}°)</i>\n"
                    f"• Duration: <b>{int(n_m)}m {int(n_s)}s</b>"
                )
                
                if n_aos >= next_midnight:
                    msg += f"\n\n<i>⚠️ Note: Estimated timings. This pass occurs after tonight's 00:00 TLE update and may slightly shift.</i>"
            else:
                msg += f"\n\n📅 <b>NEXT PASS:</b> No more passes scheduled in the next 48 hours."

            scheduler.add_job(send_telegram_msg, 'date', run_date=los, args=[chat_id, msg, context], id=f"{chat_id}_{sat_id}_los_{aos.timestamp()}")

async def send_telegram_msg(chat_id, text, context: ContextTypes.DEFAULT_TYPE):
    try:
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Mesaj gonderilemedi {chat_id}: {e}")

async def auto_daily_tle_update(context: ContextTypes.DEFAULT_TYPE):
    for chat_id, data in user_data.items():
        if not data.get('satellites'):
            continue 
            
        updated_sats = []
        failed_sats = []
        
        for sat_id in list(data['satellites'].keys()):
            old_name = data['satellites'][sat_id]['tle'][2]
            line1, line2, name, source = get_tle_enhanced(sat_id)
            
            if line1:
                sat_obj = EarthSatellite(line1, line2, name, ts)
                epoch_dt = sat_obj.epoch.utc_datetime().replace(tzinfo=pytz.utc).astimezone(TURKEY_TZ)
                epoch_str = epoch_dt.strftime('%d %b %H:%M')
                
                user_data[chat_id]['satellites'][sat_id]['tle'] = (line1, line2, name)
                
                status_line = f"✅ {name}: Fetched from <b>{source}</b>\n   └ <i>Data Epoch: {epoch_str}</i>"
                updated_sats.append(status_line)
            else:
                failed_sats.append(f"⚠️ {old_name}: All sources failed. Using cached data.")
                
            await schedule_pass_alerts(chat_id, sat_id, context)
            await send_pass_schedule(chat_id, sat_id, context)
            
        report_msg = "🔄 <b>Daily Maintenance Report</b>\n\n"
        if updated_sats:
            report_msg += "\n".join(updated_sats) + "\n\n"
        if failed_sats:
            report_msg += "\n".join(failed_sats) + "\n\n"
        report_msg += "📅 24-hour schedules calculated."
        
        await context.bot.send_message(chat_id=chat_id, text=report_msg, parse_mode='HTML')

def init_user(chat_id):
    if chat_id not in user_data:
        user_data[chat_id] = {
            'global_gs': {'lat': 39.89110, 'lon': 32.77870, 'alt': 925, 'name': 'TUBITAK UZAY ANKARA'},
            'remind_time': 10,
            'min_elevation': 0,
            'satellites': {}
        }

# --- TELEGRAM KOMUTLARI ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active_chats.add(chat_id)
    init_user(chat_id)
    await update.message.reply_text(
        "🛰️ Welcome to Satellite Tracker Bot!\n\n"
        "Default Station: TUBITAK UZAY ANKARA\n"
        "Use /info to see all available commands."
    )

async def cmd_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active_chats.add(chat_id)
    info_text = (
        "ℹ️ <b>Bot Commands & Usage:</b>\n\n"
        "🔸 <b>/satellite &lt;NORAD_ID1&gt; &lt;ID2&gt; ...</b> : Add one or more satellites to your fleet.\n"
        "<i>Example: /satellite 39030 25544</i>\n\n"
        "🔸 <b>/constellation</b> : Add the Turkish fleet (GÖKTÜRK-2, İMECE, GÖKTÜRK-1A) to your tracking list.\n"
        "<i>Example: /constellation OR /constellation default</i>\n\n"
        "🔸 <b>/listsatellites</b> : View all currently tracked satellites.\n\n"
        "🔸 <b>/viewsat</b> : 🌍 Get your private 3D Live Tracker link!\n\n"
        "🔸 <b>/groundstation &lt;lat&gt; &lt;lon&gt; &lt;alt&gt;</b> : Set global ground station.\n"
        "<i>Example: /groundstation 39.89 32.77 925</i>\n\n"
        "🔸 <b>/groundstation &lt;NORAD_ID&gt; &lt;lat&gt; &lt;lon&gt; &lt;alt&gt;</b> : Set a custom ground station for a specific satellite.\n"
        "<i>Example: /groundstation 39030 78.22 15.60 400</i>\n\n"
        "🔸 <b>/remindtime &lt;minutes&gt;</b> : Set global early warning time.\n"
        "<i>Example: /remindtime 15</i>\n\n"
        "🔸 <b>/remindtime &lt;NORAD_ID&gt; &lt;minutes&gt;</b> : Set warning time for a specific satellite.\n"
        "<i>Example: /remindtime 39030 5</i>\n\n"
        "🔸 <b>/minelevation &lt;degrees&gt;</b> : Filter out low passes.\n"
        "<i>Example: /minelevation 10</i>\n\n"
        "🔸 <b>/stop</b> : Stop all tracking.\n"
        "🔸 <b>/stop &lt;NORAD_ID&gt;</b> (or /removesatellite &lt;NORAD_ID&gt;) : Stop tracking a specific satellite.\n"
    )
    await update.message.reply_text(info_text, parse_mode='HTML')

async def cmd_viewsat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kullanıcıya özel 3D Tracker linki üretir"""
    chat_id = update.effective_chat.id
    active_chats.add(chat_id)
    init_user(chat_id)
    
    # Render'a yüklediğinde buradaki localhost'u kendi render linkin ile değiştirmelisin
    base_url = os.environ.get("WEB_URL", "http://127.0.0.1:8080")
    link = f"{base_url}/view?chat_id={chat_id}"
    
    await update.message.reply_text(
        f"🌍 <b>Your Personal 3D Tracker Ready!</b>\n\n"
        f"Click the link below to view your fleet live:\n"
        f"👉 {link}\n\n"
        f"<i>Note: Do not share this link if you want to keep your fleet tracking private.</i>",
        parse_mode='HTML'
    )

async def set_groundstation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active_chats.add(chat_id)
    args = context.args
    init_user(chat_id)

    if not args:
        await update.message.reply_text("⚠️ Usage: /groundstation <lat> <lon> <alt> OR /groundstation <NORAD_ID> <lat> <lon> <alt>\nExample: /groundstation 39.89 32.77 925")
        return

    if args[0].lower() == 'default':
        user_data[chat_id]['global_gs'] = {'lat': 39.89110, 'lon': 32.77870, 'alt': 925, 'name': 'TUBITAK UZAY ANKARA'}
        await update.message.reply_text("📍 Global Station reset to: <b>TUBITAK UZAY ANKARA</b>", parse_mode='HTML')
        for sat_id, sat_info in user_data[chat_id]['satellites'].items():
            if not sat_info.get('custom_gs'):
                await schedule_pass_alerts(chat_id, sat_id, context)
                await send_pass_schedule(chat_id, sat_id, context)
                
    elif len(args) == 3:
        try:
            lat, lon, alt = float(args[0]), float(args[1]), float(args[2])
            user_data[chat_id]['global_gs'] = {'lat': lat, 'lon': lon, 'alt': alt, 'name': f'Station ({lat:.2f}, {lon:.2f})'}
            await update.message.reply_text(f"📍 Global Station updated: <b>({lat}, {lon} | Alt: {alt}m)</b>", parse_mode='HTML')
            for sat_id, sat_info in user_data[chat_id]['satellites'].items():
                if not sat_info.get('custom_gs'):
                    await schedule_pass_alerts(chat_id, sat_id, context)
                    await send_pass_schedule(chat_id, sat_id, context)
        except ValueError:
            await update.message.reply_text("⚠️ Usage: /groundstation <lat> <lon> <alt>\nExample: /groundstation 39.89 32.77 925")
            
    elif len(args) == 4:
        try:
            sat_id = args[0]
            lat, lon, alt = float(args[1]), float(args[2]), float(args[3])
            
            if sat_id in user_data[chat_id]['satellites']:
                user_data[chat_id]['satellites'][sat_id]['custom_gs'] = {'lat': lat, 'lon': lon, 'alt': alt, 'name': f'Custom GS for {sat_id}'}
                await update.message.reply_text(f"📍 Specific Station for {sat_id} updated to: <b>({lat}, {lon} | Alt: {alt}m)</b>", parse_mode='HTML')
                await schedule_pass_alerts(chat_id, sat_id, context)
                await send_pass_schedule(chat_id, sat_id, context)
            else:
                await update.message.reply_text(f"❌ Error: Satellite {sat_id} is not currently being tracked. Track it first.")
        except ValueError:
            await update.message.reply_text("⚠️ Usage: /groundstation <NORAD_ID> <lat> <lon> <alt>\nExample: /groundstation 39030 78.22 15.60 400")
    else:
        await update.message.reply_text("⚠️ Usage: /groundstation <lat> <lon> <alt> OR /groundstation <NORAD_ID> <lat> <lon> <alt>\nExample: /groundstation 39.89 32.77 925")

async def set_satellite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active_chats.add(chat_id)
    args = context.args
    init_user(chat_id)

    if not args:
        await update.message.reply_text("⚠️ Usage: /satellite <NORAD_ID1> <ID2> ...\nExample: /satellite 39030 25544")
        return

    await update.message.reply_text(f"🚀 Adding {len(args)} satellite(s) to your fleet...")

    for sat_id in args:
        if sat_id in user_data[chat_id]['satellites']:
            await update.message.reply_text(f"ℹ️ Satellite {sat_id} is already in your fleet.")
            continue

        line1, line2, name, source = get_tle_enhanced(sat_id)
        if line1:
            user_data[chat_id]['satellites'][sat_id] = {'tle': (line1, line2, name), 'custom_gs': None, 'custom_remind': None}
            await update.message.reply_text(f"✅ Success! Target acquired: <b>{name}</b> ({sat_id})", parse_mode='HTML')
            await schedule_pass_alerts(chat_id, sat_id, context)
            await send_pass_schedule(chat_id, sat_id, context)
        else:
            await update.message.reply_text(f"❌ Error: Could not find TLE data for NORAD ID {sat_id}.")
            
    await update.message.reply_text("🌐 Satellite(s) added and alerts are set!")

async def cmd_constellation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active_chats.add(chat_id)
    init_user(chat_id)

    sat_ids = ['39030', '56178', '41875']

    await update.message.reply_text("🇹🇷 Initializing Turkish Constellation tracking (GÖKTÜRK-2, İMECE, GÖKTÜRK-1A)...")

    for sid in sat_ids:
        if sid in user_data[chat_id]['satellites']:
            await update.message.reply_text(f"ℹ️ Satellite {sid} is already in your fleet.")
            continue

        line1, line2, name, source = get_tle_enhanced(sid)
        if line1:
            user_data[chat_id]['satellites'][sid] = {'tle': (line1, line2, name), 'custom_gs': None, 'custom_remind': None}
            await update.message.reply_text(f"✅ Success! Target acquired: <b>{name}</b> ({sid})", parse_mode='HTML')
            await schedule_pass_alerts(chat_id, sid, context)
            await send_pass_schedule(chat_id, sid, context)
        else:
            await update.message.reply_text(f"❌ Error: Could not find TLE data for NORAD ID {sid}.")
            
    await update.message.reply_text("🌐 Turkish Constellation added and alerts are set!")

async def cmd_listsatellites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active_chats.add(chat_id)
    init_user(chat_id)
    
    satellites = user_data[chat_id]['satellites']
    if not satellites:
        await update.message.reply_text("You are not tracking any satellites right now.")
        return
        
    msg = "🛰️ <b>Currently Tracked Satellites:</b>\n\n"
    for sid, info in satellites.items():
        name = info['tle'][2]
        gs_name = info['custom_gs']['name'] if info.get('custom_gs') else user_data[chat_id]['global_gs']['name']
        remind = info.get('custom_remind') if info.get('custom_remind') is not None else user_data[chat_id]['remind_time']
        msg += f"• <b>{name}</b> (ID: {sid})\n  📍 Ground Station: {gs_name}\n  ⏱️ Alert: {remind} mins before AOS\n\n"
        
    await update.message.reply_text(msg, parse_mode='HTML')

async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active_chats.add(chat_id)
    args = context.args
    init_user(chat_id)
    
    scheduler = context.application.job_queue.scheduler

    if not args:
        user_data[chat_id]['satellites'] = {}
        for job in scheduler.get_jobs():
            if job.id.startswith(f"{chat_id}_"):
                job.remove()
        await update.message.reply_text("🛑 Stopped all tracking. All alarms have been canceled.")
    else:
        sat_id = args[0]
        if sat_id in user_data[chat_id]['satellites']:
            del user_data[chat_id]['satellites'][sat_id]
            for job in scheduler.get_jobs():
                if job.id.startswith(f"{chat_id}_{sat_id}_"):
                    job.remove()
            await update.message.reply_text(f"🛑 Stopped tracking satellite {sat_id}.")
        else:
            await update.message.reply_text(f"⚠️ Satellite {sat_id} is not in your tracking list.")

async def set_remindtime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active_chats.add(chat_id)
    args = context.args
    init_user(chat_id)

    if not args:
        await update.message.reply_text("⚠️ Usage:\n/remindtime <minutes>\n/remindtime <NORAD_ID> <minutes>\nExample: /remindtime 15 OR /remindtime 39030 5")
        return

    if len(args) == 1:
        if not args[0].isdigit():
            await update.message.reply_text("⚠️ Usage:\n/remindtime <minutes>\nExample: /remindtime 15")
            return
            
        mins = int(args[0])
        user_data[chat_id]['remind_time'] = mins
        await update.message.reply_text(f"✅ <b>Global Settings Updated!</b>\nAlerts: <b>{mins} min</b> before AOS for all tracked satellites.", parse_mode='HTML')
        
        for sat_id in user_data[chat_id]['satellites'].keys():
            await schedule_pass_alerts(chat_id, sat_id, context)

    elif len(args) == 2:
        sat_id = args[0]
        if not args[1].isdigit():
            await update.message.reply_text("⚠️ Usage:\n/remindtime <NORAD_ID> <minutes>\nExample: /remindtime 39030 5")
            return
            
        mins = int(args[1])
        if sat_id in user_data[chat_id]['satellites']:
            user_data[chat_id]['satellites'][sat_id]['custom_remind'] = mins
            sat_name = user_data[chat_id]['satellites'][sat_id]['tle'][2]
            await update.message.reply_text(f"✅ <b>Specific Setting Updated!</b>\nAlerts for <b>{sat_name}</b> ({sat_id}): <b>{mins} min</b> before AOS.", parse_mode='HTML')
            await schedule_pass_alerts(chat_id, sat_id, context)
        else:
            await update.message.reply_text(f"❌ Error: Satellite {sat_id} is not currently being tracked. Track it first.")
            
    else:
        await update.message.reply_text("⚠️ Usage:\n/remindtime <minutes>\n/remindtime <NORAD_ID> <minutes>\nExample: /remindtime 15 OR /remindtime 39030 5")

async def set_minelevation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active_chats.add(chat_id)
    args = context.args
    init_user(chat_id)

    if not args:
        await update.message.reply_text("⚠️ Usage: /minelevation <degrees>\nExample: /minelevation 10")
        return

    try:
        min_el = float(args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Error: Please enter a valid number (e.g., 10 or 15.5).\nExample: /minelevation 10")
        return

    user_data[chat_id]['min_elevation'] = min_el
    await update.message.reply_text(f"✅ <b>Filter Updated!</b>\nI will now ONLY alert you for passes where the maximum elevation is <b>{min_el}° or higher.</b>", parse_mode='HTML')
    
    for sat_id in user_data[chat_id]['satellites'].keys():
        await schedule_pass_alerts(chat_id, sat_id, context)
        await send_pass_schedule(chat_id, sat_id, context)

async def update_tle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active_chats.add(chat_id)
    init_user(chat_id)
    
    if not user_data[chat_id]['satellites']:
        await update.message.reply_text("⚠️ No satellites are currently being tracked.")
        return

    await update.message.reply_text("📡 <b>Safe Update Initiated!</b>\nRefreshing all tracked satellites...", parse_mode='HTML')
    
    updated_sats = []
    failed_sats = []
    
    for sat_id in list(user_data[chat_id]['satellites'].keys()):
        old_name = user_data[chat_id]['satellites'][sat_id]['tle'][2]
        line1, line2, name, source = get_tle_enhanced(sat_id)
        
        if line1:
            sat_obj = EarthSatellite(line1, line2, name, ts)
            epoch_dt = sat_obj.epoch.utc_datetime().replace(tzinfo=pytz.utc).astimezone(TURKEY_TZ)
            epoch_str = epoch_dt.strftime('%d %b %H:%M')
            
            user_data[chat_id]['satellites'][sat_id]['tle'] = (line1, line2, name)
            
            status_line = f"✅ {name}: Fetched from <b>{source}</b>\n   └ <i>Data Epoch: {epoch_str}</i>"
            updated_sats.append(status_line)
        else:
            failed_sats.append(f"⚠️ {old_name}: All sources failed. Using cached data.")
            
        await schedule_pass_alerts(chat_id, sat_id, context)
        await send_pass_schedule(chat_id, sat_id, context)
            
    report_msg = "<b>Update Complete!</b>\n\n"
    if updated_sats:
        report_msg += "\n".join(updated_sats) + "\n\n"
    if failed_sats:
        report_msg += "\n".join(failed_sats) + "\n"
        
    await update.message.reply_text(report_msg, parse_mode='HTML')

def main():
    token = os.environ.get("TELEGRAM_TOKEN")
    application = Application.builder().token(token).build()

    async def shutdown_notice(app: Application):
        for chat_id in active_chats:
            try:
                await app.bot.send_message(
                    chat_id=chat_id, 
                    text="🔄 <b>System Update in Progress</b>\nI am restarting for a version update. My memory will be cleared. Please wait 1 minute and re-establish your fleet.",
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.error(f"Kapanış mesajı gönderilemedi: {e}")

    application.post_stop = shutdown_notice

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("info", cmd_info))
    application.add_handler(CommandHandler("viewsat", cmd_viewsat)) # <--- YENİ KOMUT EKLENDİ
    application.add_handler(CommandHandler("groundstation", set_groundstation))
    application.add_handler(CommandHandler("satellite", set_satellite))
    application.add_handler(CommandHandler("constellation", cmd_constellation))
    application.add_handler(CommandHandler("listsatellites", cmd_listsatellites))
    application.add_handler(CommandHandler("removesatellite", cmd_stop)) 
    application.add_handler(CommandHandler("stop", cmd_stop))
    application.add_handler(CommandHandler("remindtime", set_remindtime))
    application.add_handler(CommandHandler("minelevation", set_minelevation))
    application.add_handler(CommandHandler("tleupdate", update_tle))

    job_queue = application.job_queue
    midnight_trt = datetime.strptime('00:00:00', '%H:%M:%S').time().replace(tzinfo=TURKEY_TZ)
    job_queue.run_daily(auto_daily_tle_update, time=midnight_trt)

    # ==============================================================
    # --- BOT BAŞLAMADAN ÖNCE WEB SUNUCUSUNU (API) BAŞLAT ---
    # ==============================================================
    api_thread = threading.Thread(target=run_api, daemon=True)
    api_thread.start()

    # Botu çalıştır
    application.run_polling()

if __name__ == '__main__':
    main()