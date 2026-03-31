import os
import logging
import requests
from datetime import datetime, timedelta
import pytz
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from skyfield.api import Topos, load, EarthSatellite
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# O an botu kullananları aklında tutması için geçici bir küme
active_chats = set()

# Loglama ayarları
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Zaman Dilimi
TURKEY_TZ = pytz.timezone('Europe/Istanbul')

# Bellek (Veritabanı niyetine)
user_data = {}
# Format: { chat_id: { 'gs': {'lat': 39.89110, 'lon': 32.77870, 'alt': 925, 'name': 'Tübitak Uzay Ankara'}, 'sat_id': '25544', 'remind_time': 10, 'min_elevation': 0, 'tle': (line1, line2, name) } }

# Skyfield Zaman Ölçeği
ts = load.timescale()

# --- YARDIMCI FONKSİYONLAR ---

def get_tle_from_celestrak(norad_id):
    """CelesTrak'tan güncel TLE verisini sağlam bir şekilde çeker"""
    url = f'https://celestrak.org/NORAD/elements/gp.php?CATNR={norad_id}&FORMAT=tle'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        lines = response.text.strip().split('\n')
        if len(lines) >= 3:
            name = lines[0].strip()
            line1 = lines[1].strip()
            line2 = lines[2].strip()
            return line1, line2, name
    except Exception as e:
        logger.error(f"TLE Çekme Hatası: {e}")
        
    return None, None, None

def calculate_passes(chat_id):
    """Skyfield ile geçişleri hesaplar ve SADECE filtreyi geçenleri döndürür"""
    data = user_data.get(chat_id)
    if not data or 'tle' not in data:
        return []

    line1, line2, sat_name = data['tle']
    sat = EarthSatellite(line1, line2, sat_name, ts)
    
    gs = data['gs']
    station = Topos(latitude_degrees=gs['lat'], longitude_degrees=gs['lon'], elevation_m=gs['alt'])
    
    # Kullanıcının belirlediği minimum irtifa filtresi (Varsayılan: 0)
    min_el_threshold = data.get('min_elevation', 0)

    # Şu andan itibaren 7 günlük hesaplama
    t0 = ts.now()
    t1 = ts.utc(t0.utc_datetime() + timedelta(days=7))
    
    # Geçiṣleri bul (Elevation > 0 derece olanlar AOS ve LOS'tur)
    t, events = sat.find_events(station, t0, t1, altitude_degrees=0.0)
    
    passes = []
    current_pass = {}
    
    for ti, event in zip(t, events):
        event_time = ti.utc_datetime().replace(tzinfo=pytz.utc).astimezone(TURKEY_TZ)
        if event == 0: # AOS (Ufuk çizgisinin üstüne çıktı)
            current_pass['aos'] = event_time
            current_pass['tca'] = None
            current_pass['los'] = None
        elif event == 1 and 'aos' in current_pass: # TCA (En yüksek açı)
            current_pass['tca'] = event_time
            
            # Max Elevation değerini hesapla
            difference = sat - station
            topocentric = difference.at(ti)
            alt, az, distance = topocentric.altaz()
            current_pass['max_el'] = alt.degrees
            
        elif event == 2 and 'aos' in current_pass: # LOS (Ufuk çizgisinin altına indi)
            current_pass['los'] = event_time
            
            # FİLTRELEME: Eğer bu geçişin en yüksek noktası kullanıcının istediği dereceden büyükse listeye ekle
            if current_pass.get('max_el', 0) >= min_el_threshold:
                passes.append(current_pass)
                
            current_pass = {}
            
    return passes

async def schedule_pass_alerts(chat_id, context: ContextTypes.DEFAULT_TYPE):
    """Geçişler için alarm (job) kurar"""
    data = user_data.get(chat_id)
    if not data:
        return

    scheduler = context.application.job_queue.scheduler
    
    # Önce bu chat_id için eski alarmları temizle
    for job in scheduler.get_jobs():
        if job.id.startswith(str(chat_id)):
            job.remove()

    passes = calculate_passes(chat_id)
    
    if not passes:
        # Eğer filtre yüzünden hiç geçiş kalmadıysa kullanıcıya bilgi ver
        await context.bot.send_message(chat_id=chat_id, text=f"⚠️ No passes found above your {data.get('min_elevation', 0)}° elevation filter for the next 7 days.")
        return

    sat_name = data['tle'][2]
    gs_name = data['gs']['name']
    remind_mins = data['remind_time']
    now = datetime.now(TURKEY_TZ)

    for i, p in enumerate(passes):
        aos = p.get('aos')
        tca = p.get('tca')
        los = p.get('los')
        max_el = p.get('max_el', 0)
        
        if not aos or not tca or not los:
            continue

        # Tarih formatlamaları
        aos_str = aos.strftime('%H:%M:%S')
        tca_str = tca.strftime('%H:%M:%S')
        los_str = los.strftime('%H:%M:%S')
        
        duration = los - aos
        dur_m, dur_s = divmod(duration.total_seconds(), 60)
        
        tca_dur = tca - aos
        tca_m, tca_s = divmod(tca_dur.total_seconds(), 60)

        # 1. Hatırlatma Alarmı (Initial Warning)
        warning_time = aos - timedelta(minutes=remind_mins)
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
            scheduler.add_job(send_telegram_msg, 'date', run_date=warning_time, args=[chat_id, msg, context], id=f"{chat_id}_warn_{aos.timestamp()}")

        # 2. AOS Alarmı (Geçiş Başladı)
        if aos > now:
            msg = (
                f"🟢 <b>AOS: {sat_name} is now in the footprint of {gs_name}!</b>\n\n"
                f"⏱️ <b>Timeline:</b>\n"
                f"• AOS: <b>{aos_str}</b>\n"
                f"• Max Elevation Time (TCA): <b>{tca_str}</b> <i>(Elevation: {max_el:.1f}°)</i>\n"
                f"• LOS: <b>{los_str}</b>\n\n"
                f"Total pass duration: <b>{int(dur_m)}m {int(dur_s)}s</b>"
            )
            scheduler.add_job(send_telegram_msg, 'date', run_date=aos, args=[chat_id, msg, context], id=f"{chat_id}_aos_{aos.timestamp()}")

        # 3. TCA Alarmı (En Yüksek İrtifa)
        if tca > now:
            msg = (
                f"⭐ <b>TCA: {sat_name} is currently at its highest point!</b>\n"
                f"Current Elevation: <b>{max_el:.1f}°</b>\n"
                f"Pass will end in {int(dur_m - tca_m)}m {int(dur_s - tca_s)}s."
            )
            scheduler.add_job(send_telegram_msg, 'date', run_date=tca, args=[chat_id, msg, context], id=f"{chat_id}_tca_{aos.timestamp()}")

        # 4. LOS Alarmı (Geçiş Bitti ve Sonraki Geçiş Bilgisi)
        if los > now:
            msg = f"🔴 <b>LOS: {sat_name} has completed its pass over {gs_name}.</b>\nSatellite is out of the footprint."
            
            # Sonraki geçiş bilgisini ekle
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
            else:
                msg += f"\n\n📅 <b>NEXT PASS:</b> No more passes scheduled in the current window."

            scheduler.add_job(send_telegram_msg, 'date', run_date=los, args=[chat_id, msg, context], id=f"{chat_id}_los_{aos.timestamp()}")

async def send_telegram_msg(chat_id, text, context: ContextTypes.DEFAULT_TYPE):
    """Job Queue tarafından çağrılan mesaj gönderme fonksiyonu"""
    try:
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Mesaj gonderilemedi {chat_id}: {e}")

async def auto_daily_tle_update(context: ContextTypes.DEFAULT_TYPE):
    """Her gün gece yarısı çalışan TLE güncelleme servisi"""
    for chat_id, data in user_data.items():
        if 'sat_id' in data:
            sat_id = data['sat_id']
            line1, line2, name = get_tle_from_celestrak(sat_id)
            if line1:
                user_data[chat_id]['tle'] = (line1, line2, name)
                await schedule_pass_alerts(chat_id, context)
                await context.bot.send_message(chat_id=chat_id, text="🔄 <b>Daily Maintenance:</b> TLEs refreshed successfully.", parse_mode='HTML')

# --- TELEGRAM KOMUTLARI ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active_chats.add(chat_id)
    user_data[chat_id] = {
        'gs': {'lat': 39.89110, 'lon': 32.77870, 'alt': 925, 'name': 'Tübitak Uzay Ankara'},
        'remind_time': 10,
        'min_elevation': 0 # Varsayılan olarak tüm geçişleri gösterir
    }
    await update.message.reply_text("🛰️ Welcome to Satellite Tracker Bot!\n\nDefault Station: Tübitak Uzay Ankara\nUse /satellite <NORAD_ID> to start tracking.")

async def set_groundstation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active_chats.add(chat_id)
    args = context.args

    if chat_id not in user_data:
        user_data[chat_id] = {'remind_time': 10, 'min_elevation': 0}

    if not args or args[0].lower() == 'default':
        user_data[chat_id]['gs'] = {'lat': 39.89110, 'lon': 32.77870, 'alt': 925, 'name': 'Tübitak Uzay Ankara'}
        await update.message.reply_text("📍 Station reset to: <b>Tübitak Uzay Ankara</b>", parse_mode='HTML')
    elif len(args) >= 3:
        try:
            lat = float(args[0])
            lon = float(args[1])
            alt = float(args[2])
            user_data[chat_id]['gs'] = {'lat': lat, 'lon': lon, 'alt': alt, 'name': f'Custom Station ({lat:.2f}, {lon:.2f})'}
            await update.message.reply_text(f"📍 Station updated: <b>{lat}, {lon} (Alt: {alt}m)</b>", parse_mode='HTML')
        except ValueError:
            await update.message.reply_text("⚠️ Error: Coordinates must be numbers. Example: /groundstation 39.89 32.77 925")
    else:
        await update.message.reply_text("⚠️ Usage:\n/groundstation default\n/groundstation <lat> <lon> <alt>")
        return

    if 'tle' in user_data[chat_id]:
        await schedule_pass_alerts(chat_id, context)

async def set_satellite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active_chats.add(chat_id)
    args = context.args

    if not args:
        await update.message.reply_text("⚠️ Usage: /satellite <NORAD_ID>\nExample: /satellite 25544")
        return

    sat_id = args[0]
    await update.message.reply_text(f"📡 Downloading orbital data for NORAD ID: {sat_id}...")

    line1, line2, name = get_tle_from_celestrak(sat_id)
    if line1:
        if chat_id not in user_data:
            user_data[chat_id] = {'gs': {'lat': 39.89110, 'lon': 32.77870, 'alt': 925, 'name': 'Tübitak Uzay Ankara'}, 'remind_time': 10, 'min_elevation': 0}
        
        user_data[chat_id]['sat_id'] = sat_id
        user_data[chat_id]['tle'] = (line1, line2, name)
        
        await update.message.reply_text(f"✅ Success! Target acquired: <b>{name}</b>\nCalculating passes...", parse_mode='HTML')
        await schedule_pass_alerts(chat_id, context)
    else:
        await update.message.reply_text("❌ Error: Could not find TLE data for that NORAD ID.")

async def set_remindtime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active_chats.add(chat_id)
    args = context.args

    if not args or not args[0].isdigit():
        await update.message.reply_text("⚠️ Usage: /remindtime <minutes>\nExample: /remindtime 15")
        return

    mins = int(args[0])
    if chat_id not in user_data:
        user_data[chat_id] = {'gs': {'lat': 39.89110, 'lon': 32.77870, 'alt': 925, 'name': 'Tübitak Uzay Ankara'}, 'min_elevation': 0}
    
    user_data[chat_id]['remind_time'] = mins
    await update.message.reply_text(f"✅ <b>Settings Updated!</b>\nAlerts: <b>{mins} min</b> before AOS.", parse_mode='HTML')
    
    if 'tle' in user_data[chat_id]:
        await schedule_pass_alerts(chat_id, context)

async def set_minelevation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Yeni Komut: Minimum Elevasyon Filtresi"""
    chat_id = update.effective_chat.id
    active_chats.add(chat_id)
    args = context.args

    if not args:
        await update.message.reply_text("⚠️ Usage: /minelevation <degrees>\nExample: /minelevation 10\nThis sets the minimum maximum elevation required to receive an alert.")
        return

    try:
        min_el = float(args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Error: Please enter a valid number (e.g., 10 or 15.5).")
        return

    if chat_id not in user_data:
        user_data[chat_id] = {'gs': {'lat': 39.89110, 'lon': 32.77870, 'alt': 925, 'name': 'Tübitak Uzay Ankara'}, 'remind_time': 10}
    
    user_data[chat_id]['min_elevation'] = min_el
    await update.message.reply_text(f"✅ <b>Filter Updated!</b>\nI will now ONLY alert you for passes where the maximum elevation is <b>{min_el}° or higher.</b>", parse_mode='HTML')
    
    if 'tle' in user_data[chat_id]:
        await schedule_pass_alerts(chat_id, context)

async def update_tle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    active_chats.add(chat_id)
    
    if chat_id not in user_data or 'sat_id' not in user_data[chat_id]:
        await update.message.reply_text("⚠️ No satellite is currently being tracked.")
        return

    await update.message.reply_text("📡 <b>Safe Update Initiated!</b>\nTesting connection...", parse_mode='HTML')
    
    sat_id = user_data[chat_id]['sat_id']
    line1, line2, name = get_tle_from_celestrak(sat_id)
    
    if line1:
        user_data[chat_id]['tle'] = (line1, line2, name)
        await update.message.reply_text("✅ <b>Success!</b> New data verified.", parse_mode='HTML')
        await schedule_pass_alerts(chat_id, context)
    else:
        await update.message.reply_text("❌ <b>Update Failed!</b>", parse_mode='HTML')


def main():
    token = os.environ.get("TELEGRAM_TOKEN")
    application = Application.builder().token(token).build()

    # --- Kapanış Mesajı Özelliği ---
    async def shutdown_notice(app: Application):
        for chat_id in active_chats:
            try:
                # Kapanmadan hemen önce mesaj fırlatıyoruz
                await app.bot.send_message(
                    chat_id=chat_id, 
                    text="🔄 <b>System Update in Progress</b>\nI am restarting for a version update. My memory will be cleared. Please re-send your satellite ID in 1 minute.",
                    parse_mode='HTML'
                )
            except Exception as e:
                logger.error(f"Kapanış mesajı gönderilemedi: {e}")

    # Bu komut Render botu durdururken (SIGTERM geldiğinde) çalışır
    application.post_stop = shutdown_notice

    # Komutları kaydet
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("groundstation", set_groundstation))
    application.add_handler(CommandHandler("satellite", set_satellite))
    application.add_handler(CommandHandler("remindtime", set_remindtime))
    application.add_handler(CommandHandler("minelevation", set_minelevation))
    application.add_handler(CommandHandler("tleupdate", update_tle))

    # Günlük otomatik bakım
    job_queue = application.job_queue
    job_queue.run_daily(auto_daily_tle_update, time=datetime.strptime('03:00:00', '%H:%M:%S').time())

    application.run_polling()

if __name__ == '__main__':
    main()