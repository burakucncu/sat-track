# 🛰️ Satellite Tracker & Constellation Bot

A high-precision, 24/7 autonomous Telegram bot designed for tracking multiple satellites simultaneously. Built with Python and the **Skyfield/SGP4** propagation models, it acts as a personal fleet command center, providing real-time orbital event notifications with scientific accuracy.

## ✨ Key Features

* **Multi-Satellite Fleet Tracking:** Monitor multiple satellites at the same time using the `/constellation` command without alarms overwriting each other.
* **Scientific Accuracy:** Leverages the `skyfield` library to calculate exact Acquisition of Signal (AOS), Time of Closest Approach (TCA), and Loss of Signal (LOS).
* **Target-Specific Configurations:** * Assign a **Custom Ground Station** to a specific satellite (e.g., track one satellite from Ankara and another from SvalSat).
  * Set **Custom Warning Times** for individual satellites.
* **Smart Elevation Filter:** Avoid "junk" passes. Use `/minelevation` to only receive alerts for high-quality passes above your chosen horizon threshold.
* **Update Awareness:** The bot intelligently remembers active users and sends a **"System Update"** notification before restarting, ensuring continuous tracking awareness.
* **Auto-Maintenance:** Daily TLE refreshes at 03:00 AM to maintain sub-second precision and prevent orbital drift.

## 📱 Telegram Commands

* `/start` - Initializes the session and sets the default station (TUBITAK UZAY ANKARA).
* `/info` - Displays the complete list of available commands and usage examples.
* `/satellite <NORAD_ID>` - Tracks a single satellite (clears others).
* `/constellation <ID1> <ID2> ...` - Tracks multiple satellites at once. Use `/constellation default` for the default TUBITAK fleet (39030, 56178, 41875).
* `/listsatellites` - Views all currently tracked satellites and their specific configurations.
* `/groundstation <lat> <lon> <alt>` - Sets the global ground station for all satellites.
* `/groundstation <NORAD_ID> <lat> <lon> <alt>` - Sets a custom ground station for a specific satellite.
* `/remindtime <minutes>` - Sets the global early warning time.
* `/remindtime <NORAD_ID> <minutes>` - Sets the warning time for a specific satellite.
* `/minelevation <degrees>` - Filters out passes that don't reach a certain peak altitude.
* `/tleupdate` - Manually forces a refresh of orbital elements from CelesTrak.
* `/stop` - Stops all tracking and cancels all alarms.
* `/stop <NORAD_ID>` - Stops tracking a specific satellite.

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
    Create a `.env` file and add your Telegram bot token:
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
6.  **Deploy:** The bot will run 24/7 and survive restarts with its automated notification system.

---
*Developed for orbital mechanics tracking and satellite constellation automation.*