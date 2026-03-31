# 🛰️ Satellite Tracker Telegram Bot

A high-precision, 24/7 autonomous Telegram bot designed for satellite tracking and pass predictions. Built with Python and the **Skyfield/SGP4** propagation models, it provides real-time notifications for orbital events with scientific accuracy.

## ✨ Key Features

* **Scientific Accuracy:** Leverages the `skyfield` library to calculate exact Acquisition of Signal (AOS), Time of Closest Approach (TCA), and Loss of Signal (LOS) based on the latest TLE data.
* **Default Target - Göktürk-2:** Pre-configured for **Göktürk-2 (NORAD ID: 39030)**, Turkey's high-resolution earth observation satellite.
* **Smart Elevation Filter:** Avoid "junk" passes. Use `/minelevation` to only receive alerts for high-quality passes above your chosen horizon threshold.
* **Named Ground Stations:** Support for custom observer locations with labels (e.g., "Home", "Campus", "Tübitak Uzay").
* **Update Awareness:** The bot intelligently remembers active users and sends a **"System Update"** notification before restarting, ensuring you know exactly when to re-sync your tracking.
* **Auto-Maintenance:** Daily TLE refreshes at 03:00 AM Europe/Istanbul time to maintain sub-second precision.

## 📱 Telegram Commands

* `/start` - Initializes the session and sets the default station (Tübitak Uzay Ankara).
* `/satellite <NORAD_ID>` - Targets a specific satellite (e.g., `/satellite 39030`).
* `/groundstation <name> <lat> <lon> <alt>` - Sets a custom labeled location (e.g., `/groundstation My_Office 39.89 32.77 925`).
* `/groundstation default` - Resets to the default Ankara station.
* `/remindtime <minutes>` - Sets how early you want the first warning (Default: 10m).
* `/minelevation <degrees>` - Filters out passes that don't reach a certain peak altitude.
* `/tleupdate` - Manually forces a refresh of orbital elements from CelesTrak.

## 🚀 Local Installation

1.  **Clone the repository:**
    ```bash
    git clone [https://github.com/yourusername/sat-track.git](https://github.com/yourusername/sat-track.git)
    cd sat-track
    ```

2.  **Set up environment:**
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```

3.  **Configure `.env`:**
    Create a `.env` file and add your token:
    ```text
    TELEGRAM_TOKEN=your_api_key_here
    ```

4.  **Run:**
    ```bash
    python bot.py
    ```

## ☁️ Deployment (Render.com)

1.  Create a **Background Worker** on Render.
2.  Connect your GitHub repository.
3.  **Build Command:** `pip install -r requirements.txt`
4.  **Start Command:** `python bot.py`
5.  **Environment Variables:** Add `TELEGRAM_TOKEN` with your bot's token.
6.  **Deploy:** The bot will run 24/7 and survive restarts with its notification system.

---
*Developed for Geomatics Engineering applications and satellite observation automation.*